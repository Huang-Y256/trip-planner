"""旅行计划持久化存储（SQLite）。

替代前端 sessionStorage，支持：
1. 生成计划后返回 plan_id，前端通过 URL 访问
2. 跨标签页/跨设备分享（通过 URL）
3. 刷新页面数据不丢失
"""

from __future__ import annotations

import sqlite3
import json
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 数据库文件放在 backend/data/ 目录下
_DB_DIR = Path(__file__).resolve().parent.parent / "data"
_DB_PATH = _DB_DIR / "trip_plans.db"


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接（自动建表）。"""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trip_plans (
            id TEXT PRIMARY KEY,
            plan_json TEXT NOT NULL,
            status TEXT DEFAULT 'validated',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    return conn


def save_plan(plan_data: dict, status: str = "validated") -> str:
    """保存旅行计划，返回 plan_id。"""
    plan_id = uuid.uuid4().hex[:12]
    now = datetime.utcnow().isoformat()
    plan_json = json.dumps(plan_data, ensure_ascii=False)

    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO trip_plans (id, plan_json, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (plan_id, plan_json, status, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("旅行计划已保存: plan_id=%s, city=%s", plan_id, plan_data.get("city", "?"))
    return plan_id


def get_plan(plan_id: str) -> Optional[dict]:
    """根据 plan_id 获取旅行计划。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT plan_json, status FROM trip_plans WHERE id = ?", (plan_id,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    return {
        "plan": json.loads(row[0]),
        "status": row[1],
    }


def update_plan(plan_id: str, plan_data: dict) -> bool:
    """更新已有计划（用于编辑模式保存）。"""
    now = datetime.utcnow().isoformat()
    plan_json = json.dumps(plan_data, ensure_ascii=False)

    conn = _get_conn()
    try:
        cursor = conn.execute(
            "UPDATE trip_plans SET plan_json = ?, updated_at = ? WHERE id = ?",
            (plan_json, now, plan_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def list_plans(limit: int = 20) -> list[dict]:
    """列出最近的计划（用于历史记录功能）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, plan_json, status, created_at FROM trip_plans ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        plan_data = json.loads(row[1])
        results.append({
            "plan_id": row[0],
            "city": plan_data.get("city", ""),
            "start_date": plan_data.get("start_date", ""),
            "end_date": plan_data.get("end_date", ""),
            "status": row[2],
            "created_at": row[3],
        })
    return results

