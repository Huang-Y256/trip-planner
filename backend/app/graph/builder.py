"""编译 LangGraph 旅行规划工作流。"""

from __future__ import annotations

from typing import Literal

from langgraph.graph import StateGraph, END

from .state import TripPlanningState
from .nodes import (
    extract_preferences,
    parallel_fetch,
    plan_itinerary,
    validate_plan,
    replan_with_feedback,
    create_fallback_plan,
    MAX_RETRIES,
)


def _route_after_validation(state: TripPlanningState) -> Literal["pass", "retry", "fail"]:
    errors = state.get("validation_errors", [])
    retries = state.get("retry_count", 0)
    max_retries = state.get("max_retries", MAX_RETRIES)

    if not errors:
        return "pass"
    if retries < max_retries:
        return "retry"
    return "fail"


def build_trip_planning_graph():
    """构建并编译旅行规划的 StateGraph。"""

    workflow = StateGraph(TripPlanningState)

    # -- 注册节点 ----------------------------------------------------------------
    workflow.add_node("extract_prefs", extract_preferences)
    workflow.add_node("parallel_fetch", parallel_fetch)
    workflow.add_node("planner", plan_itinerary)
    workflow.add_node("validator", validate_plan)
    workflow.add_node("replanner", replan_with_feedback)
    workflow.add_node("fallback", create_fallback_plan)

    # -- 连接边 -------------------------------------------------------------------
    workflow.set_entry_point("extract_prefs")
    workflow.add_edge("extract_prefs", "parallel_fetch")
    workflow.add_edge("parallel_fetch", "planner")
    workflow.add_edge("planner", "validator")

    workflow.add_conditional_edges(
        "validator",
        _route_after_validation,
        {
            "pass": END,
            "retry": "replanner",
            "fail": "fallback",
        },
    )
    workflow.add_edge("replanner", "planner")

    # -- 编译 ---------------------------------------------------------------------
    return workflow.compile()


# 模块级编译图（单例模式）
_graph = None


def get_trip_planning_graph():
    global _graph
    if _graph is None:
        _graph = build_trip_planning_graph()
    return _graph
