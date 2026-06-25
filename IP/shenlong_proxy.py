# coding=utf-8
"""
神龙海外代理工具函数 - API 提取模式

流程：
  1. 调用 API 链接提取一批 IP:PORT
  2. 从列表中随机选一个可用的代理
  3. 返回给调用方使用（requests / Playwright 均可）

使用前提：本机已开启境外网络（Clash Verge 全局代理）
"""

import requests
import random
import time
import logging
from dataclasses import dataclass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================
# 配置区
# ============================================================

# 神龙代理 API 提取链接（数量参数 c 由函数动态替换，此处 c 值作为默认值占位）
# 后台路径：获取代理 -> API 提取 -> 生成链接
API_URL = "http://api.shenlongproxy.com/ip?cty=US&c={count}&pt=1&ft=txt&pat=\\n&rep=1&key=7a7e4c02&ts=30"

# 每次调用 API 提取的 IP 数量，1 表示单次只取一个
API_FETCH_COUNT = 1

# pt=1 对应 HTTP 协议；pt=3 对应 SOCKS5 协议
PROXY_PROTOCOL = "http"

# 本机 Clash Verge 代理端口（用于调用神龙 API，需要境外网络）
CLASH_PROXY = "http://127.0.0.1:7897"

# ============================================================


@dataclass
class ProxyConfig:
    host: str
    port: int
    protocol: str = "http"

    def to_url(self) -> str:
        return f"{self.protocol}://{self.host}:{self.port}"

    def to_requests_proxies(self) -> dict:
        url = self.to_url()
        return {"http": url, "https": url}

    def to_playwright_proxy(self) -> dict:
        return {"server": self.to_url()}


def fetch_proxy_list(count: int = API_FETCH_COUNT) -> list[str]:
    """
    调用 API 链接，返回原始 IP:PORT 字符串列表

    Args:
        count: 提取数量，默认由 API_FETCH_COUNT 决定（默认 1）

    Returns:
        ["1.2.3.4:8080", ...]
    """
    url = API_URL.format(count=count)
    logger.info(f"正在从 API 提取 {count} 个代理 IP...")
    try:
        resp = requests.get(
            url,
            proxies={"http": CLASH_PROXY, "https": CLASH_PROXY},
            timeout=15,
        )
        resp.raise_for_status()
        lines = [line.strip() for line in resp.text.strip().splitlines() if ":" in line.strip()]
        logger.info(f"成功提取 {len(lines)} 个代理 IP")
        return lines
    except requests.RequestException as e:
        raise ConnectionError(f"调用神龙 API 失败：{e}") from e


def pick_proxy(proxy_lines: list[str], protocol: str = PROXY_PROTOCOL) -> ProxyConfig:
    """
    从列表中随机取一个代理，返回 ProxyConfig 对象

    Args:
        proxy_lines: fetch_proxy_list() 返回的列表
        protocol:    "http" 或 "socks5"
    """
    if not proxy_lines:
        raise ValueError("代理列表为空，无法选取代理")
    line = random.choice(proxy_lines)
    host, port = line.rsplit(":", 1)
    return ProxyConfig(host=host, port=int(port), protocol=protocol)


def get_proxy(protocol: str = PROXY_PROTOCOL, count: int = API_FETCH_COUNT) -> ProxyConfig:
    """
    一步到位：调用 API 提取 count 个 IP -> 随机选一个 -> 返回 ProxyConfig

    Args:
        protocol: "http" 或 "socks5"
        count:    API 提取数量，默认 1（单次只取一个，直接使用）

    Returns:
        ProxyConfig 对象
    """
    lines = fetch_proxy_list(count=count)
    proxy = pick_proxy(lines, protocol)
    logger.info(f"已选取代理：{proxy.to_url()}")
    return proxy


def verify_with_requests(proxy: ProxyConfig, timeout: int = 15) -> str:
    """
    用 requests 快速验证代理，返回出口 IP

    Args:
        proxy:   ProxyConfig 对象
        timeout: 超时秒数

    Returns:
        出口 IP 字符串
    """
    # 官方 demo 同款验证地址 + 备用
    check_urls = [
        "http://myip.ipip.net",
        "https://api.ipify.org?format=json",
        "https://httpbin.org/ip",
    ]
    proxies = proxy.to_requests_proxies()
    for url in check_urls:
        try:
            start = int(time.time() * 1000)
            resp = requests.get(url, proxies=proxies, timeout=timeout)
            cost = int(time.time() * 1000) - start
            text = resp.text.strip()
            logger.info(f"[验证] {url} -> {text} (耗时 {cost}ms)")
            return text
        except Exception as e:
            logger.warning(f"[验证] {url} 失败：{e}")
    raise ConnectionError("所有验证地址均失败，该代理不可用")


if __name__ == "__main__":
    print("=" * 55)
    print("神龙代理 API 提取模式 - requests 快速验证")
    print("=" * 55)
    proxy = get_proxy()
    print(f"使用代理：{proxy.to_url()}")
    result = verify_with_requests(proxy)
    print(f"出口 IP 信息：{result}")
