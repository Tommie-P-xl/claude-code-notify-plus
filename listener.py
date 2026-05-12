"""
临时消息监听器。
在 hook 等待用户回复期间启动，收到回复后立即停止。
无守护进程，用完即走。
"""

import threading
import time
import json
import sys
import os
import random
import base64
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent

# ── 日志 ──────────────────────────────────────────────────

def _log(msg: str):
    from datetime import datetime
    log_file = SCRIPT_DIR / "notify.log"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [listener] {msg}\n")
    except Exception:
        pass


# ── 配置辅助 ──────────────────────────────────────────────

def _update_config(channel: str, key: str, value: str):
    """原子更新 config.json 中指定渠道的字段"""
    config_file = SCRIPT_DIR / "config.json"
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if channel not in cfg:
            cfg[channel] = {}
        if cfg[channel].get(key) == value:
            return
        cfg[channel][key] = value
        import tempfile
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(SCRIPT_DIR), suffix=".tmp")
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(config_file))
        _log(f"[{channel}] 自动更新 {key}={value[:30]}")
    except Exception as e:
        _log(f"[{channel}] 更新配置失败: {e}")


# ── 回复解析 ──────────────────────────────────────────────

def _extract_reply_parts(text: str) -> tuple[str, str]:
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


# ── 反馈发送 ──────────────────────────────────────────────

def _send_confirmation(channel: str, label: str, reply: str):
    """向回复渠道发送确认反馈"""
    try:
        from notify import load_config
        cfg = load_config()
        ch = _create_channel(channel, cfg)
        if ch:
            ch.send("Claude Code - 回复确认", f"已收到回复: {reply}")
            _log(f"[{channel}] 确认反馈已发送")
    except Exception as e:
        _log(f"[{channel}] 发送确认反馈失败: {e}")


def _send_already_handled_feedback(channel: str, label: str):
    """向渠道发送'已处理'反馈"""
    try:
        from notify import load_config
        cfg = load_config()
        ch = _create_channel(channel, cfg)
        if ch:
            ch.send("Claude Code - 已处理", f"#{label} 已被其他渠道处理，您的回复已忽略")
            _log(f"[{channel}] 已处理反馈已发送")
    except Exception as e:
        _log(f"[{channel}] 发送已处理反馈失败: {e}")


def _send_no_pending_feedback(channel: str, label: str = ""):
    """向渠道发送'无待处理请求'反馈"""
    try:
        from notify import load_config
        cfg = load_config()
        ch = _create_channel(channel, cfg)
        if ch:
            msg = f"当前无等待回复的请求（#{label} 可能已被其他渠道处理）" if label else "当前无等待回复的请求"
            ch.send("Claude Code - 提示", msg)
    except Exception:
        pass


def _create_channel(channel: str, cfg: dict):
    """根据渠道名创建渠道实例"""
    if channel == "qq":
        from channels.qq import QQBotChannel
        return QQBotChannel(cfg)
    elif channel == "telegram":
        from channels.telegram import TelegramChannel
        return TelegramChannel(cfg)
    elif channel == "feishu":
        from channels.feishu import FeishuChannel
        return FeishuChannel(cfg)
    elif channel == "dingtalk":
        from channels.dingtalk import DingTalkChannel
        return DingTalkChannel(cfg)
    elif channel == "weixin":
        from channels.weixin import WeixinChannel
        return WeixinChannel(cfg)
    return None


# ── 消息处理 ──────────────────────────────────────────────

def _process_message(text: str, channel: str, request_id: str, pending: dict, stop_event: threading.Event) -> bool:
    """
    解析收到的消息，判断是否匹配当前请求。
    匹配成功则写入 response 文件并 set stop_event。
    返回 True 表示成功处理。
    """
    # 1. 解析标签和回复内容
    label, reply = _extract_reply_parts(text)

    # 2. 省略标签时，默认回复当前请求
    if not label:
        reply = text.strip()
        label = pending.get("label", "")

    # 3. 标签不匹配，忽略
    if label.upper() != pending.get("label", "").upper():
        return False

    if not reply:
        return False

    _log(f"[{channel}] 匹配请求: label={label}, reply={reply[:50]}")

    # 4. 写入 response 文件（原子操作）
    from interaction import write_response
    success = write_response(request_id, reply, channel, label=label)

    if success:
        stop_event.set()
        # 向回复渠道发送确认反馈（在独立线程中发，不阻塞主流程）
        threading.Thread(
            target=_send_confirmation,
            args=(channel, label, reply),
            daemon=True
        ).start()
        return True
    else:
        # 已被其他渠道抢先处理
        _send_already_handled_feedback(channel, label)
        return False


