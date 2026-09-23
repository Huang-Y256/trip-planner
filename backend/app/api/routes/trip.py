"""基于 LangGraph 的旅行规划 API 路由（含 SSE 流式接口）。"""

from __future__ import annotations

import json
import logging
import queue
from typing import Any
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ...models.schemas import TripRequest, TripPlan, TripPlanResponse
from ...graph.builder import get_trip_planning_graph
from ...graph.state import TripPlanningState
from ...graph.nodes import MAX_RETRIES
from ...services.trip_store import save_plan, get_plan, update_plan, list_plans

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trip", tags=["旅行规划"])

# 各节点对应的用户可读提示文本
NODE_MESSAGES = {
    "extract_prefs": "🔍 正在分析您的偏好...",
    "parallel_fetch": "📡 正在并行获取景点、天气和酒店信息...",
    "planner": "📋 AI 正在生成行程计划...",
    "validator": "✅ 正在校验计划数据...",
    "replanner": "🔄 校验未通过，正在修正...",
    "fallback": "⚠️ 正在生成降级占位计划...",
}

# SSE 心跳间隔应小于 Railway/反向代理的空闲连接超时时间
SSE_HEARTBEAT_SECONDS = 15


@router.post(
    "/plan",
    response_model=TripPlanResponse,
    summary="生成旅行计划",
    description="基于 LangGraph 多节点状态机编排的多智能体旅行规划",
)
def plan_trip(request: TripRequest):
    """通过 LangGraph 流水线生成旅行计划。

    注意：此路由刻意不使用 async，因为 LangGraph 图调用是同步的
    （LLM 调用 + MCP 子进程）。FastAPI 会自动将其放入默认线程池执行。
    """
    graph = get_trip_planning_graph()

    initial_state: TripPlanningState = {
        "city": request.city,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "travel_days": request.travel_days,
        "transportation": request.transportation,
        "accommodation": request.accommodation,
        "preferences": request.preferences,
        "free_text_input": request.free_text_input or "",
        "attraction_data": "",
        "weather_data": "",
        "hotel_data": "",
        "draft_plan": None,
        "validated_plan": None,
        "validation_errors": [],
        "retry_count": 0,
        "max_retries": MAX_RETRIES,
        "status": "init",
        "warnings": [],
    }

    try:
        logger.info("调用 LangGraph 旅行规划流水线，目的地: %s", request.city)
        final_state = graph.invoke(initial_state)
    except Exception:
        logger.exception("LangGraph 流水线调用失败")
        raise HTTPException(
            status_code=503,
            detail="旅行规划服务暂时不可用，请稍后重试",
        )

    plan_data = final_state.get("validated_plan")
    if not plan_data:
        raise HTTPException(
            status_code=503,
            detail="AI 规划服务暂时不可用，请稍后重试",
        )

    status = final_state.get("status", "unknown")
    warnings = final_state.get("warnings", [])

    if status == "fallback":
        message = "AI 规划服务暂时不可用，返回降级行程"
    elif status == "validated":
        message = "旅行计划生成成功"
    else:
        message = f"旅行计划生成完成 (状态: {status})"

    # 将 dict 转换为 TripPlan Pydantic 模型以进行响应校验
    try:
        trip_plan = TripPlan(**plan_data)
    except Exception:
        logger.exception("校验后的计划解析为 TripPlan 失败")
        raise HTTPException(
            status_code=500,
            detail="生成计划的数据格式异常",
        )

    # 持久化到 SQLite，返回 plan_id
    plan_id = save_plan(plan_data, status=status)

    return TripPlanResponse(
        success=True,
        message=message,
        plan_id=plan_id,
        data=trip_plan,
    )


@router.get("/plan/{plan_id}", summary="根据 plan_id 获取旅行计划")
def get_trip_plan(plan_id: str):
    """前端通过 URL 中的 plan_id 加载已保存的计划。"""
    stored = get_plan(plan_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="旅行计划不存在")

    plan_data = stored["plan"]
    try:
        trip_plan = TripPlan(**plan_data)
    except Exception:
        raise HTTPException(status_code=500, detail="存储的计划数据格式异常")

    return TripPlanResponse(
        success=True,
        message="获取旅行计划成功",
        plan_id=plan_id,
        data=trip_plan,
    )


@router.put("/plan/{plan_id}", summary="更新旅行计划（编辑模式保存）")
def update_trip_plan(plan_id: str, plan: TripPlan):
    """前端编辑行程后调用此接口保存修改。"""
    if get_plan(plan_id) is None:
        raise HTTPException(status_code=404, detail="旅行计划不存在")

    plan_data = plan.model_dump()
    updated = update_plan(plan_id, plan_data)
    if not updated:
        raise HTTPException(status_code=500, detail="更新失败")

    return TripPlanResponse(success=True, message="更新成功", plan_id=plan_id, data=plan)


