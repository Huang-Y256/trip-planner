"""LangGraph 旅行规划流水线的状态定义。"""

from typing import Annotated, Optional

import operator
from typing_extensions import TypedDict


class TripPlanningState(TypedDict):
    """在所有 LangGraph 节点之间传递的共享可变状态。

    字段按生命周期分组：用户输入、中间搜索结果、
    校验结果、最终输出。
    """

    # -- 用户输入 ---------------------------------------------------------------
    city: str
    start_date: str
    end_date: str
    travel_days: int
    transportation: str
    accommodation: str
    preferences: list[str]
    free_text_input: str

    # -- 中间搜索结果（由搜索节点写入） ------------------------------------------
    attraction_data: str
    weather_data: str
    hotel_data: str

    # -- 规划器输出 ---------------------------------------------------------------
    draft_plan: Optional[dict]
    validated_plan: Optional[dict]
    validation_errors: list[str]
    retry_count: int
    max_retries: int

    # -- 流水线状态 ---------------------------------------------------------------
    status: str
    warnings: Annotated[list[str], operator.add]
