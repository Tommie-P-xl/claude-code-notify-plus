#!/usr/bin/env python3
"""
Session 保活守护进程。
- 微信：通过 getupdates 长轮询保持 session 存活，获取 context_token
- QQ：通过 WebSocket 监听消息事件，自动获取 user_openid
"""

import asyncio
import json
import os
import sys
import signal
import time
import random
import base64
import threading
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.json"

# 交互功能：处理用户回复消息
PENDING_DIR = SCRIPT_DIR / "pending"
RESPONSE_DIR = SCRIPT_DIR / "responses"
PID_FILE = SCRIPT_DIR / "keepalive.pid"
LOG_FILE = SCRIPT_DIR / "notify.log"

# 微信 iLink API 常量
WEIXIN_BASE = "https://ilinkai.weixin.qq.com"
WEIXIN_CHANNEL_VERSION = "2.3.1"

# QQ Bot API 常量
QQ_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
QQ_API_BASE = "https://api.sgroup.qq.com"
QQ_INTENT_GROUP_AND_C2C = 1 << 25  # 订阅群聊和单聊消息事件

KEEPALIVE_POLL_TIMEOUT = 35
MAX_CONSECUTIVE_FAILURES = 3


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [keepalive] {msg}\n")
    except Exception:
        pass


def load_config() -> dict:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def write_pid():
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def remove_pid():
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def is_already_running() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except (ValueError, OSError, ProcessLookupError):
        return False


def random_wechat_uin() -> str:
    uint32 = random.randint(0, 2**32 - 1)
    return base64.b64encode(str(uint32).encode("utf-8")).decode("utf-8")


# ========== 微信保活 ==========

# ── 交互消息处理 ────────────────────────────────────────

def _extract_reply_parts(text: str) -> tuple:
    """
    从用户消息中提取请求标签和选项。
    - "A 1" → ("A", "1")
    - "1" → ("", "1")
    - "a1" → ("A", "1")
    """
    text = text.strip()
    if not text:
        return ("", "")

    if len(text) >= 3 and text[0].isalpha() and text[1] == " ":
        return (text[0].upper(), text[2:].strip())

    if len(text) >= 2 and text[0].isalpha() and not text[1:].isalpha():
        return (text[0].upper(), text[1:].strip())

    return ("", text)


