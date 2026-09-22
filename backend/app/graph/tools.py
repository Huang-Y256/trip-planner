"""高德地图数据获取工具——MCP 优先 + REST API 兜底的双层策略。

架构设计：
  第 1 层：MCP 协议（uvx amap-mcp-server），体现标准协议集成能力
  第 2 层：高德 REST API 直调，当 MCP 不可用时自动降级

面试话术：实现了 MCP 优先 + REST 兜底的双层数据获取策略。
当第三方 MCP Server 因依赖兼容性问题不可用时，系统自动降级
到 REST API，保证了数据获取的可靠性，同时保留了协议集成的
架构演示价值。
"""

from __future__ import annotations

import json
import subprocess
import logging
import os
import sys
import threading
from pathlib import Path
from queue import Queue, Empty
from typing import Any

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

_AMAP_BASE = "https://restapi.amap.com/v3"

# =====================================================================
# 第 1 层：MCP 协议调用（uvx amap-mcp-server）
# =====================================================================


def _find_uvx() -> str:
    """找到 uvx 可执行文件的完整路径。"""
    python_dir = Path(sys.executable).parent
    candidates = [
        python_dir / "Scripts" / "uvx.exe",
        python_dir / "uvx.exe",
        python_dir / "bin" / "uvx",
        python_dir / "uvx",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return "uvx"


def _read_line_with_timeout(proc: subprocess.Popen, timeout: float = 15.0) -> str | None:
    """从子进程 stdout 读取一行，带超时保护。"""
    result_queue: Queue = Queue()

    def _reader():
        try:
            line = proc.stdout.readline()
            result_queue.put(line)
        except Exception:
            result_queue.put(None)

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    try:
        return result_queue.get(timeout=timeout)
    except Empty:
        return None


def _build_env() -> dict:
    """构建 MCP Server 运行环境变量。"""
    settings = get_settings()
    env = {**os.environ, "AMAP_MAPS_API_KEY": settings.amap_api_key}

    uv_cache = Path(os.environ.get("UV_CACHE_DIR", str(Path(os.environ.get("TEMP", "/tmp")) / "uv-cache")))
    uv_cache.mkdir(parents=True, exist_ok=True)
    env["UV_CACHE_DIR"] = str(uv_cache)
    env["UV_TOOL_DIR"] = str(uv_cache / "tools")
    env["UV_TOOL_DATA_DIR"] = str(uv_cache / "tool-data")

    return env


def _call_via_mcp(tool_name: str, arguments: dict) -> str:
    """通过 MCP 协议调用工具（完整握手：initialize → initialized → tools/call）。

    成功返回工具结果字符串，失败返回空字符串。
    """
    uvx_path = _find_uvx()
    env = _build_env()

    proc = None
    try:
        proc = subprocess.Popen(
            [uvx_path, "amap-mcp-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        # -- 步骤 1：发送 initialize 请求 --
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "trip-planner", "version": "1.0.0"},
            },
        }) + "\n")
        proc.stdin.flush()

        # -- 步骤 2：等待 initialize 响应 --
        init_ok = False
        while True:
            line = _read_line_with_timeout(proc, timeout=10)
            if line is None:
                logger.debug("MCP %s: initialize 超时", tool_name)
                return ""
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                continue
            if resp.get("id") == 1 and "result" in resp:
                init_ok = True
                break

        if not init_ok:
            return ""

        # -- 步骤 3：发送 initialized 通知 --
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        # -- 步骤 4：发送 tools/call 请求 --
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }) + "\n")
        proc.stdin.flush()

        # -- 步骤 5：等待工具调用结果 --
        while True:
            line = _read_line_with_timeout(proc, timeout=10)
            if line is None:
                logger.debug("MCP %s: tools/call 响应超时", tool_name)
                return ""
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                continue
            if resp.get("id") == 2 and "result" in resp:
                content = resp["result"].get("content", [])
                texts = [c.get("text", "") for c in content if c.get("type") == "text"]
                return "\n".join(texts)
            if resp.get("id") == 2 and "error" in resp:
                logger.debug("MCP %s: 工具调用出错: %s", tool_name, resp["error"])
                return ""

        return ""
    except Exception:
        logger.debug("MCP %s: 调用异常", tool_name, exc_info=True)
        return ""
    finally:
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                proc.kill()


# =====================================================================
# 第 2 层：高德 REST API 直调（兜底方案）
# =====================================================================


def _call_via_rest(tool_name: str, arguments: dict) -> str:
    """直接调用高德 REST API（MCP 不可用时的兜底方案）。"""
    settings = get_settings()
    if not settings.amap_api_key:
        return ""

    # MCP 工具名 → REST API 路径 + 参数映射
    rest_map = {
        "maps_text_search": ("/place/text", {
            "keywords": arguments.get("keywords", ""),
            "city": arguments.get("city", ""),
            "citylimit": "true" if arguments.get("citylimit", "true") in ("true", True) else "false",
            "offset": 10,
        }),
        "maps_weather": ("/weather/weatherInfo", {
            "city": arguments.get("city", ""),
            "extensions": "all",
        }),
        "maps_geo": ("/geocode/geo", {
            "address": arguments.get("address", ""),
            "city": arguments.get("city", ""),
        }),
    }

    mapping = rest_map.get(tool_name)
    if not mapping:
        logger.warning("未知的工具名: %s", tool_name)
        return ""

    path, params = mapping
    params["key"] = settings.amap_api_key
    url = f"{_AMAP_BASE}{path}"

    try:
        resp = httpx.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "1":
            logger.warning("高德 REST API 返回错误: %s", data.get("info", "unknown"))
            return ""
        return resp.text
    except httpx.TimeoutException:
        logger.warning("高德 REST API %s 超时", path)
        return ""
    except Exception:
        logger.exception("高德 REST API %s 调用失败", path)
        return ""


# =====================================================================
# 对外接口：双层策略入口
# =====================================================================


def call_mcp_tool(tool_name: str, arguments: dict) -> str:
    """高德数据获取入口：MCP 优先，REST API 兜底。

    策略：
    1. 尝试通过 MCP 协议调用（如果可用）
    2. MCP 失败/超时/返回空 → 自动降级到 REST API
    3. 两种方式都失败 → 返回空字符串

    保持与原有 call_mcp_tool() 签名一致，调用方无需修改。
    """
    settings = get_settings()
    if not settings.amap_api_key:
        logger.warning("AMAP_API_KEY 未配置，跳过工具调用")
        return ""

    # 第 1 层：尝试 MCP
    result = _call_via_mcp(tool_name, arguments)
    if result:
        logger.debug("MCP %s: 成功", tool_name)
        return result

    # 第 2 层：MCP 失败，降级到 REST API
    logger.info("MCP %s: 不可用，降级到 REST API", tool_name)
    result = _call_via_rest(tool_name, arguments)
    if result:
        logger.debug("REST API %s: 成功", tool_name)
        return result

    logger.warning("MCP 和 REST API 均无法获取 %s 数据", tool_name)
    return ""


def is_mcp_available() -> bool:
    """检查 MCP 或 REST API 是否至少有一种可用。"""
    settings = get_settings()
    if not settings.amap_api_key:
        return False

    # 检查 uvx 是否安装（MCP 可用性）
    try:
        subprocess.run(
            [_find_uvx(), "--version"], capture_output=True, timeout=5, check=True
        )
        return True  # MCP 可用
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False  # MCP 不可用，但 REST API 仍可作为兜底