def _process_message_global(text: str, channel: str, stop_event: threading.Event):
    """
    主监听进程的全局消息处理：
    遍历所有 pending 文件，找到 label 匹配的请求。
    """
    label, reply = _extract_reply_parts(text)
    if not reply or not label:
        return

    from interaction import list_requests, write_response, PENDING_DIR
    if not PENDING_DIR.exists():
        return

    for req in list_requests():
        req_label = req.get("label", "").upper()
        if req_label == label.upper():
            success = write_response(req["id"], reply, channel, label=label)
            if success:
                stop_event.set()
                threading.Thread(
                    target=_send_confirmation,
                    args=(channel, label, reply),
                    daemon=True
                ).start()
            else:
                _send_already_handled_feedback(channel, label)
            return

    # 未找到匹配请求
    _send_no_pending_feedback(channel, label)


# ── 对外接口 ──────────────────────────────────────────────

def start_listeners(config: dict, request_id: str, pending: dict, stop_event: threading.Event):
    """
    根据 config 中已启用的渠道，启动对应的临时监听线程。
    所有线程共享同一个 stop_event，任一渠道收到回复后 set stop_event。

    参数：
      config      - 完整配置 dict
      request_id  - 当前 pending 请求 ID
      pending     - pending dict（含 label 等信息）
      stop_event  - threading.Event，set 后所有线程退出
    """
    threads = []

    if config.get("telegram", {}).get("enabled") and config.get("telegram", {}).get("bot_token"):
        t = threading.Thread(
            target=_telegram_listener,
            args=(config, request_id, pending, stop_event),
            daemon=True,
            name="listener-telegram"
        )
        threads.append(t)

    if config.get("qq", {}).get("enabled") and config.get("qq", {}).get("app_id"):
        t = threading.Thread(
            target=_qq_listener,
            args=(config, request_id, pending, stop_event),
            daemon=True,
            name="listener-qq"
        )
        threads.append(t)

    if config.get("feishu", {}).get("enabled") and config.get("feishu", {}).get("app_id"):
        t = threading.Thread(
            target=_feishu_listener,
            args=(config, request_id, pending, stop_event),
            daemon=True,
            name="listener-feishu"
        )
        threads.append(t)

    if config.get("dingtalk", {}).get("enabled") and config.get("dingtalk", {}).get("client_id"):
        t = threading.Thread(
            target=_dingtalk_listener,
            args=(config, request_id, pending, stop_event),
            daemon=True,
            name="listener-dingtalk"
        )
        threads.append(t)

    if config.get("weixin", {}).get("enabled") and config.get("weixin", {}).get("bot_token"):
        t = threading.Thread(
            target=_weixin_listener,
            args=(config, request_id, pending, stop_event),
            daemon=True,
            name="listener-weixin"
        )
        threads.append(t)

    for t in threads:
        t.start()

    if threads:
        _log(f"启动 {len(threads)} 个临时监听线程: {', '.join(t.name for t in threads)}")

    return threads


# ── Telegram 临时监听 ─────────────────────────────────────

def _get_telegram_latest_offset(base_url: str) -> int:
    """获取 Telegram 最新 update_id，避免重复处理历史消息"""
    import urllib.request
    try:
        url = f"{base_url}/getUpdates?limit=1&offset=-1"
        req = urllib.request.Request(url, method="GET")
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        results = data.get("result", [])
        if results:
            return results[-1]["update_id"] + 1
    except Exception:
        pass
    return 0


