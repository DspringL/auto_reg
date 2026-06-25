# coding=utf-8
"""
神龙海外住宅代理供应商

通过神龙 API 按需提取海外住宅 IP，内置 TTL 缓存，避免每次注册都发 API 请求。

配置键（config_store / .env 均可）：
    SHENLONG_ENABLED        true/false（默认 false）
    SHENLONG_API_KEY        API 密钥
    SHENLONG_COUNTRY        目标国家，如 US / GB / JP（默认 US）
    SHENLONG_PROTOCOL       http 或 socks5（默认 http）
    SHENLONG_FETCH_COUNT    单次 API 提取数量（默认 10，用于本地缓存池）
    SHENLONG_IP_TTL         IP 有效期分钟数（默认 30）
    LOCAL_OUTBOUND_PROXY    本地出口代理，用于调用神龙 API（如 Clash 端口）
"""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

from .base import ProxyProvider

logger = logging.getLogger(__name__)

# 协议 -> API pt 参数映射
_PROTOCOL_PT: dict[str, str] = {"http": "1", "socks5": "3"}

_API_URL_TEMPLATE = (
    "http://api.shenlongproxy.com/ip"
    "?cty={cty}&c={count}&pt={pt}&ft=txt&pat=\\n&rep=1&key={key}&ts={ts}"
)


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _cfg(key: str, default: str = "") -> str:
    """读取配置：config_store > .env > os.environ。"""
    try:
        from core.config_store import config_store  # noqa
        v = config_store.get(key, "")
        if v:
            return v
    except Exception:
        pass
    # 直接读环境变量
    v = os.environ.get(key, "")
    if v:
        return v
    # 回退到直接解析 .env 文件（应用未用 load_dotenv 时）
    try:
        from core.config_store import _get_env_fallback_value
        v = _get_env_fallback_value(key)
        if v:
            return v
    except Exception:
        pass
    return default


@dataclass
class _CacheEntry:
    """IP 缓存条目，记录获取时间用于 TTL 判断。"""
    lines: list[str]          # ["ip:port", ...]
    fetched_at: float = field(default_factory=time.time)

    def is_expired(self, ttl_minutes: int) -> bool:
        return (time.time() - self.fetched_at) >= ttl_minutes * 60


# ---------------------------------------------------------------------------
# Provider 实现
# ---------------------------------------------------------------------------

