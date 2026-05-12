"""微信通知渠道。通过 ilink Bot API 直接发送消息和扫码登录，不依赖 openclaw。"""

import json
import os
import random
import base64
import threading
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any
from .base import NotificationChannel

SCRIPT_DIR = Path(__file__).resolve().parent.parent


def _log(msg: str):
    from datetime import datetime
    log_file = SCRIPT_DIR / "notify.log"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass

ILINK_BASE = "https://ilinkai.weixin.qq.com"

# iLink API 常量（与 Hermes Agent 保持一致）
CHANNEL_VERSION = "2.2.0"


# 全局登录状态（线程安全）
_login_state = {
    "in_progress": False,
    "qr_img_url": None,      # 二维码图片 URL
    "status": "idle",         # idle / wait / scaned / confirmed / expired / error
    "error": None,
    "bot_token": None,
    "baseurl": None,
    "ilink_bot_id": None,
    "ilink_user_id": None,
}
_login_lock = threading.Lock()
_login_thread = None


class WeixinChannel(NotificationChannel):
    """通过 ilink Bot API 向微信发送消息"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._wx_config = config.get("weixin", {})

    @property
    def name(self) -> str:
        return "weixin"

    def is_enabled(self) -> bool:
        return self._wx_config.get("enabled", False)

    def send(self, title: str, message: str) -> bool:
        """通过 ilink Bot API 发送微信消息"""
        bot_token = self._wx_config.get("bot_token", "")
        baseurl = self._wx_config.get("baseurl", ILINK_BASE).rstrip("/")
        to_user_id = self._wx_config.get("to_user_id", "")
        context_token = self._wx_config.get("context_token", "")

        if not bot_token:
            _log("[weixin] send 失败: bot_token 为空，请重新扫码登录")
            return False
        if not to_user_id:
            _log("[weixin] send 跳过: to_user_id 为空，请先在微信上给 bot 发一条消息以自动获取")
            return False

        full_text = f"【{title}】\n{message}"

        import time as _time
        client_id = f"claude-notify:{int(_time.time()*1000)}-{random.randbytes(4).hex()}"

        def _build_body(with_ctx: bool) -> bytes:
            msg = {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "item_list": [
                    {
                        "type": 1,
                        "text_item": {"text": full_text}
                    }
                ],
            }
            if with_ctx and context_token:
                msg["context_token"] = context_token
            return json.dumps({
                "msg": msg,
                "base_info": {
                    "channel_version": CHANNEL_VERSION,
                }
            }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        url = f"{baseurl}/ilink/bot/sendmessage"

        def _do_send(body: bytes) -> dict:
            """
            发送请求，返回解析后的 API 响应 dict。
            - 成功: {"ok": True}
            - errcode=-14: {"ok": False, "reason": "session_timeout"}
            - 其他错误: {"ok": False, "reason": "api_error"}
            - 网络异常: {"ok": False, "reason": "network_error"}
            """
            headers = {
                "Content-Type": "application/json",
                "AuthorizationType": "ilink_bot_token",
                "Authorization": f"Bearer {bot_token}",
                "X-WECHAT-UIN": _random_wechat_uin(),
                "iLink-App-Id": "bot",
                "iLink-App-ClientVersion": str((2 << 16) | (2 << 8) | 0),
                "Content-Length": str(len(body)),
            }
            _log(f"[weixin] 请求头: {json.dumps({k: v for k, v in headers.items() if k != 'Authorization'}, ensure_ascii=False)}")
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                resp = urllib.request.urlopen(req, timeout=15)
                resp_body = resp.read().decode("utf-8", errors="replace")
                _log(f"[weixin] HTTP {resp.status} | 响应: {resp_body[:300]}")
                if 200 <= resp.status < 300:
                    try:
                        result = json.loads(resp_body)
                        ret = result.get("ret", 0)
                        errcode = result.get("errcode", 0)
                        if ret != 0 or errcode != 0:
                            if errcode == -14 or ret == -14:
                                return {"ok": False, "reason": "session_timeout"}
                            # ret=-2: context_token 过期，降级为 api_error 让调用方丢弃 token 重试
                            _log(f"[weixin] API 错误 ret={ret} errcode={errcode} errmsg={result.get('errmsg', '')}")
                            return {"ok": False, "reason": "api_error"}
                    except (json.JSONDecodeError, AttributeError):
                        pass
                    return {"ok": True}
                return {"ok": False, "reason": "api_error"}
            except urllib.error.HTTPError as e:
                resp_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
                _log(f"[weixin] HTTPError {e.code} | 响应: {resp_body[:300]}")
                return {"ok": False, "reason": "network_error"}
            except urllib.error.URLError as e:
                _log(f"[weixin] URLError: {e.reason}")
                return {"ok": False, "reason": "network_error"}
            except Exception as e:
                _log(f"[weixin] 异常: {e}")
                return {"ok": False, "reason": "network_error"}

        # 第一次发送（带 context_token）
        body = _build_body(with_ctx=True)
        _log(f"[weixin] POST {url} to_user={to_user_id} ctx={'yes' if context_token else 'no'}")
        _log(f"[weixin] 请求体: {body.decode('utf-8', errors='replace')[:500]}")
        result = _do_send(body)

        if result["ok"]:
            return True

        # errcode=-14: bot session 过期
        # 不带 context_token 重试仍会失败，需要重新登录
        if result["reason"] == "session_timeout":
            _log("[weixin] bot session 过期 (errcode=-14)，请在 Web UI 重新扫码登录后继续使用")
            return False

        # 其他错误：尝试不带 context_token 重试一次
        if context_token:
            _log("[weixin] 发送失败，尝试不带 context_token 重试")
            body = _build_body(with_ctx=False)
            _log(f"[weixin] 重试请求体: {body.decode('utf-8', errors='replace')[:500]}")
            result = _do_send(body)
            return result["ok"]

        return False

    @staticmethod
    def get_login_status(config: Dict[str, Any]) -> Dict[str, Any]:
        """获取微信登录状态"""
        wx = config.get("weixin", {})
        bot_token = wx.get("bot_token", "")
        ilink_user_id = wx.get("ilink_user_id", "")
        baseurl = wx.get("baseurl", "")

        return {
            "logged_in": bool(bot_token),
            "ilink_user_id": ilink_user_id or None,
            "baseurl": baseurl or None,
            "message": "微信已登录" if bot_token else "未登录，请扫码",
        }

    @staticmethod
    def start_qr_login() -> Dict[str, Any]:
        """启动微信扫码登录流程（直接调用 ilink API）"""
        global _login_thread
        with _login_lock:
            if _login_state["in_progress"]:
                return {"ok": False, "error": "登录流程已在进行中"}
            _login_state.update({
                "in_progress": True,
                "qr_img_url": None,
                "status": "wait",
                "error": None,
                "bot_token": None,
                "baseurl": None,
                "ilink_bot_id": None,
                "ilink_user_id": None,
            })

        def _do_login():
            _qr_login_loop()

        _login_thread = threading.Thread(target=_do_login, daemon=True)
        _login_thread.start()
        return {"ok": True, "message": "扫码登录流程已启动"}

    @staticmethod
    def get_qr_status() -> Dict[str, Any]:
        """获取扫码登录状态"""
        with _login_lock:
            return dict(_login_state)

    @staticmethod
    def clear_login() -> Dict[str, Any]:
        """清除微信登录信息"""
        return {"ok": True, "message": "微信登录信息已清除"}


def _random_wechat_uin() -> str:
    """生成随机的 X-WECHAT-UIN 头"""
    uint32 = random.randint(0, 2**32 - 1)
    return base64.b64encode(str(uint32).encode("utf-8")).decode("utf-8")


def _fetch_qr_code() -> Dict[str, Any]:
    """从 ilink API 获取二维码"""
    url = f"{ILINK_BASE}/ilink/bot/get_bot_qrcode?bot_type=3"
    headers = {
        "Content-Type": "application/json",
        "iLink-App-ClientVersion": "1",
    }
    req = urllib.request.Request(url, method="GET")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode("utf-8"))
        return {
            "ok": True,
            "qrcode": data.get("qrcode", ""),
            "qr_img_url": data.get("qrcode_img_content", ""),
        }
    except Exception as e:
        return {"ok": False, "error": f"获取二维码失败: {e}"}


def _poll_qr_status(qrcode_token: str, base_url: str = ILINK_BASE) -> Dict[str, Any]:
    """轮询扫码状态（长轮询，35秒超时）"""
    url = f"{base_url}/ilink/bot/get_qrcode_status?qrcode={urllib.request.quote(qrcode_token)}"
    headers = {
        "Content-Type": "application/json",
        "iLink-App-ClientVersion": "1",
    }
    req = urllib.request.Request(url, method="GET")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=40)
        data = json.loads(resp.read().decode("utf-8"))
        return data
    except urllib.error.URLError:
        return {"status": "wait"}
    except Exception:
        return {"status": "wait"}


def _init_session_after_login(token: str, baseurl: str):
    """QR 登录成功后调用 getupdates 初始化 session，获取 context_token"""
    import time as _time
    body = json.dumps({
        "get_updates_buf": "",
        "base_info": {"channel_version": CHANNEL_VERSION}
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {token}",
        "X-WECHAT-UIN": _random_wechat_uin(),
        "iLink-App-Id": "bot",
        "iLink-App-ClientVersion": str((2 << 16) | (2 << 8) | 0),
        "Content-Length": str(len(body)),
    }
    url = f"{baseurl.rstrip('/')}/ilink/bot/getupdates"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=40)
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
        ret = data.get("ret", data.get("errcode", 0))
        if ret == 0:
            _log("[weixin] getupdates 初始化成功")
            from notify import load_config, save_config
            cfg = load_config()
            for msg in data.get("msgs", []):
                # 提取 context_token
                ctx = msg.get("context_token", "")
                if ctx and not cfg["weixin"].get("context_token"):
                    cfg["weixin"]["context_token"] = ctx
                    _log(f"[weixin] 获取到 context_token")
                # 提取发送者 ID 作为 to_user_id
                from_user = msg.get("from_user_id", "")
                if from_user and not cfg["weixin"].get("to_user_id"):
                    cfg["weixin"]["to_user_id"] = from_user
                    _log(f"[weixin] 获取到 to_user_id: {from_user}")
            save_config(cfg)
        else:
            _log(f"[weixin] getupdates 初始化失败 ret={ret}")
    except Exception as e:
        _log(f"[weixin] getupdates 初始化异常: {e}")


def _qr_login_loop():
    """完整的扫码登录循环"""
    max_retries = 3

    for attempt in range(max_retries):
        # Step 1: 获取二维码
        qr_result = _fetch_qr_code()
        if not qr_result["ok"]:
            with _login_lock:
                _login_state["error"] = qr_result["error"]
                _login_state["status"] = "error"
                _login_state["in_progress"] = False
            return

        qrcode_token = qr_result["qrcode"]
        qr_img_url = qr_result["qr_img_url"]

        with _login_lock:
            _login_state["qr_img_url"] = qr_img_url
            _login_state["status"] = "wait"

        # Step 2: 轮询状态
        poll_base = ILINK_BASE
        while True:
            result = _poll_qr_status(qrcode_token, poll_base)
            status = result.get("status", "wait")

            if status == "scaned":
                with _login_lock:
                    _login_state["status"] = "scaned"

            elif status == "scaned_but_redirect":
                redirect_host = result.get("redirect_host", "")
                if redirect_host:
                    poll_base = f"https://{redirect_host}"
                with _login_lock:
                    _login_state["status"] = "scaned"

            elif status == "confirmed":
                bot_token = result.get("bot_token", "")
                baseurl = result.get("baseurl", poll_base)
                ilink_bot_id = result.get("ilink_bot_id", "")
                ilink_user_id = result.get("ilink_user_id", "")

                with _login_lock:
                    _login_state["status"] = "confirmed"
                    _login_state["bot_token"] = bot_token
                    _login_state["baseurl"] = baseurl
                    _login_state["ilink_bot_id"] = ilink_bot_id
                    _login_state["ilink_user_id"] = ilink_user_id
                    _login_state["in_progress"] = False

                # 调用 getupdates 初始化 session 并获取 context_token
                _init_session_after_login(bot_token, baseurl)

                _log("[weixin] 登录成功（注意：session 不自动保活，长时间不用需重新扫码）")
                return

            elif status == "expired":
                if attempt < max_retries - 1:
                    with _login_lock:
                        _login_state["status"] = "wait"
                        _login_state["qr_img_url"] = None
                    break  # 外层循环重试
                else:
                    with _login_lock:
                        _login_state["error"] = "二维码已过期，重试次数已用完"
                        _login_state["status"] = "expired"
                        _login_state["in_progress"] = False
                    return

            elif status == "wait":
                pass  # 继续轮询

            else:
                pass  # 未知状态，继续轮询

    with _login_lock:
        _login_state["error"] = "登录失败"
        _login_state["status"] = "error"
        _login_state["in_progress"] = False
