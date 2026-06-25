# coding=utf-8
"""
PROXY_URL 兜底供应商

读取 .env / config_store 中的 PROXY_URL，作为最后一级回退。
当且仅当其他动态/静态供应商均无法提供代理时使用。
"""
from __future__ import annotations

import logging
from typing import Optional

from .base import ProxyProvider

logger = logging.getLogger(__name__)


def _get_proxy_url() -> str:
    try:
        from core.config_store import _get_env_fallback_value  # noqa
        v = _get_env_fallback_value("PROXY_URL")
        if v:
            return v
    except Exception:
        pass
    import os
    return os.environ.get("PROXY_URL", "").strip()


class FallbackUrlProvider(ProxyProvider):
    """
    PROXY_URL 兜底：适合本机开着 Clash / V2Ray 等本地代理的场景。
    始终 is_enabled=True，但 get_proxy 在 PROXY_URL 为空时返回 None。
    """

    @property
    def name(self) -> str:
        return "fallback_url"

    def is_enabled(self) -> bool:
        return True

    def get_proxy(self, region: str = "") -> Optional[str]:
        url = _get_proxy_url()
        if url:
            logger.debug("[fallback_url] 使用 PROXY_URL %s", url)
            return url
        return None