def _process_incoming_message(text: str, channel: str):
    """
    处理收到的消息，匹配 pending 请求并写入 response。
    """
    if not PENDING_DIR.exists():
        return
    pending_files = list(PENDING_DIR.glob("*.json"))
    if not pending_files:
        return

    label, reply = _extract_reply_parts(text)
    if not reply:
        return

    # 找到目标 pending 请求
    target_pending = None
    if label:
        for pf in pending_files:
            try:
                req = json.loads(pf.read_text(encoding="utf-8"))
                if req.get("label", "").upper() == label:
                    target_pending = req
                    break
            except Exception:
                continue
    if not target_pending:
        pending_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        try:
            target_pending = json.loads(pending_files[0].read_text(encoding="utf-8"))
        except Exception:
            return

    if not target_pending:
        return

    request_id = target_pending["id"]

    # 原子写入 response
    resp_file = RESPONSE_DIR / f"{request_id}.json"
    if resp_file.exists():
        return

    import tempfile
    response = {
        "request_id": request_id,
        "reply": reply,
        "channel": channel,
        "received_at": time.time(),
    }

    try:
        RESPONSE_DIR.mkdir(exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(RESPONSE_DIR), suffix=".tmp")
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(response, f, ensure_ascii=False)
        os.rename(tmp_path, str(resp_file))
        log(f"[{channel}] 交互回复: {text[:50]} → {request_id}")
    except FileExistsError:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    except Exception as e:
        log(f"[{channel}] 写入 response 失败: {e}")
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def weixin_keepalive_loop():
    """微信 getupdates 长轮询保持 session"""
    consecutive_failures = 0
    sync_buf = ""

    while True:
        cfg = load_config()
        wx = cfg.get("weixin", {})
        token = wx.get("bot_token", "")
        baseurl = wx.get("baseurl", WEIXIN_BASE)
        enabled = wx.get("enabled", False)

        if not enabled or not token:
            log("微信未启用或配置不完整，微信保活退出")
            break

        body = json.dumps({
            "get_updates_buf": sync_buf,
            "base_info": {"channel_version": WEIXIN_CHANNEL_VERSION}
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {token}",
            "X-WECHAT-UIN": random_wechat_uin(),
            "Content-Length": str(len(body)),
        }

        url = f"{baseurl.rstrip('/')}/ilink/bot/getupdates"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")

        try:
            resp = urllib.request.urlopen(req, timeout=KEEPALIVE_POLL_TIMEOUT + 5)
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            ret = data.get("ret", 0)
            errcode = data.get("errcode", 0)

            if ret == 0 and errcode == 0:
                new_buf = data.get("get_updates_buf", "")
                if new_buf:
                    sync_buf = new_buf

                msgs = data.get("msgs", [])
                if msgs:
                    for msg in msgs:
                        ctx = msg.get("context_token", "")
                        if ctx:
                            cfg = load_config()
                            cfg["weixin"]["context_token"] = ctx
                            save_config(cfg)
                            log(f"[weixin] 获取到 context_token")

                    # [新增] 处理用户回复消息
                    if PENDING_DIR.exists() and any(PENDING_DIR.glob("*.json")):
                        for item in msg.get("item_list", []):
                            if item.get("type") == 1:
                                msg_text = item.get("text_item", {}).get("text", "")
                                if msg_text.strip():
                                    _process_incoming_message(msg_text.strip(), "weixin")
                                break

                consecutive_failures = 0
                if msgs:
                    log(f"[weixin] getupdates OK (msgs={len(msgs)})")
            else:
                consecutive_failures += 1
                log(f"[weixin] getupdates 失败 ret={ret} errcode={errcode}, 连续失败 {consecutive_failures}")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    log("[weixin] 连续失败，保活退出")
                    break
                time.sleep(5)
                continue

        except urllib.error.URLError as e:
            if "timed out" in str(e.reason).lower() or "timeout" in str(e.reason).lower():
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    break
                time.sleep(5)
                continue
        except Exception as e:
            consecutive_failures += 1
            log(f"[weixin] 异常: {e}")
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                break
            time.sleep(5)
            continue


# ========== QQ WebSocket ==========

def qq_get_access_token(app_id: str, app_secret: str) -> str:
    """获取 QQ Bot access_token"""
    body = json.dumps({"appId": app_id, "clientSecret": app_secret}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(QQ_TOKEN_URL, data=body, headers=headers, method="POST")
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read().decode("utf-8"))
    return data.get("access_token", "")


def qq_get_gateway(access_token: str) -> str:
    """获取 QQ WebSocket 网关地址"""
    headers = {"Authorization": f"QQBot {access_token}"}
    req = urllib.request.Request(f"{QQ_API_BASE}/gateway", method="GET")
    for k, v in headers.items():
        req.add_header(k, v)
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read().decode("utf-8"))
    return data.get("url", "")


