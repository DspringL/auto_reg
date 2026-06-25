# 新代理商接入指南

本文档说明如何为 **Any Auto Register** 接入一个新的海外代理供应商。
整个过程只需新建一个文件并追加两行代码，框架其余部分无需改动。

---

## 目录结构

```
core/
├── proxy_pool.py              # 调度层（仅在此注册新供应商）
├── proxy_providers/
│   ├── __init__.py
│   ├── base.py                # ProxyProvider 抽象基类
│   ├── shenlong.py            # 参考实现：神龙住宅代理
│   ├── static_db.py           # 数据库静态代理池
│   └── fallback_url.py        # PROXY_URL 兜底
```

代理调度优先级（从高到低）：

```
动态供应商（神龙 / 922 / …）
       ↓ 全部失败时
数据库静态代理池
       ↓ 为空时
PROXY_URL 兜底
```

---

## 接入步骤

### 第一步：新建供应商文件

在 `core/proxy_providers/` 目录下新建 `<name>.py`，继承 `ProxyProvider`：

```python
# core/proxy_providers/example.py
# coding=utf-8
"""
ExampleProxy 供应商

配置键（.env / config_store 均可）：
    EXAMPLE_ENABLED     true/false（默认 false）
    EXAMPLE_API_KEY     API 密钥
    EXAMPLE_COUNTRY     目标国家（默认 US）
    EXAMPLE_PROTOCOL    http 或 socks5（默认 http）
    EXAMPLE_FETCH_COUNT 单次提取数量（默认 10）
    EXAMPLE_IP_TTL      IP 有效期分钟（默认 30）
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


# ---------------------------------------------------------------------------
# 配置读取（复用标准 _cfg 模式）
# ---------------------------------------------------------------------------

def _cfg(key: str, default: str = "") -> str:
    """优先读 config_store，回退到 .env / os.environ。"""
    try:
        from core.config_store import config_store
        v = config_store.get(key, "")
        if v:
            return v
    except Exception:
        pass
    import os
    return os.environ.get(key, default)


# ---------------------------------------------------------------------------
# 内部 TTL 缓存（可选，不需要缓存的供应商可省略）
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    lines: list[str]                         # ["ip:port", ...]
    fetched_at: float = field(default_factory=time.time)

    def is_expired(self, ttl_minutes: int) -> bool:
        return (time.time() - self.fetched_at) >= ttl_minutes * 60


# ---------------------------------------------------------------------------
# 供应商实现
# ---------------------------------------------------------------------------

class ExampleProvider(ProxyProvider):

    def __init__(self):
        self._lock = threading.Lock()
        self._cache: Optional[_CacheEntry] = None

    # ── 必须实现的三个方法 ─────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "example"   # 全局唯一，用于日志和 report_result 路由

    def is_enabled(self) -> bool:
        return _cfg("EXAMPLE_ENABLED", "false").lower() in ("1", "true", "yes")

    def get_proxy(self, region: str = "") -> Optional[str]:
        """
        返回一个可用的代理 URL，或 None（表示当前无可用代理）。
        抛出异常时 ProxyPool 会自动降级到下一个供应商。
        """
        lines = self._get_cached_lines()
        if not lines:
            return None
        protocol = _cfg("EXAMPLE_PROTOCOL", "http").lower()
        line = random.choice(lines)
        host, port_str = line.rsplit(":", 1)
        return f"{protocol}://{host}:{port_str}"

    # ── 可选重写 ───────────────────────────────────────────────────────────

    def report_result(self, url: str, success: bool) -> None:
        """
        上报使用结果。
        动态代理建议失败时从缓存移除；静态代理建议写库统计。
        """
        if success:
            return
        with self._lock:
            if not self._cache:
                return
            from urllib.parse import urlsplit
            parts = urlsplit(url)
            bad = f"{parts.hostname}:{parts.port}"
            self._cache.lines = [l for l in self._cache.lines if l != bad]
            logger.debug("[example] 移除失败代理 %s，剩余 %d 条", bad, len(self._cache.lines))

    # ── 内部方法 ───────────────────────────────────────────────────────────

    def fetch_list(self, count: int = 10) -> list[str]:
        """调用供应商 API，返回 ["ip:port", ...] 列表。"""
        api_key = _cfg("EXAMPLE_API_KEY", "")
        if not api_key:
            raise ValueError("ExampleProxy API Key 未配置（EXAMPLE_API_KEY）")

        # 构造请求——根据实际 API 文档修改
        api_url = f"https://api.example-proxy.com/fetch?key={api_key}&count={count}"

        # 调用 API 时通过本地出口代理（境外网络）
        outbound = _cfg("LOCAL_OUTBOUND_PROXY", "") or _cfg("PROXY_URL", "") or None
        proxies_cfg = {"http": outbound, "https": outbound} if outbound else None

        resp = requests.get(api_url, proxies=proxies_cfg, timeout=15)
        resp.raise_for_status()

        # 解析响应——常见格式：每行一个 "ip:port"
        lines = [l.strip() for l in resp.text.strip().splitlines() if ":" in l.strip()]
        logger.info("[example] API 返回 %d 个代理", len(lines))
        return lines

    def _get_cached_lines(self) -> list[str]:
        ttl = int(_cfg("EXAMPLE_IP_TTL", "30") or 30)
        with self._lock:
            if self._cache and not self._cache.is_expired(ttl) and self._cache.lines:
                return list(self._cache.lines)

        try:
            count = int(_cfg("EXAMPLE_FETCH_COUNT", "10") or 10)
            lines = self.fetch_list(count=count)
        except Exception as e:
            logger.warning("[example] 拉取代理列表失败：%s", e)
            with self._lock:
                if self._cache and self._cache.lines:
                    logger.warning("[example] 降级使用过期缓存（%d 条）", len(self._cache.lines))
                    return list(self._cache.lines)
            return []

        with self._lock:
            self._cache = _CacheEntry(lines=lines)
        return lines
```

