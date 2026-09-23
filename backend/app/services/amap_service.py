"""高德地图 MCP 服务封装。"""

from __future__ import annotations

import json
import re
import logging
from typing import Any, Dict, List, Optional

from ..models.schemas import Location, POIInfo, WeatherInfo
from ..graph.tools import call_mcp_tool

logger = logging.getLogger(__name__)


def _extract_json(raw: str) -> Optional[dict]:
    """从 MCP 工具输出字符串中提取第一个 JSON 对象。"""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


class AmapService:
    """高德地图 MCP 工具调用的轻量封装。"""

    def search_poi(self, keywords: str, city: str, citylimit: bool = True) -> List[POIInfo]:
        raw = call_mcp_tool("maps_text_search", {
            "keywords": keywords,
            "city": city,
            "citylimit": str(citylimit).lower(),
        })
        data = _extract_json(raw)
        if not data:
            return []

        pois: List[POIInfo] = []
        for item in data.get("pois", []):
            loc_str = item.get("location", "")
            parts = loc_str.split(",")
            if len(parts) != 2:
                continue
            try:
                lng, lat = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            pois.append(POIInfo(
                id=item.get("id", ""),
                name=item.get("name", ""),
                type=item.get("type", ""),
                address=item.get("address", ""),
                location=Location(longitude=lng, latitude=lat),
                tel=item.get("tel") or None,
            ))
        return pois

    def get_weather(self, city: str) -> List[WeatherInfo]:
        raw = call_mcp_tool("maps_weather", {"city": city})
        data = _extract_json(raw)
        if not data:
            return []

        results: List[WeatherInfo] = []
        for item in data.get("forecasts", [{}])[0].get("casts", []):
            try:
                day_temp = int(str(item.get("daytemp", "0")).replace("°", "").replace("C", ""))
                night_temp = int(str(item.get("nighttemp", "0")).replace("°", "").replace("C", ""))
            except ValueError:
                day_temp, night_temp = 0, 0

            results.append(WeatherInfo(
                date=item.get("date", ""),
                day_weather=item.get("dayweather", ""),
                night_weather=item.get("nightweather", ""),
                day_temp=day_temp,
                night_temp=night_temp,
                wind_direction=item.get("daywind", ""),
                wind_power=item.get("daypower", ""),
            ))
        return results

    def plan_route(
        self,
        origin_address: str,
        destination_address: str,
        origin_city: Optional[str] = None,
        destination_city: Optional[str] = None,
        route_type: str = "walking",
    ) -> Dict[str, Any]:
        tool_map = {
            "walking": "maps_direction_walking_by_address",
            "driving": "maps_direction_driving_by_address",
            "transit": "maps_direction_transit_integrated_by_address",
        }
        tool_name = tool_map.get(route_type, tool_map["walking"])

        args: Dict[str, Any] = {
            "origin_address": origin_address,
            "destination_address": destination_address,
        }
        if origin_city:
            args["origin_city"] = origin_city
        if destination_city:
            args["destination_city"] = destination_city

        raw = call_mcp_tool(tool_name, args)
        data = _extract_json(raw)
        if not data:
            return {}
        return data

    def geocode(self, address: str, city: Optional[str] = None) -> Optional[Location]:
        args: Dict[str, Any] = {"address": address}
        if city:
            args["city"] = city

        raw = call_mcp_tool("maps_geo", args)
        data = _extract_json(raw)
        if not data:
            return None

        geocodes = data.get("geocodes", [])
        if not geocodes:
            return None

        loc_str = geocodes[0].get("location", "")
        parts = loc_str.split(",")
        if len(parts) != 2:
            return None
        try:
            return Location(longitude=float(parts[0]), latitude=float(parts[1]))
        except ValueError:
            return None

    def get_poi_detail(self, poi_id: str) -> Dict[str, Any]:
        raw = call_mcp_tool("maps_search_detail", {"id": poi_id})
        data = _extract_json(raw)
        if not data:
            return {"raw": raw}
        return data


_amap_service: AmapService | None = None


def get_amap_service() -> AmapService:
    global _amap_service
    if _amap_service is None:
        _amap_service = AmapService()
    return _amap_service
