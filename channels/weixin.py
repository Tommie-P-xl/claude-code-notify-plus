"""微信通知渠道。通过 ilink Bot API 直接发送消息和扫码登录，不依赖 openclaw。"""

import json
import os
import sys
import random
import base64
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any
from .base import NotificationChannel
from .text import sanitize_text

SCRIPT_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


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

# 发送队列目录（用于跨进程 IPC，解决 iLink 协议会话绑定问题）
SEND_QUEUE_DIR = SCRIPT_DIR / "send_queue"


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

_keepalive_lock = threading.Lock()
_keepalive_thread = None
_keepalive_stop = threading.Event()
_keepalive_status = {
    "running": False,
    "last_ok": 0.0,
    "last_error": "",
}


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
        """通过 ilink Bot API 发送微信消息。

        当 keepalive 轮询正在运行时，消息通过文件队列转发给 keepalive 进程发送，
        避免跨进程 HTTP 连接导致 iLink 协议 ret=-2 拒绝。
        当 keepalive 未运行时，回退到直接发送。
        """
        bot_token = self._wx_config.get("bot_token", "")
        to_user_id = self._wx_config.get("to_user_id", "")

        if not bot_token:
            _log("[weixin] send 失败: bot_token 为空，请重新扫码登录")
            return False
        if not to_user_id:
            _log("[weixin] send 跳过: to_user_id 为空，请先在微信上给 bot 发一条消息以自动获取")
            return False

        # 优先通过队列发送（keepalive 进程内执行，保持会话绑定一致）
        if _is_keepalive_running():
            msg_id = _enqueue_message(title, message)
            _log(f"[weixin] 消息已入队: {msg_id}")
            return _wait_for_send_result(msg_id)

        # 回退：keepalive 未运行时直接发送
        _log("[weixin] keepalive 未运行，回退到直接发送")
        return _direct_send(self._wx_config, title, message)

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
        stop_keepalive()
        return {"ok": True, "message": "微信登录信息已清除"}


def _is_stale_context_error(ret: Any, errcode: Any, errmsg: Any) -> bool:
    """识别 iLink 将 context_token 过期伪装成 ret=-2 的场景。"""
    try:
        code = int(ret if ret not in (None, 0) else errcode)
    except (TypeError, ValueError):
        code = 0
    if code != -2:
        return False
    msg = (errmsg or "").strip().lower()
    return msg in ("", "unknown error", "invalid context token", "context token expired")


def _load_config_file() -> dict:
    try:
        cfg_file = SCRIPT_DIR / "config.json"
        if cfg_file.exists():
            return json.loads(cfg_file.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_config_file(cfg: dict) -> None:
    cfg_file = SCRIPT_DIR / "config.json"
    tmp = cfg_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, cfg_file)


def _update_config_field(key: str, value: Any) -> None:
    try:
        cfg = _load_config_file()
        wx = cfg.setdefault("weixin", {})
        if wx.get(key) == value:
            return
        wx[key] = value
        _save_config_file(cfg)
    except Exception as exc:
        _log(f"[weixin] 更新 config.{key} 失败: {exc}")


def _mark_session_timeout() -> None:
    try:
        cfg = _load_config_file()
        wx = cfg.setdefault("weixin", {})
        wx["enabled"] = False
        wx["context_token"] = ""
        wx["session_expired"] = True
        _save_config_file(cfg)
    except Exception:
        pass
    stop_keepalive()


def _random_wechat_uin() -> str:
    """生成随机的 X-WECHAT-UIN 头"""
    uint32 = random.randint(0, 2**32 - 1)
    return base64.b64encode(str(uint32).encode("utf-8")).decode("utf-8")


def _enqueue_message(title: str, message: str) -> str:
    """将消息写入文件队列，返回消息 ID。由 notify 进程调用。"""
    SEND_QUEUE_DIR.mkdir(exist_ok=True)
    msg_id = f"{int(time.time() * 1000)}-{random.randbytes(4).hex()}"
    queue_file = SEND_QUEUE_DIR / f"{msg_id}.json"
    payload = {
        "id": msg_id,
        "title": title,
        "message": message,
        "ts": time.time(),
        "status": "pending",
        "result": None,
    }
    tmp = queue_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, queue_file)
    return msg_id


def _wait_for_send_result(msg_id: str, timeout: float = 30.0) -> bool:
    """轮询等待 keepalive 进程处理结果。由 notify 进程调用。"""
    queue_file = SEND_QUEUE_DIR / f"{msg_id}.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data = json.loads(queue_file.read_text(encoding="utf-8"))
            if data.get("status") == "done":
                # 清理队列文件
                try:
                    queue_file.unlink(missing_ok=True)
                except Exception:
                    pass
                return bool(data.get("result"))
        except (FileNotFoundError, json.JSONDecodeError):
            return False
        time.sleep(0.3)
    return False


