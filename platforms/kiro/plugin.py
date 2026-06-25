"""Kiro 平台插件 - 基于 AWS Builder ID 注册"""
import time
from core.base_platform import BasePlatform, Account, AccountStatus, RegisterConfig
from core.base_mailbox import BaseMailbox
from core.registry import register


@register
class KiroPlatform(BasePlatform):
    name = "kiro"
    display_name = "Kiro (AWS Builder ID)"
    version = "1.0.0"

    def __init__(self, config: RegisterConfig = None, mailbox: BaseMailbox = None):
        super().__init__(config)
        self.mailbox = mailbox

    def register(self, email: str, password: str = None) -> Account:
        from platforms.kiro.core import KiroRegister
        from core.config_store import config_store

        proxy = self.config.proxy
        laoudo_account_id = self.config.extra.get("laoudo_account_id", "")
        semi_auto = str(self.config.extra.get("semi_auto", "")).strip() in ("1", "true", "yes")

        # 从全局配置读取执行器类型，以决定是否无头模式
        default_executor = self.config.extra.get(
            "default_executor",
            config_store.get("default_executor", "headless"),
        )
        headless = default_executor != "headed"

        slow_multiplier = 1
        try:
            raw = str(self.config.extra.get("kiro_slow_mode_multiplier", "1")).strip()
            v = int(raw)
            if v >= 2:
                slow_multiplier = v
        except (ValueError, TypeError):
            pass

        reg = KiroRegister(proxy=proxy, tag="KIRO", headless=headless, slow_mode=slow_multiplier > 1, slow_multiplier=slow_multiplier)
        log_fn = getattr(self, '_log_fn', print)
        reg.log = lambda msg: log_fn(msg)

        # 把 task_control 注入 KiroRegister：
        #   1. 让 _stop_fn 走 task_control.is_stop_requested()（统一入口）
        #   2. _init_browser() 会向 task_control 注册 _force_close_browser 回调，
        #      stop_task API 调用 task_control.request_stop() 时会在后台线程
        #      立即关闭 browser，使所有阻塞的 Playwright 调用立即抛出异常
        _task_control = getattr(self, '_task_control', None)
        if _task_control is not None:
            reg._task_control = _task_control
            reg.set_stop_fn(_task_control.is_stop_requested)

        slow_label = f"（慢速 {slow_multiplier}x）" if slow_multiplier > 1 else ""
        log_fn(f"代理配置：{proxy or '直连（无代理）'}{slow_label}")

        otp_timeout = int(self.config.extra.get("otp_timeout", 300))

        if semi_auto:
            # 半自动模式：通过前端弹窗让用户手动输入验证码
            import uuid as _uuid
            task_id = getattr(self, '_task_id', None)
            slot = str(_uuid.uuid4())[:8]
            _task_control = getattr(self, '_task_control', None)

            def otp_cb():
                # 发送特殊日志标记，前端检测到后弹出输入框
                log_fn(f"[OTP_REQUIRED:{slot}] 请在弹窗中输入邮箱 {email} 收到的 6 位验证码（等待最多 {otp_timeout} 秒）")
                deadline = time.time() + otp_timeout
                while time.time() < deadline:
                    # 检查任务停止信号（协作式控制器）
                    if _task_control is not None and _task_control.is_stop_requested():
                        log_fn("[OTP_REQUIRED_STOPPED] 任务已停止，放弃等待验证码")
                        return None
                    # 检查旧式字典标志
                    if task_id:
                        from api.tasks import _tasks, _tasks_lock
                        with _tasks_lock:
                            task_data = _tasks.get(task_id, {})
                            if task_data.get("control", {}).get("stop_requested"):
                                log_fn("[OTP_REQUIRED_STOPPED] 任务已停止，放弃等待验证码")
                                return None
                            code = task_data.get("otp_slots", {}).get(slot)
                        if code:
                            log_fn(f"收到用户输入验证码: {code}")
                            return code
                    time.sleep(0.5)
                log_fn("[OTP_REQUIRED_TIMEOUT] 等待验证码超时")
                return None

        elif self.mailbox:
            mail_acct = self.mailbox.get_email()
            email = email or mail_acct.email
            log_fn(f"邮箱: {mail_acct.email}")
            _before = self.mailbox.get_current_ids(mail_acct)
            def otp_cb():
                log_fn("等待验证码...")
                code = self.mailbox.wait_for_code(
                    mail_acct,
                    keyword="builder id",
                    timeout=otp_timeout,
                    before_ids=_before,
                    code_pattern=r'(?is)(?:verification\s+code|验证码)[^0-9]{0,20}(\d{6})',
                )
                if code: log_fn(f"验证码: {code}")
                return code
        else:
            otp_cb = None

        ok, info = reg.register(
            email=email,
            pwd=password,
            name=self.config.extra.get("name", "Kiro User"),
            mail_token=laoudo_account_id or None,
            otp_timeout=otp_timeout,
            otp_callback=otp_cb,
        )

        if not ok:
            raise RuntimeError(f"Kiro 注册失败: {info.get('error')}")

        account = Account(
            platform="kiro",
            email=info["email"],
            password=info["password"],
            status=AccountStatus.REGISTERED,
            extra={
                "name": info.get("name", ""),
                "accessToken": info.get("accessToken", ""),
                "sessionToken": info.get("sessionToken", ""),
                "clientId": info.get("clientId", ""),
                "clientSecret": info.get("clientSecret", ""),
                "clientIdHash": info.get("clientIdHash", ""),
                "refreshToken": info.get("refreshToken", ""),
                "webAccessToken": info.get("webAccessToken", ""),
                "region": info.get("region", "us-east-1"),
                "provider": "BuilderId",
                "authMethod": "IdC",
            },
        )

        # 注册完成后检测账号是否被封禁，结果写入 extra 供 external_sync 判断
        access_token = info.get("accessToken", "")
        region = info.get("region", "us-east-1")
        if access_token:
            from platforms.kiro.switch import check_kiro_account_banned
            # 代理：只使用 .env 中的 PROXY_URL，不走代理池
            detect_proxy = config_store.get("PROXY_URL", "") or config_store.get("proxy_url", "") or None
            log_fn(f"【封禁检测】开始检测账号状态...（代理: {detect_proxy or '直连'}）")
            try:
                is_banned, detail = check_kiro_account_banned(
                    access_token=access_token,
                    region=region,
                    proxy=detect_proxy,
                )
                if is_banned:
                    log_fn(f"【封禁检测】⚠️  账号已被封禁！详情: {detail}")
                    account.status = AccountStatus.INVALID
                    account.extra["ban_detail"] = detail
                else:
                    log_fn(f"【封禁检测】✅ 账号状态正常。详情: {detail}")
            except Exception as e:
                log_fn(f"【封禁检测】检测异常（跳过，不影响导入）: {e}")
        else:
            log_fn("【封禁检测】缺少 accessToken，跳过检测")

        return account

    def check_valid(self, account: Account) -> bool:
        """通过 refreshToken 检测账号是否有效"""
        extra = account.extra or {}
        refresh_token = extra.get("refreshToken", "")
        if not refresh_token:
            return False
        try:
            from platforms.kiro.switch import refresh_kiro_token
            ok, _ = refresh_kiro_token(
                refresh_token,
                extra.get("clientId", ""),
                extra.get("clientSecret", ""),
            )
            return ok
        except Exception:
            return False

    def get_platform_actions(self) -> list:
        return [
            {"id": "switch_account", "label": "切换到桌面应用", "params": []},
            {"id": "refresh_token", "label": "刷新 Token", "params": []},
            {"id": "upload_kiro_manager", "label": "导入 Kiro Manager", "params": []},
        ]

    def execute_action(self, action_id: str, account: Account, params: dict) -> dict:
        extra = account.extra or {}

        if action_id == "switch_account":
            from platforms.kiro.switch import (
                refresh_kiro_token, switch_kiro_account, restart_kiro_ide,
            )
            from platforms.kiro.core import KiroRegister
            from core.base_mailbox import create_mailbox, MailboxAccount

            access_token = extra.get("accessToken", "") or account.token
            refresh_token = extra.get("refreshToken", "")
            client_id = extra.get("clientId", "")
            client_secret = extra.get("clientSecret", "")

            # Kiro 桌面端需要完整的 Builder ID SSO 缓存。
            # 只有 accessToken/sessionToken 的网页态账号无法稳定切到桌面应用。
            if not access_token:
                return {"ok": False, "error": "当前账号缺少 accessToken，无法切换到桌面应用"}
            if not refresh_token or not client_id or not client_secret:
                if account.email and account.password:
                    reg = KiroRegister(proxy=self.config.proxy, tag="KIRO-SWITCH")
                    reg.log = getattr(self, "_log_fn", print)
                    otp_callback = None
                    mailbox_extra = dict(self.config.extra or {})
                    for key in (
                        "mail_provider",
                        "luckmail_base_url",
                        "luckmail_project_code",
                        "luckmail_email_type",
                        "luckmail_domain",
                    ):
                        if extra.get(key) not in (None, ""):
                            mailbox_extra[key] = extra.get(key)

                    mail_provider = mailbox_extra.get("mail_provider", "")
                    if mail_provider:
                        try:
                            mailbox = create_mailbox(
                                provider=mail_provider,
                                extra=mailbox_extra,
                                proxy=self.config.proxy,
                            )
                            mail_account = MailboxAccount(
                                email=account.email,
                                account_id=extra.get("mailbox_token", ""),
                            )
                            before_ids = mailbox.get_current_ids(mail_account)

                            def _otp_cb():
                                reg.log("桌面授权等待邮箱验证码 ...")
                                try:
                                    code = mailbox.wait_for_code(
                                        mail_account,
                                        keyword="",
                                        timeout=45,
                                        before_ids=before_ids,
                                        code_pattern=r'(?is)(?:verification\s+code|验证码)[^0-9]{0,20}(\d{6})',
                                    )
                                except Exception:
                                    reg.log("未等到新验证码，回退读取最近一封身份验证邮件 ...")
                                    code = mailbox.wait_for_code(
                                        mail_account,
                                        keyword="",
                                        timeout=15,
                                        before_ids=None,
                                        code_pattern=r'(?is)(?:verification\s+code|验证码)[^0-9]{0,20}(\d{6})',
                                    )
                                if code:
                                    reg.log(f"桌面授权验证码: {code}")
                                return code

                            otp_callback = _otp_cb
                        except Exception:
                            otp_callback = None

                    ok, desktop_info = reg.fetch_desktop_tokens(
                        account.email,
                        account.password,
                        otp_callback=otp_callback,
                    )
                    if not ok:
                        return {
                            "ok": False,
                            "error": (
                                "当前账号缺少 refreshToken / clientId / clientSecret，"
                                f"且自动补抓桌面端 Token 失败: {desktop_info.get('error', 'unknown error')}"
                            ),
                        }
                    access_token = desktop_info.get("accessToken", "") or access_token
                    refresh_token = desktop_info.get("refreshToken", "")
                    client_id = desktop_info.get("clientId", "")
                    client_secret = desktop_info.get("clientSecret", "")
                else:
                    return {
                        "ok": False,
                        "error": (
                            "当前账号只有网页登录态，缺少 refreshToken / clientId / clientSecret，"
                            "并且没有可用的邮箱/密码用于自动补抓桌面端 Token。"
                        ),
                    }

            if refresh_token and client_id and client_secret:
                ok, result = refresh_kiro_token(refresh_token, client_id, client_secret)
                if ok:
                    access_token = result["accessToken"]
                    refresh_token = result.get("refreshToken", refresh_token)

            ok, msg = switch_kiro_account(
                access_token=access_token,
                refresh_token=refresh_token,
                client_id=client_id,
                client_secret=client_secret,
            )
            if not ok:
                return {"ok": False, "error": msg}

            restart_ok, restart_msg = restart_kiro_ide()
            return {"ok": True, "data": {
                "accessToken": access_token,
                "refreshToken": refresh_token,
                "clientId": client_id,
                "clientSecret": client_secret,
                "message": f"{msg}。{restart_msg}" if restart_ok else msg,
            }}

        elif action_id == "refresh_token":
            from platforms.kiro.switch import refresh_kiro_token

            refresh_token = extra.get("refreshToken", "")
            client_id = extra.get("clientId", "")
            client_secret = extra.get("clientSecret", "")

            ok, result = refresh_kiro_token(refresh_token, client_id, client_secret)
            if ok:
                new_access = result["accessToken"]
                new_refresh = result.get("refreshToken", refresh_token)
                return {
                    "ok": True,
                    "data": {
                        "access_token": new_access,
                        "accessToken": new_access,
                        "refreshToken": new_refresh,
                    },
                }
            return {"ok": False, "error": result.get("error", "刷新失败")}

        elif action_id == "upload_kiro_manager":
            from platforms.kiro.account_manager_upload import upload_to_kiro_manager
            import copy
            # 手动导入不受封禁检测影响，强制以 active 状态写入
            acc_copy = copy.deepcopy(account)
            acc_copy.status = AccountStatus.REGISTERED
            if isinstance(acc_copy.extra, dict):
                acc_copy.extra.pop("ban_detail", None)
            ok, msg = upload_to_kiro_manager(acc_copy)
            return {"ok": ok, "data": {"message": msg}}

        raise NotImplementedError(f"未知操作: {action_id}")