---

### 第二步：在 proxy_pool.py 注册

打开 `core/proxy_pool.py`，在 `_build_providers()` 中追加实例：

```python
# core/proxy_pool.py

from core.proxy_providers.shenlong import ShenlongProvider
from core.proxy_providers.example import ExampleProvider   # ← 新增导入
from core.proxy_providers.static_db import StaticDbProvider
from core.proxy_providers.fallback_url import FallbackUrlProvider


def _build_providers() -> list[ProxyProvider]:
    return [
        ShenlongProvider(),    # 1. 神龙动态住宅代理
        ExampleProvider(),     # 2. Example 动态代理  ← 新增
        StaticDbProvider(),    # 3. 数据库静态代理池
        FallbackUrlProvider(), # 4. PROXY_URL 兜底
    ]
```

列表顺序即优先级，数字小的先被尝试。

---

### 第三步：添加配置键

**`api/config.py`** — 让前端界面可以读写这些配置：

```python
CONFIG_KEYS = [
    # … 现有键 …

    # Example 动态代理
    "example_enabled",
    "example_api_key",
    "example_country",
    "example_protocol",
    "example_fetch_count",
    "example_ip_ttl",
]
```

**`.env.example`** — 补充配置说明（复制到 `.env` 后填入实际值）：

```ini
# ── Example 动态代理 ───────────────────────────────────────────────────────
# 开启后优先级仅次于前一个供应商；需已配置 LOCAL_OUTBOUND_PROXY
EXAMPLE_ENABLED=false
EXAMPLE_API_KEY=
EXAMPLE_COUNTRY=US
EXAMPLE_PROTOCOL=http
EXAMPLE_FETCH_COUNT=10
EXAMPLE_IP_TTL=30
```

---

### 第四步（可选）：添加 API 端点

如果需要从前端测试/查看该供应商的状态，在 `api/proxies.py` 追加：

