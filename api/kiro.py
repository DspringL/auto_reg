# coding=utf-8
"""
Kiro 平台专属 API
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from urllib.parse import urlparse, parse_qs

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/kiro", tags=["kiro"])


class CompleteCallbackRequest(BaseModel):
    callback_url: str
    email: str | None = None        # 可选，覆盖 JWT 里解析的 email
    password: str | None = None     # 可选，保存账号用的密码
    task_id: str | None = None      # 注册任务 ID（用于取 code_verifier）
    slot: str | None = None         # callback slot（配合 task_id 使用）


@router.post("/complete-callback")
def complete_callback(body: CompleteCallbackRequest):
    """
    从浏览器地址栏的 callback URL 换取 Kiro 桌面端 Token 并保存账号。

    两种使用场景：
    A) 注册任务中路由拦截失败：传 task_id + slot，后端自动取 code_verifier
    B) 独立手动导入按钮：只传 callback_url，后端尝试无 PKCE 方式换 token
    """
    url = (body.callback_url or "").strip()
    if not url:
        raise HTTPException(400, "callback_url 不能为空")

    # ── 1. 解析 URL 提取 code ─────────────────────────────────────────────
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
    except Exception as e:
        raise HTTPException(400, f"URL 解析失败：{e}")

    code = (params.get("code") or [None])[0]
    if not code:
        raise HTTPException(400, "URL 中缺少 code 参数，请粘贴完整的 callback URL")

    # ── 2. 取 code_verifier ───────────────────────────────────────────────
    code_verifier = ""
    client_override: dict | None = None  # 如果任务里存了 client 信息也一并取出

    if body.task_id and body.slot:
        # 从注册任务状态里取
        try:
            from api.tasks import _tasks, _tasks_lock
            with _tasks_lock:
                task_data = _tasks.get(body.task_id, {})
                pending = task_data.get("pending_callbacks", {}).get(body.slot, {})
                code_verifier = pending.get("code_verifier", "")
                client_override = pending.get("client_info")
        except Exception:
            pass

    if not code_verifier:
        # 独立模式：生成一个随机 verifier，AWS OIDC 有时对 public client 不强制校验
        # 若仍然失败则说明必须用原始 verifier，此时只能走任务内联模式
        code_verifier = uuid.uuid4().hex + uuid.uuid4().hex
        logger.warning("complete-callback: 无 code_verifier，使用随机值，可能因 PKCE 校验失败")

    # ── 3. 注册桌面端 OIDC Client ─────────────────────────────────────────
    from platforms.kiro.core import KiroRegister, KIRO_IDC_REGION

    outbound = (
        os.environ.get("LOCAL_OUTBOUND_PROXY", "")
        or os.environ.get("PROXY_URL", "")
    ).strip() or None

    reg = KiroRegister(proxy=outbound, tag="KIRO-CALLBACK")

    if client_override:
        client_info = client_override
    else:
        try:
            client_info = reg._register_desktop_client(KIRO_IDC_REGION)
        except Exception as e:
            raise HTTPException(502, f"注册桌面端 OIDC Client 失败：{e}")

    # ── 4. 用 code 换取 Token ─────────────────────────────────────────────
    redirect_uri = "http://127.0.0.1/oauth/callback"
    try:
        desktop_token = reg._exchange_desktop_token(
            region=KIRO_IDC_REGION,
            client_id=client_info["clientId"],
            client_secret=client_info["clientSecret"],
            redirect_uri=redirect_uri,
            code=code,
            code_verifier=code_verifier,
        )
    except Exception as e:
        error_msg = str(e)
        if any(k in error_msg.lower() for k in ("expired", "invalid", "authorization_code", "pkce")):
            raise HTTPException(
                502,
                "code 已过期或 PKCE 校验失败。"
                "请在浏览器出现 callback URL 后的 60 秒内提交，或通过注册任务中的弹窗提交。"
                f"详情：{error_msg}"
            )
        raise HTTPException(502, f"换取 Token 失败：{error_msg}")

    access_token = desktop_token.get("accessToken", "")
    refresh_token = desktop_token.get("refreshToken", "")
    if not access_token:
        raise HTTPException(502, "Token exchange 成功但未返回 accessToken")

    # ── 5. 解析 email ─────────────────────────────────────────────────────
    email = body.email or ""
    if not email:
        try:
            import base64 as _b64
            payload_b64 = access_token.split(".")[1]
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            payload = json.loads(_b64.urlsafe_b64decode(payload_b64))
            email = (
                payload.get("username")
                or payload.get("sub")
                or payload.get("email")
                or ""
            )
        except Exception:
            email = ""

    if not email:
        raise HTTPException(
            422,
            "无法从 Token 中解析 email，请在请求体中手动传入 email 字段"
        )

    # ── 6. 保存账号 ───────────────────────────────────────────────────────
    password = body.password or f"Aa!1{uuid.uuid4().hex[:8]}"
    from core.db import save_account
    from core.base_platform import Account, AccountStatus

    account = Account(
        email=email,
        password=password,
        platform="kiro",
        status=AccountStatus.registered,
        token=access_token,
        extra={
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "clientId": client_info["clientId"],
            "clientSecret": client_info["clientSecret"],
            "clientIdHash": client_info.get("clientIdHash", ""),
            "region": KIRO_IDC_REGION,
            "source": "callback_import",
        },
    )

    try:
        saved = save_account(account)
    except Exception as e:
        raise HTTPException(500, f"账号保存失败：{e}")

    logger.info("Kiro callback 导入成功：%s", email)
    return {
        "ok": True,
        "email": email,
        "account_id": getattr(saved, "id", None),
        "has_refresh_token": bool(refresh_token),
    }
