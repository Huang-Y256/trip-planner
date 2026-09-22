"""LangGraph 旅行规划流水线的节点函数。"""

from __future__ import annotations

import json
import re
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from ..services.llm_service import get_llm
from .state import TripPlanningState
from .tools import call_mcp_tool, is_mcp_available

logger = logging.getLogger(__name__)

MAX_RETRIES = 2


# ---------------------------------------------------------------------------
# 规划器节点的结构化输出 Schema
# ---------------------------------------------------------------------------

class AttractionSchema(BaseModel):
    name: str
    address: str
    location: dict = Field(
        default_factory=dict,
        description='{"longitude": 116.4, "latitude": 39.9}',
    )
    visit_duration: int = 120
    description: str = ""
    category: str = "景点"
    ticket_price: int = 0


class MealSchema(BaseModel):
    type: str
    name: str
    description: str = ""
    estimated_cost: int = 0


class HotelSchema(BaseModel):
    name: str
    address: str = ""
    price_range: str = ""
    rating: str = ""
    type: str = ""
    estimated_cost: int = 0


class DayPlanSchema(BaseModel):
    date: str
    day_index: int
    description: str = ""
    transportation: str = ""
    accommodation: str = ""
    hotel: HotelSchema | None = None
    attractions: list[AttractionSchema] = Field(default_factory=list)
    meals: list[MealSchema] = Field(default_factory=list)


class WeatherItemSchema(BaseModel):
    date: str
    day_weather: str = ""
    night_weather: str = ""
    day_temp: int = 0
    night_temp: int = 0
    wind_direction: str = ""
    wind_power: str = ""


class BudgetSchema(BaseModel):
    total_attractions: int = 0
    total_hotels: int = 0
    total_meals: int = 0
    total_transportation: int = 0
    total: int = 0


class TripPlanSchema(BaseModel):
    city: str
    start_date: str
    end_date: str
    days: list[DayPlanSchema]
    weather_info: list[WeatherItemSchema] = Field(default_factory=list)
    overall_suggestions: str = ""
    budget: BudgetSchema | None = None


# ---------------------------------------------------------------------------
# 节点：提取偏好并构建搜索策略
# ---------------------------------------------------------------------------

def extract_preferences(state: TripPlanningState) -> dict[str, Any]:
    """将用户偏好规范化为组合搜索关键词。"""
    prefs = state.get("preferences", [])
    if prefs:
        keyword = "|".join(prefs[:5])
    else:
        keyword = "景点"

    logger.info("Extracted search keyword: %s", keyword)
    return {"warnings": [f"搜索关键词: {keyword}"]}


# ---------------------------------------------------------------------------
# 节点：并行获取（景点 + 天气 + 酒店）
# ---------------------------------------------------------------------------

def _fetch_attractions(city: str, keyword: str) -> str:
    result = call_mcp_tool(
        tool_name="maps_text_search",
        arguments={"keywords": keyword, "city": city, "citylimit": "true"},
    )
    return result if result else "景点搜索结果不可用"


def _fetch_weather(city: str) -> str:
    result = call_mcp_tool(tool_name="maps_weather", arguments={"city": city})
    return result if result else "天气信息不可用"


def _fetch_hotels(city: str, accommodation: str) -> str:
    result = call_mcp_tool(
        tool_name="maps_text_search",
        arguments={"keywords": accommodation, "city": city, "citylimit": "true"},
    )
    return result if result else "酒店信息不可用"


def parallel_fetch(state: TripPlanningState) -> dict[str, Any]:
    """并发执行三个 MCP 工具查询。"""
    city = state["city"]
    prefs = state.get("preferences", [])
    keyword = "|".join(prefs[:5]) if prefs else "景点"
    accommodation = state.get("accommodation", "酒店")

    tasks = {
        "attraction_data": (_fetch_attractions, (city, keyword)),
        "weather_data": (_fetch_weather, (city,)),
        "hotel_data": (_fetch_hotels, (city, accommodation)),
    }

    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(fn, *args): key for key, (fn, args) in tasks.items()
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result(timeout=60)
            except Exception:
                logger.exception("MCP fetch failed for %s", key)
                results[key] = f"{key} 获取失败"

    logger.info("Parallel fetch complete: keys=%s", list(results.keys()))
    return results


# ---------------------------------------------------------------------------
# 节点：生成行程计划（LLM 结构化输出）
# ---------------------------------------------------------------------------

PLANNER_SYSTEM = """你是一个专业的旅行规划专家。根据提供的景点、天气和酒店信息，\
为用户生成一份详细的多日旅行计划。返回结果必须符合 TripPlanSchema 的字段结构。

要求:
1. 每天安排 2-3 个景点
2. 每天包含早/中/晚三餐
3. 每天推荐一个酒店
4. 景点坐标必须真实
5. 包含预算汇总
6. 温度为纯数字（不带 °C）"""


