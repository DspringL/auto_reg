# coding=utf-8
"""
ProxyProvider 抽象基类

每个代理供应商实现此接口，ProxyPool 只面向接口编程。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class ProxyProvider(ABC):
    """
    代理供应商协议。

    最小实现：name、is_enabled()、get_proxy()。
    可选重写：report_result()，用于记录成功/失败统计。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """供应商唯一标识，用于日志和路由 report_result。"""
        ...

    @abstractmethod
    def is_enabled(self) -> bool:
        """当前供应商是否可用（配置是否齐全且已开启）。"""
        ...

    @abstractmethod
    def get_proxy(self, region: str = "") -> Optional[str]:
        """
        获取一个代理 URL。

        Args:
            region: 可选的区域过滤（如 "US"），供应商不支持时忽略即可。

        Returns:
            代理 URL 字符串（如 "http://1.2.3.4:8080"），无可用代理时返回 None。

        Raises:
            Exception: 获取失败时抛出，ProxyPool 会捕获并降级到下一个供应商。
        """
        ...

    def report_result(self, url: str, success: bool) -> None:
        """
        上报本次代理使用结果，用于成功率统计和自动禁用。
        默认空实现，有需要的供应商自行重写。
        """