async def qq_websocket_loop():
    """QQ WebSocket 事件监听，自动获取 user_openid"""
    try:
        import websockets
    except ImportError:
        log("[qq] websockets 未安装，跳过 QQ 监听")
        return

    while True:
        cfg = load_config()
        qq = cfg.get("qq", {})
        app_id = qq.get("app_id", "")
        app_secret = qq.get("app_secret", "")
        enabled = qq.get("enabled", False)

        if not app_id or not app_secret:
            log("[qq] QQ 配置不完整，QQ 监听退出")
            break

        try:
            access_token = qq_get_access_token(app_id, app_secret)
            if not access_token:
                log("[qq] 获取 access_token 失败")
                await asyncio.sleep(30)
                continue

            gateway_url = qq_get_gateway(access_token)
            if not gateway_url:
                log("[qq] 获取 gateway URL 失败")
                await asyncio.sleep(30)
                continue

            log(f"[qq] 连接 WebSocket: {gateway_url[:50]}...")

            async with websockets.connect(gateway_url, ping_interval=None) as ws:
                # 接收 Hello 消息（OpCode 10）
                hello_raw = await asyncio.wait_for(ws.recv(), timeout=10)
                hello = json.loads(hello_raw)
                heartbeat_interval = hello.get("d", {}).get("heartbeat_interval", 40000) / 1000

                # 发送 Identify（OpCode 2）
                identify = {
                    "op": 2,
                    "d": {
                        "token": f"QQBot {access_token}",
                        "intents": QQ_INTENT_GROUP_AND_C2C,
                        "shard": [0, 1],
                        "properties": {
                            "$os": "windows",
                            "$browser": "claude-notify",
                            "$device": "claude-notify",
                        },
                    },
                }
                await ws.send(json.dumps(identify))

                # 接收 READY 事件
                ready_raw = await asyncio.wait_for(ws.recv(), timeout=10)
                ready = json.loads(ready_raw)
                if ready.get("t") == "READY":
                    log("[qq] WebSocket 已连接，监听消息事件...")
                else:
                    log(f"[qq] 非预期消息: {ready.get('t', 'unknown')}")

                # 心跳 + 事件循环
                last_heartbeat = time.time()
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=2)
                        msg = json.loads(raw)

                        if msg.get("op") == 1:  # 心跳请求
                            await ws.send(json.dumps({"op": 1, "d": None}))
                            last_heartbeat = time.time()
                            continue

                        if msg.get("op") == 0:  # 事件分发
                            event_type = msg.get("t", "")
                            event_data = msg.get("d", {})

                            if event_type == "C2C_MESSAGE_CREATE":
                                author = event_data.get("author", {})
                                user_openid = author.get("user_openid", "")
                                content = event_data.get("content", "")[:50]
                                if user_openid:
                                    cfg = load_config()
                                    if cfg.get("qq", {}).get("target_id") != f"qqbot:c2c:{user_openid}":
                                        cfg["qq"]["target_id"] = f"qqbot:c2c:{user_openid}"
                                        save_config(cfg)
                                        log(f"[qq] 获取到 user_openid: {user_openid}")
                                    else:
                                        log(f"[qq] 收到消息 from {user_openid}: {content}")
                                # [新增] 处理用户回复消息
                                content_text = event_data.get("content", "").strip()
                                if content_text and PENDING_DIR.exists() and any(PENDING_DIR.glob("*.json")):
                                    _process_incoming_message(content_text, "qq")

                            elif event_type == "GROUP_AT_MESSAGE_CREATE":
                                group_openid = event_data.get("group_openid", "")
                                author = event_data.get("author", {})
                                member_openid = author.get("member_openid", "")
                                if group_openid:
                                    log(f"[qq] 收到群消息 group={group_openid} from={member_openid}")

                    except asyncio.TimeoutError:
                        # 发送心跳
                        if time.time() - last_heartbeat >= heartbeat_interval:
                            await ws.send(json.dumps({"op": 1, "d": None}))
                            last_heartbeat = time.time()
                        continue

        except Exception as e:
            log(f"[qq] WebSocket 异常: {e}")
            await asyncio.sleep(10)
            continue


def qq_thread_entry():
    """QQ WebSocket 线程入口"""
    try:
        asyncio.run(qq_websocket_loop())
    except Exception as e:
        log(f"[qq] 线程异常退出: {e}")


# ========== Telegram Long Polling ==========

