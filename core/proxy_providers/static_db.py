# coding=utf-8
"""
静态数据库代理池

从 SQLite ProxyModel 表读取，加权轮询（成功率高的优先）。
对应原 proxy_pool.py 中的数据库逻辑。
"""
from __future__ import annotations

import threading
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from .base import ProxyProvider

logger = logging.getLogger(__name__)


class StaticDbProvider(ProxyProvider):
    """从数据库读取静态代理，加权轮询。"""

    def __init__(self):
        self._index = 0
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "static_db"

    def is_enabled(self) -> bool:
        # 只要数据库里有活跃代理就视为可用；get_proxy 返回 None 时 ProxyPool 自动跳过
        return True

    def get_proxy(self, region: str = "") -> Optional[str]:
        from core.db import ProxyModel, engine  # 延迟导入避免循环

        with Session(engine) as s:
            q = select(ProxyModel).where(ProxyModel.is_active == True)
            if region:
                q = q.where(ProxyModel.region == region)
            proxies = s.exec(q).all()

        if not proxies:
            return None

        proxies = sorted(
            proxies,
            key=lambda p: p.success_count / max(p.success_count + p.fail_count, 1),
            reverse=True,
        )
        with self._lock:
            idx = self._index % len(proxies)
            self._index += 1

        url = proxies[idx].url
        logger.debug("[static_db] 选取代理 %s", url)
        return url

    def report_result(self, url: str, success: bool) -> None:
        from core.db import ProxyModel, engine

        with Session(engine) as s:
            p = s.exec(select(ProxyModel).where(ProxyModel.url == url)).first()
            if not p:
                return
            if success:
                p.success_count += 1
            else:
                p.fail_count += 1
                # 从未成功且连续失败 5 次则自动禁用
                if p.success_count == 0 and p.fail_count >= 5:
                    p.is_active = False
                    logger.warning("[static_db] 代理 %s 连续失败 %d 次，已自动禁用", url, p.fail_count)
            p.last_checked = datetime.now(timezone.utc)
            s.add(p)
            s.commit()