def _is_keepalive_running() -> bool:
    """检查 keepalive 轮询是否正在运行（同进程或跨进程）。"""
    # 同进程检查
    with _keepalive_lock:
        if _keepalive_status.get("running"):
            return True
    # 跨进程检查：通过 heartbeat 文件判断托盘进程是否存活
    try:
        hb_file = SCRIPT_DIR / "tray_heartbeat.json"
        if hb_file.exists():
            hb = json.loads(hb_file.read_text(encoding="utf-8"))
            if hb.get("weixin_keepalive") and time.time() - hb.get("ts", 0) < 30:
                return True
    except Exception:
        pass
    return False


def _direct_send(wx_config: dict, title: str, message: str) -> bool:
    """直接发送微信消息（回退模式，用于 keepalive 未运行时）。"""
    bot_token = wx_config.get("bot_token", "")
    baseurl = wx_config.get("baseurl", ILINK_BASE).rstrip("/")
    to_user_id = wx_config.get("to_user_id", "")
    context_token = wx_config.get("context_token", "")

    full_text = sanitize_text(f"【{title}】\n{message}")
    client_id = f"claude-notify:{int(time.time() * 1000)}-{random.randbytes(4).hex()}"

    def _build_body(with_ctx: bool) -> bytes:
        msg = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": client_id,
            "message_type": 2,
            "message_state": 2,
            "item_list": [{"type": 1, "text_item": {"text": full_text}}],
        }
        if with_ctx and context_token:
            msg["context_token"] = context_token
        return json.dumps({
            "msg": msg,
            "base_info": {"channel_version": CHANNEL_VERSION},
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    url = f"{baseurl}/ilink/bot/sendmessage"

    def _do_send(body: bytes) -> dict:
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
                        _log(f"[weixin] API 错误 ret={ret} errcode={errcode} errmsg={result.get('errmsg', '')}")
                        if _is_stale_context_error(ret, errcode, result.get("errmsg", "")):
                            return {"ok": False, "reason": "stale_context"}
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

    if result["reason"] == "session_timeout":
        _log("[weixin] bot session 过期 (errcode=-14)，请在 Web UI 重新扫码登录后继续使用")
        _mark_session_timeout()
        return False

    # context_token 过期或其他可恢复错误：剥离 context_token 降级重试一次
    if context_token:
        if result["reason"] == "stale_context":
            _log("[weixin] context_token 已过期，清空本地 token 并执行 tokenless fallback")
            _update_config_field("context_token", "")
        else:
            _log("[weixin] 发送失败，尝试不带 context_token 重试")
        body = _build_body(with_ctx=False)
        _log(f"[weixin] 重试请求体: {body.decode('utf-8', errors='replace')[:500]}")
        result = _do_send(body)
        return result["ok"]

    return False


def _process_send_queue(do_send_func) -> None:
    """处理发送队列中的 pending 消息。在 keepalive 进程内调用。"""
    if not SEND_QUEUE_DIR.exists():
        return
    for queue_file in sorted(SEND_QUEUE_DIR.glob("*.json")):
        if queue_file.suffix == ".tmp":
            continue
        try:
            data = json.loads(queue_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        if data.get("status") != "pending":
            continue
        # 超过 60 秒的队列消息视为过期
        if time.time() - data.get("ts", 0) > 60:
            _log(f"[weixin] 队列消息 {data.get('id', '?')} 已过期，丢弃")
            try:
                queue_file.unlink(missing_ok=True)
            except Exception:
                pass
            continue
        title = data.get("title", "")
        message = data.get("message", "")
        _log(f"[weixin] 处理队列消息: {title[:30]}")
        ok = do_send_func(title, message)
        data["status"] = "done"
        data["result"] = ok
        tmp = queue_file.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, queue_file)
        except Exception as exc:
            _log(f"[weixin] 写回队列结果失败: {exc}")
        if ok:
            _log(f"[weixin] 队列消息发送成功: {title[:30]}")
        else:
            _log(f"[weixin] 队列消息发送失败: {title[:30]}")


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


def start_keepalive() -> bool:
    """启动微信后台 getupdates 轮询，由托盘进程持有。"""
    global _keepalive_thread
    with _keepalive_lock:
        if _keepalive_thread and _keepalive_thread.is_alive():
            return True
        _keepalive_stop.clear()
        _keepalive_thread = threading.Thread(target=_keepalive_loop, name="weixin-keepalive", daemon=True)
        _keepalive_thread.start()
        return True


def stop_keepalive() -> None:
    _keepalive_stop.set()


def get_keepalive_status() -> Dict[str, Any]:
    with _keepalive_lock:
        return dict(_keepalive_status)


def _keepalive_loop() -> None:
    sync_buf = _load_config_file().get("weixin", {}).get("sync_buf", "")
    failure_count = 0
    _log("[weixin] 后台保活轮询启动")
    with _keepalive_lock:
        _keepalive_status.update({"running": True, "last_error": ""})

    try:
        while not _keepalive_stop.is_set():
            cfg = _load_config_file()
            wx = cfg.get("weixin", {})
            if not wx.get("enabled") or not wx.get("bot_token"):
                # 即使微信未启用，也处理队列中的消息（可能刚启用）
                time.sleep(5)
                continue

            # 处理发送队列（在同一进程内发送，保持会话绑定一致）
            _process_send_queue(lambda t, m: _direct_send(wx, t, m))

            token = wx.get("bot_token", "")
            baseurl = wx.get("baseurl", ILINK_BASE).rstrip("/")
            body = json.dumps({
                "get_updates_buf": sync_buf,
                "base_info": {"channel_version": CHANNEL_VERSION},
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
            req = urllib.request.Request(
                f"{baseurl}/ilink/bot/getupdates",
                data=body,
                headers=headers,
                method="POST",
            )

            try:
                resp = urllib.request.urlopen(req, timeout=40)
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
                ret = data.get("ret", data.get("errcode", 0))
                if ret == 0:
                    failure_count = 0
                    sync_buf = data.get("get_updates_buf", sync_buf) or sync_buf
                    _persist_sync_buf(sync_buf)
                    for msg in data.get("msgs", []):
                        _handle_incoming_message(msg)
                    with _keepalive_lock:
                        _keepalive_status.update({"last_ok": time.time(), "last_error": ""})
                elif ret == -14 or data.get("errcode") == -14:
                    _log("[weixin] 后台轮询检测到 session 过期，需要重新扫码")
                    _mark_session_timeout()
                    return
                elif ret == -2:
                    _log("[weixin] 后台轮询 ret=-2，清空 sync_buf 后继续")
                    sync_buf = ""
                    _persist_sync_buf(sync_buf)
                else:
                    failure_count += 1
                    with _keepalive_lock:
                        _keepalive_status["last_error"] = f"ret={ret}"
                    time.sleep(min(30, 2 ** min(failure_count, 5)))
            except Exception as exc:
                failure_count += 1
                with _keepalive_lock:
                    _keepalive_status["last_error"] = str(exc)
                time.sleep(min(30, 2 ** min(failure_count, 5)) + random.random())
    finally:
        with _keepalive_lock:
            _keepalive_status["running"] = False
        _log("[weixin] 后台保活轮询退出")


def _persist_sync_buf(sync_buf: str) -> None:
    try:
        cfg = _load_config_file()
        wx = cfg.setdefault("weixin", {})
        wx["sync_buf"] = sync_buf
        _save_config_file(cfg)
    except Exception:
        pass


def _handle_incoming_message(msg: dict) -> None:
    ctx = msg.get("context_token", "")
    if ctx:
        _update_config_field("context_token", ctx)
    from_user = msg.get("from_user_id", "")
    if from_user:
        _update_config_field("to_user_id", from_user)

    text = _extract_text(msg)
    if text:
        _dispatch_interaction_reply(text)


def _extract_text(msg: dict) -> str:
    texts = []
    for item in msg.get("item_list", []) or []:
        text_item = item.get("text_item") or {}
        if text_item.get("text"):
            texts.append(str(text_item["text"]))
    return "\n".join(texts).strip()


def _dispatch_interaction_reply(text: str) -> None:
    """由托盘常驻轮询直接接收用户回复，避免 hook 进程再开微信长轮询。"""
    try:
        import interaction
        label, reply = interaction._extract_reply_parts(text)
        pending = interaction.get_request_by_label(label) if label else interaction.get_latest_request()
        if not pending:
            return
        if label and label.upper() != pending.get("label", "").upper():
            return
        request_id = pending.get("id", "")
        if request_id:
            interaction.write_response(request_id, reply, "weixin", label=pending.get("label", ""))
    except Exception as exc:
        _log(f"[weixin] 分发交互回复失败: {exc}")


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
                start_keepalive()

                _log("[weixin] 登录成功，后台保活已启动")
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