def telegram_poll_loop():
    """Telegram Bot 长轮询监听，自动获取 chat_id"""
    import urllib.request
    import urllib.error

    offset = 0
    consecutive_failures = 0

    while True:
        cfg = load_config()
        tg = cfg.get("telegram", {})
        bot_token = tg.get("bot_token", "")
        enabled = tg.get("enabled", False)

        if not enabled or not bot_token:
            log("[telegram] Telegram 未启用或配置不完整，监听退出")
            break

        url = f"https://api.telegram.org/bot{bot_token}/getUpdates?timeout=30&offset={offset}"
        req = urllib.request.Request(url, method="GET")

        try:
            resp = urllib.request.urlopen(req, timeout=35)
            data = json.loads(resp.read().decode("utf-8"))

            if data.get("ok"):
                results = data.get("result", [])
                for update in results:
                    offset = update.get("update_id", offset) + 1
                    msg = update.get("message", {})
                    chat = msg.get("chat", {})
                    chat_id = str(chat.get("id", ""))
                    text = msg.get("text", "").strip()

                    if chat_id:
                        cfg = load_config()
                        if cfg.get("telegram", {}).get("chat_id") != chat_id:
                            cfg["telegram"]["chat_id"] = chat_id
                            save_config(cfg)
                            log(f"[telegram] 获取到 chat_id: {chat_id}")

                    if text and PENDING_DIR.exists() and any(PENDING_DIR.glob("*.json")):
                        _process_incoming_message(text, "telegram")

                consecutive_failures = 0
            else:
                consecutive_failures += 1
                log(f"[telegram] getUpdates 失败: {data}")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    break
                time.sleep(5)

        except urllib.error.URLError as e:
            if "timed out" in str(e.reason).lower() or "timeout" in str(e.reason).lower():
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    break
                time.sleep(5)
        except Exception as e:
            consecutive_failures += 1
            log(f"[telegram] 异常: {e}")
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                break
            time.sleep(5)


def telegram_thread_entry():
    """Telegram 长轮询线程入口"""
    try:
        telegram_poll_loop()
    except Exception as e:
        log(f"[telegram] 线程异常退出: {e}")


# ========== Feishu WebSocket ==========

def feishu_websocket_loop():
    """飞书 WebSocket 长连接监听，自动获取 open_id"""
    try:
        import lark_oapi as lark
        from lark_oapi.adapter.websocket import WebSocketClient
    except ImportError:
        log("[feishu] lark-oapi 未安装，跳过飞书监听")
        return

    while True:
        cfg = load_config()
        fs = cfg.get("feishu", {})
        app_id = fs.get("app_id", "")
        app_secret = fs.get("app_secret", "")
        enabled = fs.get("enabled", False)

        if not enabled or not app_id or not app_secret:
            log("[feishu] 飞书未启用或配置不完整，监听退出")
            break

        try:
            event_handler = lark.EventDispatcherHandler.builder("", "")

            def on_message(ctx, config, event):
                try:
                    msg = event.event.message
                    sender = event.event.sender
                    open_id = sender.sender_id.open_id if sender and sender.sender_id else ""
                    content = json.loads(msg.content).get("text", "").strip() if msg.content else ""

                    if open_id:
                        cfg = load_config()
                        if cfg.get("feishu", {}).get("receive_id") != open_id:
                            cfg["feishu"]["receive_id"] = open_id
                            save_config(cfg)
                            log(f"[feishu] 获取到 open_id: {open_id}")

                    if content and PENDING_DIR.exists() and any(PENDING_DIR.glob("*.json")):
                        _process_incoming_message(content, "feishu")
                except Exception as e:
                    log(f"[feishu] 处理消息异常: {e}")

            event_handler.register_p2_im_message_receive_v1(on_message)

            ws_client = WebSocketClient(
                app_id=app_id,
                app_secret=app_secret,
                event_handler=event_handler,
                log_level=lark.LogLevel.WARNING,
            )
            log("[feishu] WebSocket 连接中...")
            ws_client.start()

        except Exception as e:
            log(f"[feishu] WebSocket 异常: {e}")
            time.sleep(10)
            continue


def feishu_thread_entry():
    """飞书 WebSocket 线程入口"""
    try:
        feishu_websocket_loop()
    except Exception as e:
        log(f"[feishu] 线程异常退出: {e}")


# ========== DingTalk Stream ==========

