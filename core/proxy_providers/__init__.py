# coding=utf-8
"""
代理供应商包

新增代理商步骤：
1. 在此目录新建 <name>.py，继承 ProxyProvider
2. 在 proxy_pool.py 的 _build_providers() 中追加实例
"""
from .base import ProxyProvider

__all__ = ["ProxyProvider"]