def _telegram_listener(config: dict, request_id: str, pending: dict, stop_event: threading.Event):
    """Telegram 临时长轮询监听"""
    import urllib.request

    bot_token = config["telegram"]["bot_token"]
    base_url = f"https://api.telegram.org/bot{bot_token}"

    # 获取当前 offset（从最新消息开始，不重复处理历史消息）
    offset = _get_telegram_latest_offset(base_url)
    _log(f"[telegram] 临时监听启动, offset={offset}")

    while not stop_event.is_set():
        try:
            url = f"{base_url}/getUpdates?timeout=20&offset={offset}&allowed_updates=[\"message\"]"
            req = urllib.request.Request(url, method="GET")
            resp = urllib.request.urlopen(req, timeout=25)
            data = json.loads(resp.read().decode("utf-8"))

            if data.get("ok"):
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    text = update.get("message", {}).get("text", "").strip()
                    chat_id = str(update.get("message", {}).get("chat", {}).get("id", ""))

                    # 自动更新 chat_id（如果尚未配置）
                    if chat_id and not config.get("telegram", {}).get("chat_id"):
                        _update_config("telegram", "chat_id", chat_id)
                        config["telegram"]["chat_id"] = chat_id

                    if text:
                        _log(f"[telegram] 收到消息: {text[:50]}")
                        _process_message(text, "telegram", request_id, pending, stop_event)
                        if stop_event.is_set():
                            return
            else:
                time.sleep(2)
        except Exception:
            if not stop_event.is_set():
                time.sleep(2)

    _log("[telegram] 临时监听退出")


# ── QQ 临时 WebSocket ─────────────────────────────────────

def _qq_get_access_token(app_id: str, app_secret: str) -> str:
    """获取 QQ Bot access_token"""
    import urllib.request
    body = json.dumps({"appId": app_id, "clientSecret": app_secret}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request("https://bots.qq.com/app/getAppAccessToken", data=body, headers=headers, method="POST")
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read().decode("utf-8"))
    return data.get("access_token", "")


def _qq_get_gateway(access_token: str) -> str:
    """获取 QQ WebSocket 网关地址"""
    import urllib.request
    headers = {"Authorization": f"QQBot {access_token}"}
    req = urllib.request.Request("https://api.sgroup.qq.com/gateway", method="GET")
    for k, v in headers.items():
        req.add_header(k, v)
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read().decode("utf-8"))
    return data.get("url", "")


def _qq_listener(config: dict, request_id: str, pending: dict, stop_event: threading.Event):
    """QQ WebSocket 临时监听"""
    import asyncio

    async def _async_qq():
        try:
            import websockets
        except ImportError:
            _log("[qq] websockets 未安装，跳过")
            return

        qq_cfg = config.get("qq", {})
        app_id = qq_cfg.get("app_id", "")
        app_secret = qq_cfg.get("app_secret", "")

        # 1. 获取 access_token 和 gateway
        try:
            token = _qq_get_access_token(app_id, app_secret)
        except Exception as e:
            _log(f"[qq] 获取 access_token 失败: {e}")
            return
        if not token:
            _log("[qq] access_token 为空")
            return
        try:
            gateway = _qq_get_gateway(token)
        except Exception as e:
            _log(f"[qq] 获取 gateway 失败: {e}")
            return
        if not gateway:
            _log("[qq] gateway 为空")
            return

        _log(f"[qq] 临时 WebSocket 连接中...")

        # 2. 建立 WebSocket 连接
        try:
            async with websockets.connect(gateway, ping_interval=None) as ws:
                # Hello → Identify → READY
                hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                heartbeat_interval = hello.get("d", {}).get("heartbeat_interval", 40000) / 1000

                await ws.send(json.dumps({
                    "op": 2,
                    "d": {
                        "token": f"QQBot {token}",
                        "intents": 1 << 25,  # C2C + GROUP
                        "shard": [0, 1],
                        "properties": {
                            "$os": "windows",
                            "$browser": "claude-notify",
                            "$device": "claude-notify",
                        },
                    }
                }))

                # 等待 READY
                ready_raw = await asyncio.wait_for(ws.recv(), timeout=10)
                ready = json.loads(ready_raw)
                if ready.get("t") == "READY":
                    _log("[qq] WebSocket 已连接，监听消息...")

                # 心跳 + 事件循环
                last_heartbeat = time.time()
                while not stop_event.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=2)
                        msg = json.loads(raw)

                        if msg.get("op") == 0 and msg.get("t") == "C2C_MESSAGE_CREATE":
                            content = msg.get("d", {}).get("content", "").strip()
                            author = msg.get("d", {}).get("author", {})
                            user_openid = author.get("user_openid", "")

                            # 自动更新 target_id
                            if user_openid and not config.get("qq", {}).get("target_id"):
                                _update_config("qq", "target_id", f"qqbot:c2c:{user_openid}")
                                config["qq"]["target_id"] = f"qqbot:c2c:{user_openid}"

                            if content:
                                _log(f"[qq] 收到消息: {content[:50]}")
                                _process_message(content, "qq", request_id, pending, stop_event)

                    except asyncio.TimeoutError:
                        if time.time() - last_heartbeat >= heartbeat_interval:
                            await ws.send(json.dumps({"op": 1, "d": None}))
                            last_heartbeat = time.time()
        except Exception as e:
            if not stop_event.is_set():
                _log(f"[qq] WebSocket 异常: {e}")

    # 在独立 event loop 中运行（避免与主线程冲突）
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_async_qq())
    finally:
        loop.close()
    _log("[qq] 临时监听退出")


