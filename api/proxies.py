from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import Optional
from core.db import ProxyModel, get_session
from core.proxy_pool import proxy_pool

router = APIRouter(prefix="/proxies", tags=["proxies"])


class ProxyCreate(BaseModel):
    url: str
    region: str = ""


class ProxyBulkCreate(BaseModel):
    proxies: list[str]
    region: str = ""


@router.get("")
def list_proxies(session: Session = Depends(get_session)):
    items = session.exec(select(ProxyModel)).all()
    return items


@router.post("")
def add_proxy(body: ProxyCreate, session: Session = Depends(get_session)):
    existing = session.exec(select(ProxyModel).where(ProxyModel.url == body.url)).first()
    if existing:
        raise HTTPException(400, "代理已存在")
    p = ProxyModel(url=body.url, region=body.region)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@router.post("/bulk")
def bulk_add_proxies(body: ProxyBulkCreate, session: Session = Depends(get_session)):
    added = 0
    for url in body.proxies:
        url = url.strip()
        if not url:
            continue
        existing = session.exec(select(ProxyModel).where(ProxyModel.url == url)).first()
        if not existing:
            session.add(ProxyModel(url=url, region=body.region))
            added += 1
    session.commit()
    return {"added": added}


@router.delete("/{proxy_id}")
def delete_proxy(proxy_id: int, session: Session = Depends(get_session)):
    p = session.get(ProxyModel, proxy_id)
    if not p:
        raise HTTPException(404, "代理不存在")
    session.delete(p)
    session.commit()
    return {"ok": True}


@router.patch("/{proxy_id}/toggle")
def toggle_proxy(proxy_id: int, session: Session = Depends(get_session)):
    p = session.get(ProxyModel, proxy_id)
    if not p:
        raise HTTPException(404, "代理不存在")
    p.is_active = not p.is_active
    session.add(p)
    session.commit()
    return {"is_active": p.is_active}


@router.post("/check")
def check_proxies(background_tasks: BackgroundTasks):
    background_tasks.add_task(proxy_pool.check_all)
    return {"message": "检测任务已启动"}


# ---------------------------------------------------------------------------
# 动态代理供应商端点（通用）
# ---------------------------------------------------------------------------

def _get_shenlong_provider():
    """从 proxy_pool 中取出 ShenlongProvider 实例。"""
    from core.proxy_providers.shenlong import ShenlongProvider
    for p in proxy_pool._providers:
        if isinstance(p, ShenlongProvider):
            return p
    return ShenlongProvider()  # 降级：临时实例


@router.get("/providers")
def list_providers():
    """列出所有已注册的代理供应商及其状态。"""
    return [
        {"name": p.name, "enabled": p.is_enabled()}
        for p in proxy_pool._providers
    ]


# ── 神龙供应商端点（保持向后兼容的路径 /shenlong/*）──────────────────────

@router.get("/shenlong/status")
def shenlong_status():
    """神龙代理当前配置状态（不暴露密钥明文）。"""
    p = _get_shenlong_provider()
    from core.proxy_providers.shenlong import _cfg
    api_key = _cfg("SHENLONG_API_KEY", "")
    return {
        "enabled": p.is_enabled(),
        "api_key_set": bool(api_key),
        "country": _cfg("SHENLONG_COUNTRY", "US"),
        "protocol": _cfg("SHENLONG_PROTOCOL", "http"),
        "fetch_count": _cfg("SHENLONG_FETCH_COUNT", "10"),
        "ip_ttl": _cfg("SHENLONG_IP_TTL", "30"),
        "cache_size": len(p._cache.lines) if p._cache else 0,
    }


@router.post("/shenlong/fetch")
def shenlong_fetch(count: int = 10):
    """
    从神龙 API 实时拉取代理列表（同时刷新内部缓存），返回样本。
    不写入数据库，用于验证配置和网络连通性。
    """
    p = _get_shenlong_provider()
    if not p.is_enabled():
        raise HTTPException(400, "神龙代理未启用，请先配置 SHENLONG_ENABLED=true 及 SHENLONG_API_KEY")
    try:
        lines = p.fetch_list(count=count)
    except (ValueError, ConnectionError) as e:
        raise HTTPException(502, f"神龙 API 调用失败：{e}")
    return {
        "fetched_count": len(lines),
        "sample": lines[:5],
    }


@router.post("/shenlong/verify")
def shenlong_verify():
    """从神龙获取一个代理并验证连通性，返回出口 IP。"""
    p = _get_shenlong_provider()
    if not p.is_enabled():
        raise HTTPException(400, "神龙代理未启用，请先配置 SHENLONG_ENABLED=true 及 SHENLONG_API_KEY")
    try:
        proxy_url = p.get_proxy()
        if not proxy_url:
            raise HTTPException(502, "神龙代理列表为空，请检查 API Key 和网络")
        result = p.verify(proxy_url)
    except HTTPException:
        raise
    except (ValueError, ConnectionError) as e:
        raise HTTPException(502, f"神龙代理验证失败：{e}")
    return {
        "proxy_url": proxy_url,
        "exit_ip_info": result,
    }