def dingtalk_stream_loop():
    """钉钉 Stream 长连接监听，自动获取 user_id"""
    try:
        import dingtalk_stream
        from dingtalk_stream import DingTalkStreamClient
    except ImportError:
        log("[dingtalk] dingtalk-stream 未安装，跳过钉钉监听")
        return

    while True:
        cfg = load_config()
        dt = cfg.get("dingtalk", {})
        app_key = dt.get("app_key", "")
        app_secret = dt.get("app_secret", "")
        enabled = dt.get("enabled", False)

        if not enabled or not app_key or not app_secret:
            log("[dingtalk] 钉钉未启用或配置不完整，监听退出")
            break

        try:
            client = DingTalkStreamClient(app_key, app_secret)

            @client.register_callback_handler
            def on_message(data):
                try:
                    content = ""
                    sender_id = ""
                    if isinstance(data, dict):
                        content = data.get("text", {}).get("content", "").strip()
                        sender_id = data.get("senderStaffId", "") or data.get("senderId", "")
                    else:
                        content = getattr(data, "text", {}).get("content", "").strip() if hasattr(data, "text") else ""
                        sender_id = getattr(data, "sender_staff_id", "") or getattr(data, "sender_id", "")

                    if sender_id:
                        cfg = load_config()
                        if cfg.get("dingtalk", {}).get("user_id") != sender_id:
                            cfg["dingtalk"]["user_id"] = sender_id
                            save_config(cfg)
                            log(f"[dingtalk] 获取到 user_id: {sender_id}")

                    if content and PENDING_DIR.exists() and any(PENDING_DIR.glob("*.json")):
                        _process_incoming_message(content, "dingtalk")
                except Exception as e:
                    log(f"[dingtalk] 处理消息异常: {e}")

            log("[dingtalk] Stream 连接中...")
            client.start_forever()

        except Exception as e:
            log(f"[dingtalk] Stream 异常: {e}")
            time.sleep(10)
            continue


def dingtalk_thread_entry():
    """钉钉 Stream 线程入口"""
    try:
        dingtalk_stream_loop()
    except Exception as e:
        log(f"[dingtalk] 线程异常退出: {e}")


# ========== 主入口 ==========

def cleanup(signum=None, frame=None):
    log("守护进程收到退出信号，清理中...")
    remove_pid()
    sys.exit(0)


def main():
    if is_already_running():
        print("keepalive 已在运行中")
        return

    write_pid()
    log(f"keepalive 守护进程启动 (PID={os.getpid()})")

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, cleanup)

    # 启动 QQ WebSocket 线程（有凭据就启动，用于自动获取 user_openid）
    qq_thread = None
    cfg = load_config()
    if cfg.get("qq", {}).get("app_id") and cfg.get("qq", {}).get("app_secret"):
        qq_thread = threading.Thread(target=qq_thread_entry, daemon=True)
        qq_thread.start()
        log("[qq] WebSocket 监听线程已启动")

    # 启动 Telegram 长轮询线程
    tg_thread = None
    if cfg.get("telegram", {}).get("enabled") and cfg.get("telegram", {}).get("bot_token"):
        tg_thread = threading.Thread(target=telegram_thread_entry, daemon=True)
        tg_thread.start()
        log("[telegram] 长轮询监听线程已启动")

    # 启动飞书 WebSocket 线程
    fs_thread = None
    if cfg.get("feishu", {}).get("enabled") and cfg.get("feishu", {}).get("app_id") and cfg.get("feishu", {}).get("app_secret"):
        fs_thread = threading.Thread(target=feishu_thread_entry, daemon=True)
        fs_thread.start()
        log("[feishu] WebSocket 监听线程已启动")

    # 启动钉钉 Stream 线程
    dt_thread = None
    if cfg.get("dingtalk", {}).get("enabled") and cfg.get("dingtalk", {}).get("app_key") and cfg.get("dingtalk", {}).get("app_secret"):
        dt_thread = threading.Thread(target=dingtalk_thread_entry, daemon=True)
        dt_thread.start()
        log("[dingtalk] Stream 监听线程已启动")

    try:
        weixin_keepalive_loop()
        # 微信保活退出后，等待所有监听线程退出
        for t in [qq_thread, tg_thread, fs_thread, dt_thread]:
            if t and t.is_alive():
                t.join()
    except KeyboardInterrupt:
        pass
    finally:
        remove_pid()
        log("keepalive 守护进程已退出")


if __name__ == "__main__":
    main()