# ── 飞书临时 WebSocket ─────────────────────────────────────

def _feishu_listener(config: dict, request_id: str, pending: dict, stop_event: threading.Event):
    """飞书临时 WebSocket 监听"""
    try:
        import lark_oapi as lark
        from lark_oapi.ws import Client as WsClient
    except ImportError:
        _log("[feishu] lark-oapi 未安装，跳过")
        return

    fs_cfg = config.get("feishu", {})
    app_id = fs_cfg.get("app_id", "")
    app_secret = fs_cfg.get("app_secret", "")

    _log("[feishu] 临时 WebSocket 连接中...")

    def on_message(data):
        try:
            msg = data.event.message
            sender = data.event.sender
            open_id = sender.sender_id.open_id if sender and sender.sender_id else ""

            # 自动更新 receive_id
            if open_id and not config.get("feishu", {}).get("receive_id"):
                _update_config("feishu", "receive_id", open_id)
                config["feishu"]["receive_id"] = open_id

            content = ""
            if msg.content:
                try:
                    content = json.loads(msg.content).get("text", "").strip()
                except Exception:
                    pass

            if content:
                _log(f"[feishu] 收到消息: {content[:50]}")
                _process_message(content, "feishu", request_id, pending, stop_event)
        except Exception as e:
            _log(f"[feishu] 处理消息异常: {e}")

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )

    client = WsClient(
        app_id=app_id,
        app_secret=app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.WARNING,
    )

    # 看门狗线程：stop_event 被 set 后强制停止 client
    def _watchdog():
        stop_event.wait()  # 阻塞直到 stop_event 被 set
        try:
            # 尝试关闭 SDK 内部 websocket
            if hasattr(client, '_Client__ws_client') and client._Client__ws_client:
                client._Client__ws_client.close()
        except Exception:
            pass

    threading.Thread(target=_watchdog, daemon=True).start()

    try:
        client.start()
    except Exception:
        pass
    _log("[feishu] 临时监听退出")


# ── 钉钉临时 Stream ───────────────────────────────────────