@router.get("/plans", summary="列出最近的旅行计划")
def list_trip_plans(limit: int = 20):
    """返回最近生成的计划摘要列表（用于历史记录）。"""
    plans = list_plans(limit)
    return {"success": True, "data": plans}


def _build_initial_state(request: TripRequest) -> TripPlanningState:
    """从请求参数构建 LangGraph 初始状态。"""
    return {
        "city": request.city,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "travel_days": request.travel_days,
        "transportation": request.transportation,
        "accommodation": request.accommodation,
        "preferences": request.preferences,
        "free_text_input": request.free_text_input or "",
        "attraction_data": "",
        "weather_data": "",
        "hotel_data": "",
        "draft_plan": None,
        "validated_plan": None,
        "validation_errors": [],
        "retry_count": 0,
        "max_retries": MAX_RETRIES,
        "status": "init",
        "warnings": [],
    }


@router.post(
    "/plan/stream",
    summary="流式生成旅行计划（SSE）",
    description="通过 Server-Sent Events 实时推送 LangGraph 各节点的执行进度，"
                "完成后返回完整旅行计划。",
)
def plan_trip_stream(request: TripRequest):
    """SSE 流式接口：实时推送每个 LangGraph 节点的执行状态。

    事件格式（Server-Sent Events）:
        data: {"node": "...", "message": "...", "status": "...", "done": false}

    最终事件:
        data: {"node": "done", "data": {"trip_plan": {...}}, "done": true}
        或
        data: {"node": "error", "message": "...", "done": true}
    """
    graph = get_trip_planning_graph()
    initial_state = _build_initial_state(request)

    # LangGraph 的节点同步执行期间无法主动产生事件；
    # 用独立线程运行图，并借助队列让 SSE 生成器定期发送心跳。
    graph_events: queue.Queue = queue.Queue()

    def run_graph() -> None:
        """在线程中执行规划图，避免阻塞 SSE 心跳输出。"""
        try:
            for graph_event in graph.stream(initial_state):
                graph_events.put(graph_event)
        except Exception as exc:
            graph_events.put(exc)
        finally:
            graph_events.put(None)

    def event_generator():
        accumulated: dict[str, Any] = {}
        final_plan = None
        final_status = ""

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="trip-graph-stream") as executor:
            executor.submit(run_graph)

            try:
                while True:
                    try:
                        item = graph_events.get(timeout=SSE_HEARTBEAT_SECONDS)
                    except queue.Empty:
                        # SSE 注释行不进入前端业务解析，仅用于保持连接活跃
                        yield ": keep-alive\n\n"
                        continue

                    if item is None:
                        break

                    if isinstance(item, Exception):
                        raise item

                    event = item
                    node_name = list(event.keys())[0]
                    node_update = event[node_name]
                    accumulated.update(node_update)

                    message = NODE_MESSAGES.get(node_name, f"正在执行 {node_name}...")
                    sse_data: dict[str, Any] = {
                        "node": node_name,
                        "message": message,
                        "status": node_update.get("status", ""),
                        "done": False,
                    }

                    # 校验通过时，标记为最终结果
                    if node_update.get("status") == "validated" and node_update.get("validated_plan"):
                        final_plan = node_update["validated_plan"]
                        final_status = "validated"

                    # 降级方案也有结果
                    if node_update.get("status") == "fallback" and node_update.get("validated_plan"):
                        final_plan = node_update["validated_plan"]
                        final_status = "fallback"

                    yield f"data: {json.dumps(sse_data, ensure_ascii=False)}\n\n"

                # 流结束后发送最终结果
                if final_plan:
                    status_text = "旅行计划生成成功" if final_status == "validated" else "AI 规划服务暂时不可用，返回降级行程"
                    plan_id = save_plan(final_plan, status=final_status)
                    yield f"data: {json.dumps({'node': 'done', 'message': status_text, 'data': {'trip_plan': final_plan, 'status': final_status, 'plan_id': plan_id}, 'done': True}, ensure_ascii=False)}\n\n"
                else:
                    yield f"data: {json.dumps({'node': 'error', 'message': '生成失败，请稍后重试', 'done': True}, ensure_ascii=False)}\n\n"

            except Exception:
                logger.exception("SSE 流式生成异常")
                yield f"data: {json.dumps({'node': 'error', 'message': '服务暂时不可用，请稍后重试', 'done': True}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/health", summary="健康检查")
def health_check():
    from ...graph.tools import is_mcp_available
    from ...services.llm_service import get_llm

    mcp_ok = is_mcp_available()
    llm_ok = False
    try:
        get_llm()
        llm_ok = True
    except Exception:
        pass

    return {
        "status": "healthy" if (mcp_ok and llm_ok) else "degraded",
        "mcp_available": mcp_ok,
        "llm_available": llm_ok,
    }