def plan_itinerary(state: TripPlanningState) -> dict[str, Any]:
    """使用 LLM 结构化输出生成经过校验的旅行计划。"""
    llm = get_llm()
    structured_llm = llm.with_structured_output(TripPlanSchema)

    prompt = f"""请为以下行程生成旅行计划:

**基本信息:**
- 城市: {state['city']}
- 日期: {state['start_date']} 至 {state['end_date']}
- 天数: {state['travel_days']}天
- 交通方式: {state['transportation']}
- 住宿: {state['accommodation']}
- 偏好: {', '.join(state['preferences']) if state['preferences'] else '无'}

**景点信息:**
{state.get('attraction_data', '无')}

**天气信息:**
{state.get('weather_data', '无')}

**酒店信息:**
{state.get('hotel_data', '无')}

**额外要求:**
{state.get('free_text_input') or '无'}
"""

    # 如果是校验失败后的重新规划，则追加错误上下文
    errors = state.get("validation_errors", [])
    if errors:
        prompt += f"\n\n**上一轮校验发现的问题（请在本次生成中修正）:**\n"
        for err in errors:
            prompt += f"- {err}\n"

    try:
        plan = structured_llm.invoke(prompt)
        plan_dict = plan.model_dump()
        return {
            "draft_plan": plan_dict,
            "retry_count": state.get("retry_count", 0) + 1,
            "status": "planned",
        }
    except Exception:
        # 工具调用失败（部分免费模型/第三方 API 不支持）→ 降级为纯文本 JSON 生成
        logger.warning("Structured output (tool calling) failed, trying plain JSON fallback")
        return _plan_with_json_fallback(state, prompt)


def _plan_with_json_fallback(state: TripPlanningState, prompt: str) -> dict[str, Any]:
    """降级方案：让 LLM 以纯文本 JSON 格式输出，然后手动解析。"""
    llm = get_llm()

    json_prompt = prompt + "\n\n请严格按照以下 JSON Schema 返回结果（不要包含其他文本，只返回 JSON）:\n"
    json_prompt += json.dumps(TripPlanSchema.model_json_schema(), ensure_ascii=False, indent=2)

    try:
        response = llm.invoke(json_prompt)
        raw_text = response.content if hasattr(response, "content") else str(response)

        # 提取 JSON（可能被 ```json 代码块包裹）
        plan_dict = _extract_json_from_text(raw_text)
        if plan_dict is None:
            raise ValueError("无法从 LLM 响应中提取 JSON")

        # 用 Pydantic 校验并规范化
        validated = TripPlanSchema(**plan_dict)
        logger.info("JSON fallback plan generated successfully")
        return {
            "draft_plan": validated.model_dump(),
            "retry_count": state.get("retry_count", 0) + 1,
            "status": "planned",
        }
    except Exception:
        logger.exception("JSON fallback also failed")
        return {
            "draft_plan": None,
            "retry_count": state.get("retry_count", 0) + 1,
            "status": "planner_error",
        }


def _extract_json_from_text(text: str) -> dict | None:
    """从 LLM 纯文本响应中提取 JSON 对象。"""
    # 尝试从 ```json 代码块中提取
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试从裸 JSON 中提取
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


# ---------------------------------------------------------------------------
# 节点：校验计划
# ---------------------------------------------------------------------------

def _validate_coordinates(plan: dict) -> list[str]:
    errors: list[str] = []
    for day in plan.get("days", []):
        for attr in day.get("attractions", []):
            loc = attr.get("location") or {}
            lng = loc.get("longitude", 0)
            lat = loc.get("latitude", 0)
            if not (73 <= lng <= 135 and 18 <= lat <= 54):
                errors.append(
                    f"'{attr.get('name', '?')}' 坐标 ({lng}, {lat}) 超出中国范围"
                )
    return errors


def _validate_days(plan: dict, expected_days: int) -> list[str]:
    errors: list[str] = []
    days = plan.get("days", [])
    if len(days) != expected_days:
        errors.append(f"计划包含 {len(days)} 天，期望 {expected_days} 天")
    return errors


def validate_plan(state: TripPlanningState) -> dict[str, Any]:
    """对规划器输出进行结构与地理范围校验。"""
    draft = state.get("draft_plan")
    if draft is None:
        return {"validation_errors": ["Planner 未返回有效计划"], "validated_plan": None}

    errors = _validate_coordinates(draft) + _validate_days(draft, state["travel_days"])

    if not errors:
        logger.info("Plan validation passed")
        return {"validation_errors": [], "validated_plan": draft, "status": "validated"}

    logger.warning("Plan validation failed with %d errors", len(errors))
    return {"validation_errors": errors, "validated_plan": None, "status": "invalid"}


# ---------------------------------------------------------------------------
# 节点：带反馈的重新规划（自修复循环）
# ---------------------------------------------------------------------------

def replan_with_feedback(state: TripPlanningState) -> dict[str, Any]:
    """准备状态以携带错误上下文重新执行规划器。"""
    errors = state.get("validation_errors", [])
    logger.info("Re-planning (attempt %d/%d). Errors: %s",
                state.get("retry_count", 0), state.get("max_retries", MAX_RETRIES), errors)
    return {"validation_errors": errors, "status": "replanning"}


# ---------------------------------------------------------------------------
# 降级方案（仅在重试次数耗尽时触发）
# ---------------------------------------------------------------------------

def create_fallback_plan(state: TripPlanningState) -> dict[str, Any]:
    """生成最小占位计划（标记为降级模式）。"""
    try:
        start = datetime.strptime(state["start_date"], "%Y-%m-%d")
    except ValueError:
        start = datetime.now()

    days = []
    for i in range(state["travel_days"]):
        cur = start + timedelta(days=i)
        days.append({
            "date": cur.strftime("%Y-%m-%d"),
            "day_index": i,
            "description": f"第{i+1}天行程（降级模式）",
            "transportation": state["transportation"],
            "accommodation": state["accommodation"],
            "attractions": [],
            "meals": [],
        })

    plan = {
        "city": state["city"],
        "start_date": state["start_date"],
        "end_date": state["end_date"],
        "days": days,
        "weather_info": [],
        "overall_suggestions": "AI 规划服务暂时不可用，以下是降级占位行程。",
        "budget": None,
    }

    return {
        "validated_plan": plan,
        "status": "fallback",
        "warnings": ["AI 规划服务暂时不可用，返回降级占位行程"],
    }