def _dingtalk_listener(config: dict, request_id: str, pending: dict, stop_event: threading.Event):
    """钉钉临时 Stream 监听"""
    try:
        import dingtalk_stream
        from dingtalk_stream import ChatbotHandler, Credential
    except ImportError:
        _log("[dingtalk] dingtalk-stream 未安装，跳过")
        return

    dt_cfg = config.get("dingtalk", {})
    client_id = dt_cfg.get("client_id", "")
    client_secret = dt_cfg.get("client_secret", "")

    _log("[dingtalk] 临时 Stream 连接中...")

    credential = Credential(client_id, client_secret)
    client = dingtalk_stream.DingTalkStreamClient(credential)

    class BotHandler(ChatbotHandler):
        def process(self, callback_message):
            try:
                message = dingtalk_stream.ChatbotMessage.from_dict(callback_message.data)
                content = ""
                if message.text and hasattr(message.text, 'content'):
                    content = message.text.content.strip()

                sender_id = message.sender_staff_id or message.sender_id or ""
                if sender_id and not config.get("dingtalk", {}).get("user_id"):
                    _update_config("dingtalk", "user_id", sender_id)
                    config["dingtalk"]["user_id"] = sender_id

                if content:
                    _log(f"[dingtalk] 收到消息: {content[:50]}")
                    _process_message(content, "dingtalk", request_id, pending, stop_event)
            except Exception as e:
                _log(f"[dingtalk] 处理消息异常: {e}")

    client.register_callback_handler(dingtalk_stream.ChatbotMessage.TOPIC, BotHandler())

    # 看门狗：收到回复后停止 client
    def _watchdog():
        stop_event.wait()
        try:
            client.stop()
        except Exception:
            pass

    threading.Thread(target=_watchdog, daemon=True).start()

    try:
        client.start_forever()
    except Exception:
        pass
    _log("[dingtalk] 临时监听退出")


# ── 微信临时轮询 ──────────────────────────────────────────

def _random_wechat_uin() -> str:
    """生成随机的 X-WECHAT-UIN 头"""
    uint32 = random.randint(0, 2**32 - 1)
    return base64.b64encode(str(uint32).encode("utf-8")).decode("utf-8")


def _weixin_listener(config: dict, request_id: str, pending: dict, stop_event: threading.Event):
    """微信临时 getupdates 轮询"""
    import urllib.request
    import urllib.error

    wx_cfg = config.get("weixin", {})
    token = wx_cfg.get("bot_token", "")
    baseurl = wx_cfg.get("baseurl", "https://ilinkai.weixin.qq.com").rstrip("/")

    if not token:
        return

    _log("[weixin] 临时轮询启动")

    sync_buf = ""
    consecutive_failures = 0

    while not stop_event.is_set():
        try:
            body = json.dumps({
                "get_updates_buf": sync_buf,
                "base_info": {"channel_version": "2.2.0"}
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

            url = f"{baseurl}/ilink/bot/getupdates"
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            resp = urllib.request.urlopen(req, timeout=40)
            data = json.loads(resp.read().decode("utf-8", errors="replace"))

            ret = data.get("ret", 0)
            errcode = data.get("errcode", 0)

            if ret == 0 and errcode == 0:
                consecutive_failures = 0
                new_buf = data.get("get_updates_buf", "")
                if new_buf:
                    sync_buf = new_buf

                for msg in data.get("msgs", []):
                    # 更新 context_token 和 to_user_id（如需要）
                    ctx = msg.get("context_token", "")
                    if ctx:
                        _update_config("weixin", "context_token", ctx)
                        config.setdefault("weixin", {})["context_token"] = ctx

                    from_user = msg.get("from_user_id", "")
                    if from_user and not wx_cfg.get("to_user_id"):
                        _update_config("weixin", "to_user_id", from_user)
                        config.setdefault("weixin", {})["to_user_id"] = from_user

                    # 处理消息内容
                    for item in msg.get("item_list", []):
                        if item.get("type") == 1:
                            text = item.get("text_item", {}).get("text", "").strip()
                            if text:
                                _log(f"[weixin] 收到消息: {text[:50]}")
                                _process_message(text, "weixin", request_id, pending, stop_event)
                            break

            elif errcode == -14 or ret == -14:
                # bot session 过期，无法继续监听
                _log("[weixin] listener: session 过期，请在 Web UI 重新扫码登录")
                return

            elif ret == -2:
                # context_token 过期，清空 sync_buf 继续轮询（下次可能恢复）
                _log("[weixin] listener: ret=-2, context_token 可能过期，清空 sync_buf 继续")
                sync_buf = ""
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    return
                time.sleep(3)

            else:
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    _log("[weixin] 连续失败，退出监听")
                    return
                time.sleep(3)

        except urllib.error.URLError as e:
            if "timed out" not in str(getattr(e, 'reason', '')).lower():
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    return
                time.sleep(3)
        except Exception:
            consecutive_failures += 1
            if consecutive_failures >= 3:
                return
            time.sleep(3)

    _log("[weixin] 临时轮询退出")
