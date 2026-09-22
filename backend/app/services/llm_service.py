"""基于 LangChain ChatOpenAI（OpenAI 兼容）的 LLM 服务。"""

from __future__ import annotations

import os
import logging

from langchain_openai import ChatOpenAI

from ..config import get_settings

logger = logging.getLogger(__name__)

_llm_instance: ChatOpenAI | None = None


def get_llm(temperature: float = 0.2, **kwargs) -> ChatOpenAI:
    """返回缓存的 ChatOpenAI 兼容 LLM 实例。

    从环境变量读取 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL
    （或此前使用的 LLM_* 等效变量）。
    """
    global _llm_instance
    if _llm_instance is not None:
        return _llm_instance

    settings = get_settings()

    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or settings.openai_api_key
    base_url = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or settings.openai_base_url
    model = os.getenv("LLM_MODEL_ID") or os.getenv("OPENAI_MODEL") or settings.openai_model

    if not api_key:
        raise ValueError(
            "LLM API 密钥未配置，请在 .env 中设置 LLM_API_KEY 或 OPENAI_API_KEY"
        )

    _llm_instance = ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=temperature,
        **kwargs,
    )

    if os.getenv("LANGCHAIN_TRACING_V2") == "true" and os.getenv("LANGCHAIN_API_KEY"):
        project = os.getenv("LANGCHAIN_PROJECT", "default")
        logger.info("LangSmith 追踪已启用，项目: %s", project)
    else:
        logger.info("LangSmith 追踪未启用（设置 LANGCHAIN_TRACING_V2=true 开启）")

    return _llm_instance


def reset_llm() -> None:
    """重置缓存的 LLM 实例（用于测试）。"""
    global _llm_instance
    _llm_instance = None