```python
# api/proxies.py

def _get_example_provider():
    from core.proxy_providers.example import ExampleProvider
    for p in proxy_pool._providers:
        if isinstance(p, ExampleProvider):
            return p
    return ExampleProvider()


@router.get("/example/status")
def example_status():
    p = _get_example_provider()
    from core.proxy_providers.example import _cfg
    return {
        "enabled": p.is_enabled(),
        "api_key_set": bool(_cfg("EXAMPLE_API_KEY")),
        "country": _cfg("EXAMPLE_COUNTRY", "US"),
        "cache_size": len(p._cache.lines) if p._cache else 0,
    }


@router.post("/example/fetch")
def example_fetch(count: int = 10):
    p = _get_example_provider()
    if not p.is_enabled():
        raise HTTPException(400, "Example 代理未启用")
    try:
        lines = p.fetch_list(count=count)
    except (ValueError, ConnectionError) as e:
        raise HTTPException(502, str(e))
    return {"fetched_count": len(lines), "sample": lines[:5]}


@router.post("/example/verify")
def example_verify():
    p = _get_example_provider()
    if not p.is_enabled():
        raise HTTPException(400, "Example 代理未启用")
    proxy_url = p.get_proxy()
    if not proxy_url:
        raise HTTPException(502, "代理列表为空")
    # 复用神龙的 verify 逻辑，或自己实现
    from core.proxy_providers.shenlong import ShenlongProvider
    result = ShenlongProvider().verify(proxy_url)
    return {"proxy_url": proxy_url, "exit_ip_info": result}
```

---

## 注意事项

### 配置读取

所有 provider 统一用以下模式读取配置，优先级：数据库 `config_store` > `.env` > 环境变量：

```python
def _cfg(key: str, default: str = "") -> str:
    try:
        from core.config_store import config_store
        v = config_store.get(key, "")
        if v:
            return v
    except Exception:
        pass
    import os
    return os.environ.get(key, default)
```

### 本地出口代理

调用供应商 API 时必须通过境外网络，统一使用：

```python
outbound = _cfg("LOCAL_OUTBOUND_PROXY", "") or _cfg("PROXY_URL", "") or None
```

不要硬编码端口，不要自己再读一遍 `.env`。

### TTL 缓存

动态代理商建议实现缓存，避免每次注册都发 API 请求：

- `FETCH_COUNT` 控制单次批量拉取数量（建议 10~50）
- `IP_TTL` 控制缓存有效期（与代理商 API 参数一致）
- 缓存过期时先拉新列表，拉取失败时降级使用旧缓存（见 `_get_cached_lines`）

### report_result

| 供应商类型 | 建议实现 |
|---|---|
| 动态 IP（按次计费） | 失败时从内存缓存移除该条目，不写库 |
| 静态长效 IP | 写库更新成功/失败计数，失败超阈值自动禁用 |
| 无需统计 | 保留默认空实现即可 |

### get_proxy 返回 None vs 抛出异常

| 场景 | 应该 |
|---|---|
| 供应商已启用但当前没有可用 IP | 返回 `None`，ProxyPool 跳过此供应商 |
| 配置不完整（如 API Key 为空） | 抛出 `ValueError`，ProxyPool 记录 warning 后降级 |
| 网络请求失败 | 抛出 `ConnectionError`，ProxyPool 记录 warning 后降级 |

---

## 快速检查清单

接入完成后，逐项确认：

- [ ] `core/proxy_providers/<name>.py` 已创建，继承 `ProxyProvider`
- [ ] `name` 属性返回全局唯一的小写字符串
- [ ] `is_enabled()` 读取专属开关配置键
- [ ] `get_proxy()` 在无可用 IP 时返回 `None`，不返回空字符串
- [ ] `fetch_list()` 通过 `LOCAL_OUTBOUND_PROXY` 发起 API 请求
- [ ] `proxy_pool.py` 的 `_build_providers()` 已追加新实例
- [ ] `api/config.py` 的 `CONFIG_KEYS` 已补充配置键
- [ ] `.env.example` 已补充配置示例
- [ ] `python -m py_compile core/proxy_providers/<name>.py` 通过
