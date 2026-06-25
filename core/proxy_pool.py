# coding=utf-8
"""
代理池调度层

职责：按优先级遍历已注册的 ProxyProvider，返回第一个可用代理。
具体供应商实现均在 core/proxy_providers/ 目录，此文件不包含任何供应商逻辑。

新增代理商：在 _build_providers() 中追加实例即可，调度逻辑无需改动。
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from core.proxy_providers.base import ProxyProvider
from core.proxy_providers.shenlong import ShenlongProvider
from core.proxy_providers.static_db import StaticDbProvider
from core.proxy_providers.fallback_url import FallbackUrlProvider
from core.proxy_utils import build_requests_proxy_config

logger = logging.getLogger(__name__)


def _build_providers() -> list[ProxyProvider]:
    """
    返回按优先级排列的供应商列表。
    新增代理商：在此追加实例，放在 FallbackUrlProvider 之前即可。
    """
    return [
        ShenlongProvider(),    # 1. 神龙动态住宅代理
        StaticDbProvider(),    # 2. 数据库静态代理池
        FallbackUrlProvider(), # 3. PROXY_URL 兜底
    ]


class ProxyPool:
    """
    代理池调度器。

    get_next()        — 按优先级取一个代理 URL
    report_success()  — 上报成功（路由到对应 provider）
    report_fail()     — 上报失败（路由到对应 provider）
    check_all()       — 检测数据库静态代理可用性
    """

    def __init__(self):
        self._providers: list[ProxyProvider] = _build_providers()
        # url -> provider name，用于 report_result 路由
        self._url_provider: dict[str, str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def get_next(self, region: str = "") -> Optional[str]:
        """
        按优先级遍历 providers，返回第一个成功的代理 URL。
        某个 provider 抛出异常时自动降级到下一个。
        """
        for provider in self._providers:
            if not provider.is_enabled():
                continue
            try:
                url = provider.get_proxy(region=region)
                if url:
                    with self._lock:
                        self._url_provider[url] = provider.name
                    logger.debug("[proxy_pool] 使用 [%s] %s", provider.name, url)
                    return url
            except Exception as e:
                logger.warning(
                    "[proxy_pool] [%s] 获取代理失败，降级到下一个：%s",
                    provider.name, e,
                )
        logger.debug("[proxy_pool] 所有 provider 均无可用代理")
        return None

    def report_success(self, url: str) -> None:
        self._report(url, success=True)

    def report_fail(self, url: str) -> None:
        self._report(url, success=False)

    def check_all(self) -> dict:
        """检测数据库中所有静态代理的可用性。"""
        import requests
        from core.db import ProxyModel, engine
        from sqlmodel import Session, select

        with Session(engine) as s:
            proxies = s.exec(select(ProxyModel)).all()

        results = {"ok": 0, "fail": 0}
        for p in proxies:
            try:
                r = requests.get(
                    "https://httpbin.org/ip",
                    proxies=build_requests_proxy_config(p.url),
                    timeout=8,
                )
                if r.status_code == 200:
                    self.report_success(p.url)
                    results["ok"] += 1
                    continue
            except Exception:
                pass
            self.report_fail(p.url)
            results["fail"] += 1
        return results

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _report(self, url: str, success: bool) -> None:
        with self._lock:
            provider_name = self._url_provider.get(url)

        if provider_name:
            for provider in self._providers:
                if provider.name == provider_name:
                    provider.report_result(url, success)
                    return

        # url 来源不明（如手动传入），交给 static_db 尝试处理
        for provider in self._providers:
            if provider.name == "static_db":
                provider.report_result(url, success)
                return


proxy_pool = ProxyPool()