class ShenlongProvider(ProxyProvider):
    """
    神龙动态代理供应商。

    内部维护一个 IP 列表缓存，在 TTL 内随机取用，超时后重新调 API 刷新。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cache: Optional[_CacheEntry] = None

    @property
    def name(self) -> str:
        return "shenlong"

    def is_enabled(self) -> bool:
        return _cfg("SHENLONG_ENABLED", "false").lower() in ("1", "true", "yes")

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def get_proxy(self, region: str = "") -> Optional[str]:
        """从缓存或 API 取一个代理 URL，region 参数暂时忽略（由 API URL 的 cty 统一控制）。"""
        lines = self._get_cached_lines()
        if not lines:
            return None
        protocol = _cfg("SHENLONG_PROTOCOL", "http").lower()
        line = random.choice(lines)
        host, port_str = line.rsplit(":", 1)
        # 支持用户名密码认证：SHENLONG_USERNAME / SHENLONG_PASSWORD
        username = _cfg("SHENLONG_USERNAME", "")
        password = _cfg("SHENLONG_PASSWORD", "")
        if username and password:
            url = f"{protocol}://{username}:{password}@{host}:{port_str}"
        else:
            url = f"{protocol}://{host}:{port_str}"
        logger.info("[shenlong] 选取代理 %s", url)
        return url

    def report_result(self, url: str, success: bool) -> None:
        # 动态 IP 不持久化，失败时仅从当前缓存中移除该条目
        if success:
            return
        with self._lock:
            if self._cache is None:
                return
            # 从 host:port 匹配（去掉 scheme）
            try:
                from urllib.parse import urlsplit
                parts = urlsplit(url)
                entry_str = f"{parts.hostname}:{parts.port}"
                self._cache.lines = [l for l in self._cache.lines if l != entry_str]
                logger.debug("[shenlong] 已移除失败代理 %s，剩余 %d 条", entry_str, len(self._cache.lines))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # API 获取与缓存
    # ------------------------------------------------------------------

    def fetch_list(self, count: int | None = None) -> list[str]:
        """
        调用神龙 API，返回 ["ip:port", ...] 列表。
        通过 LOCAL_OUTBOUND_PROXY（或 PROXY_URL 兜底）发起请求。

        Raises:
            ValueError:      API Key 未配置
            ConnectionError: API 请求失败
        """
        if count is None:
            try:
                count = int(_cfg("SHENLONG_FETCH_COUNT", "10"))
            except ValueError:
                count = 10

        api_key = _cfg("SHENLONG_API_KEY", "")
        if not api_key:
            raise ValueError("神龙代理 API Key 未配置（SHENLONG_API_KEY）")

        cty = _cfg("SHENLONG_COUNTRY", "US")
        protocol = _cfg("SHENLONG_PROTOCOL", "http").lower()
        pt = _PROTOCOL_PT.get(protocol, "1")
        ts = _cfg("SHENLONG_IP_TTL", "30")
        url = _API_URL_TEMPLATE.format(key=api_key, count=count, cty=cty, pt=pt, ts=ts)

        # LOCAL_OUTBOUND_PROXY 优先，PROXY_URL 兜底（向后兼容）
        outbound = _cfg("LOCAL_OUTBOUND_PROXY", "") or _cfg("PROXY_URL", "") or None
        proxies_cfg = {"http": outbound, "https": outbound} if outbound else None

        logger.info("[shenlong] 正在提取 %d 个代理（出口：%s）...", count, outbound or "直连")
        try:
            resp = requests.get(url, proxies=proxies_cfg, timeout=15)
            resp.raise_for_status()
            lines = [
                line.strip()
                for line in resp.text.strip().splitlines()
                if ":" in line.strip()
            ]
            logger.info("[shenlong] API 返回 %d 个代理", len(lines))
            return lines
        except requests.RequestException as e:
            raise ConnectionError(f"神龙 API 调用失败：{e}") from e

    def verify(self, url: str, timeout: int = 15) -> str:
        """
        验证指定代理 URL 的连通性，返回出口 IP 文本。

        Raises:
            ConnectionError: 所有验证地址均失败
        """
        check_urls = [
            "http://myip.ipip.net",
            "https://api.ipify.org?format=json",
            "https://httpbin.org/ip",
        ]
        proxies_cfg = {"http": url, "https": url}
        for check_url in check_urls:
            try:
                start = int(time.time() * 1000)
                resp = requests.get(check_url, proxies=proxies_cfg, timeout=timeout)
                cost = int(time.time() * 1000) - start
                text = resp.text.strip()
                logger.info("[shenlong] 验证 %s -> %s（%dms）", check_url, text, cost)
                return text
            except Exception as e:
                logger.debug("[shenlong] 验证 %s 失败：%s", check_url, e)
        raise ConnectionError("神龙代理验证失败，所有检测地址均不可达")

    # ------------------------------------------------------------------
    # 内部缓存管理
    # ------------------------------------------------------------------

    def _get_cached_lines(self) -> list[str]:
        """返回有效缓存中的 IP 列表，过期或为空时重新拉取。"""
        with self._lock:
            ttl = self._ttl_minutes()
            if self._cache and not self._cache.is_expired(ttl) and self._cache.lines:
                return list(self._cache.lines)

        # 缓存失效，在锁外拉取（避免阻塞其他线程过久）
        try:
            lines = self.fetch_list()
        except Exception as e:
            logger.warning("[shenlong] 拉取代理列表失败：%s", e)
            # 缓存虽过期但还有内容时降级使用旧列表
            with self._lock:
                if self._cache and self._cache.lines:
                    logger.warning("[shenlong] API 失败，降级使用过期缓存（%d 条）", len(self._cache.lines))
                    return list(self._cache.lines)
            return []

        with self._lock:
            self._cache = _CacheEntry(lines=lines)
        return lines

    def _ttl_minutes(self) -> int:
        try:
            return int(_cfg("SHENLONG_IP_TTL", "30"))
        except ValueError:
            return 30
