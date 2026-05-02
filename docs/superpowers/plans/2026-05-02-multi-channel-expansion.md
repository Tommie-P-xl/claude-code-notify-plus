# Multi-Channel Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Feishu, DingTalk, and Telegram as notification + interaction channels, following the existing channel pattern with minimal architecture changes.

**Architecture:** Each channel is a `NotificationChannel` subclass in `channels/`. Message listening threads are added to `weixin_keepalive.py`. Web UI gets three new config tabs. All channels use outbound-only connections (no public IP needed).

**Tech Stack:** Python 3.10+, lark-oapi (Feishu), dingtalk-stream (DingTalk), urllib (Telegram), Flask (Web UI)

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `channels/telegram.py` | TelegramChannel: send via Bot API + long polling message listener |
| `channels/feishu.py` | FeishuChannel: send via Open API + WebSocket message listener |
| `channels/dingtalk.py` | DingTalkChannel: send via Open API + Stream message listener |

### Modified Files
| File | Changes |
|------|---------|
| `channels/__init__.py` | Add imports/exports for 3 new channels |
| `weixin_keepalive.py` | Add 3 listener loop functions + thread startup in `main()` |
| `notify.py` | Add imports, `collect_channels()`, `DEFAULT_CONFIG`, keepalive condition |
| `app.py` | Add API routes (validate/status/logout), toggle validation, `DEFAULT_CONFIG` |
| `static/index.html` | Add 3 tab pages + dashboard toggle cards |
| `requirements.txt` | Add `lark-oapi`, `dingtalk-stream` |

---

### Task 1: Telegram Channel

**Files:**
- Create: `channels/telegram.py`

- [ ] **Step 1: Create `channels/telegram.py`**

```python
"""Telegram Bot 通知渠道。通过 Telegram Bot API 发送消息，长轮询接收回复。"""

import json
import time
import urllib.request
import urllib.error
from typing import Dict, Any, Optional
from .base import NotificationChannel

BOT_API_BASE = "https://api.telegram.org"


def _log(msg: str):
    from pathlib import Path
    from datetime import datetime
    log_file = Path(__file__).resolve().parent.parent / "notify.log"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


class TelegramChannel(NotificationChannel):
    """通过 Telegram Bot API 发送消息"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._tg_config = config.get("telegram", {})

    @property
    def name(self) -> str:
        return "telegram"

    def is_enabled(self) -> bool:
        return self._tg_config.get("enabled", False)

    def send(self, title: str, message: str) -> bool:
        bot_token = self._tg_config.get("bot_token", "")
        chat_id = self._tg_config.get("chat_id", "")
        if not bot_token or not chat_id:
            _log("[telegram] bot_token 或 chat_id 为空")
            return False

        full_text = f"【{title}】\n{message}"
        url = f"{BOT_API_BASE}/bot{bot_token}/sendMessage"
        body = json.dumps({
            "chat_id": chat_id,
            "text": full_text,
        }).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")

        try:
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode("utf-8"))
            ok = data.get("ok", False)
            _log(f"[telegram] sendMessage ok={ok}")
            return ok
        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            _log(f"[telegram] HTTPError {e.code}: {resp_body[:200]}")
            return False
        except Exception as e:
            _log(f"[telegram] 异常: {e}")
            return False

    @staticmethod
    def validate_credentials(bot_token: str) -> Dict[str, Any]:
        """验证 Telegram Bot Token 是否有效"""
        url = f"{BOT_API_BASE}/bot{bot_token}/getMe"
        req = urllib.request.Request(url, method="GET")
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("ok"):
                bot_info = data.get("result", {})
                return {
                    "ok": True,
                    "message": f"验证成功: @{bot_info.get('username', 'unknown')}",
                    "bot_username": bot_info.get("username", ""),
                }
            return {"ok": False, "error": "API 返回 ok=false"}
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return {"ok": False, "error": "Bot Token 无效"}
            return {"ok": False, "error": f"HTTP {e.code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def get_login_status(config: Dict[str, Any]) -> Dict[str, Any]:
        tg_config = config.get("telegram", {})
        bot_token = tg_config.get("bot_token", "")
        chat_id = tg_config.get("chat_id", "")

        if not bot_token:
            return {"logged_in": False, "message": "未配置 Bot Token"}

        return {
            "logged_in": True,
            "chat_id": chat_id or None,
            "message": "Telegram Bot 已配置" if chat_id else "已配置 Token，请发送消息给 Bot 以获取 Chat ID",
        }
```

- [ ] **Step 2: Commit**

```bash
git add channels/telegram.py
git commit -m "feat: add Telegram notification channel"
```

---

### Task 2: Feishu Channel

**Files:**
- Create: `channels/feishu.py`

- [ ] **Step 1: Create `channels/feishu.py`**

```python
"""飞书通知渠道。通过飞书 Open API 发送消息，WebSocket 长连接接收回复。"""

import json
import urllib.request
import urllib.error
from typing import Dict, Any, Optional
from .base import NotificationChannel

# 飞书 API 常量
FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages"


def _log(msg: str):
    from pathlib import Path
    from datetime import datetime
    log_file = Path(__file__).resolve().parent.parent / "notify.log"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


class FeishuChannel(NotificationChannel):
    """通过飞书 Open API 发送消息"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._fs_config = config.get("feishu", {})
        self._tenant_token: Optional[str] = None
        self._token_expires_at: float = 0

    @property
    def name(self) -> str:
        return "feishu"

    def is_enabled(self) -> bool:
        return self._fs_config.get("enabled", False)

    def _get_tenant_token(self) -> Optional[str]:
        """获取 tenant_access_token（自动缓存）"""
        import time
        now = time.time()
        if self._tenant_token and now < self._token_expires_at:
            return self._tenant_token

        app_id = self._fs_config.get("app_id", "")
        app_secret = self._fs_config.get("app_secret", "")
        if not app_id or not app_secret:
            _log("[feishu] app_id 或 app_secret 为空")
            return None

        body = json.dumps({
            "app_id": app_id,
            "app_secret": app_secret,
        }).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(FEISHU_TOKEN_URL, data=body, headers=headers, method="POST")

        try:
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("code") == 0:
                self._tenant_token = data.get("tenant_access_token", "")
                expires_in = int(data.get("expire", 7200))
                self._token_expires_at = now + expires_in - 300
                _log(f"[feishu] 获取 tenant_access_token 成功")
                return self._tenant_token
            _log(f"[feishu] 获取 token 失败: {data.get('msg', '')}")
            return None
        except Exception as e:
            _log(f"[feishu] 获取 token 异常: {e}")
            return None

    def send(self, title: str, message: str) -> bool:
        token = self._get_tenant_token()
        if not token:
            return False

        receive_id = self._fs_config.get("receive_id", "")
        if not receive_id:
            _log("[feishu] receive_id 为空")
            return False

        full_text = f"【{title}】\n{message}"
        body = json.dumps({
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": full_text}),
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        url = f"{FEISHU_MSG_URL}?receive_id_type=open_id"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")

        try:
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode("utf-8"))
            ok = data.get("code") == 0
            _log(f"[feishu] sendMessage code={data.get('code')}")
            return ok
        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            _log(f"[feishu] HTTPError {e.code}: {resp_body[:200]}")
            return False
        except Exception as e:
            _log(f"[feishu] 异常: {e}")
            return False

    @staticmethod
    def validate_credentials(app_id: str, app_secret: str) -> Dict[str, Any]:
        """验证飞书凭据是否有效"""
        body = json.dumps({
            "app_id": app_id,
            "app_secret": app_secret,
        }).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(FEISHU_TOKEN_URL, data=body, headers=headers, method="POST")

        try:
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("code") == 0:
                return {"ok": True, "message": "凭据验证成功"}
            return {"ok": False, "error": data.get("msg", "验证失败")}
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"HTTP {e.code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def get_login_status(config: Dict[str, Any]) -> Dict[str, Any]:
        fs_config = config.get("feishu", {})
        app_id = fs_config.get("app_id", "")
        app_secret = fs_config.get("app_secret", "")
        receive_id = fs_config.get("receive_id", "")

        if not app_id or not app_secret:
            return {"logged_in": False, "message": "未配置 App ID / App Secret"}

        return {
            "logged_in": True,
            "receive_id": receive_id or None,
            "message": "飞书已配置" if receive_id else "已配置凭据，请发送消息给 Bot 以获取 Open ID",
        }
```

- [ ] **Step 2: Commit**

```bash
git add channels/feishu.py
git commit -m "feat: add Feishu notification channel"
```

---

### Task 3: DingTalk Channel

**Files:**
- Create: `channels/dingtalk.py`

- [ ] **Step 1: Create `channels/dingtalk.py`**

```python
"""钉钉通知渠道。通过钉钉 Open API 发送消息，Stream 长连接接收回复。"""

import json
import time
import urllib.request
import urllib.error
from typing import Dict, Any, Optional
from .base import NotificationChannel

# 钉钉 API 常量
DINGTALK_TOKEN_URL = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
DINGTALK_MSG_URL = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"


def _log(msg: str):
    from pathlib import Path
    from datetime import datetime
    log_file = Path(__file__).resolve().parent.parent / "notify.log"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


class DingTalkChannel(NotificationChannel):
    """通过钉钉 Open API 发送消息"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._dt_config = config.get("dingtalk", {})
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0

    @property
    def name(self) -> str:
        return "dingtalk"

    def is_enabled(self) -> bool:
        return self._dt_config.get("enabled", False)

    def _get_access_token(self) -> Optional[str]:
        """获取 access_token（自动缓存）"""
        now = time.time()
        if self._access_token and now < self._token_expires_at:
            return self._access_token

        app_key = self._dt_config.get("app_key", "")
        app_secret = self._dt_config.get("app_secret", "")
        if not app_key or not app_secret:
            _log("[dingtalk] app_key 或 app_secret 为空")
            return None

        body = json.dumps({
            "appKey": app_key,
            "appSecret": app_secret,
        }).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(DINGTALK_TOKEN_URL, data=body, headers=headers, method="POST")

        try:
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode("utf-8"))
            self._access_token = data.get("accessToken", "")
            expires_in = int(data.get("expireIn", 7200))
            self._token_expires_at = now + expires_in - 300
            _log(f"[dingtalk] 获取 access_token 成功")
            return self._access_token
        except Exception as e:
            _log(f"[dingtalk] 获取 token 异常: {e}")
            return None

    def send(self, title: str, message: str) -> bool:
        token = self._get_access_token()
        if not token:
            return False

        robot_code = self._dt_config.get("app_key", "")
        user_id = self._dt_config.get("user_id", "")
        if not robot_code or not user_id:
            _log("[dingtalk] app_key 或 user_id 为空")
            return False

        full_text = f"【{title}】\n{message}"
        body = json.dumps({
            "robotCode": robot_code,
            "userIds": [user_id],
            "msgKey": "sampleText",
            "msgParam": json.dumps({"content": full_text}),
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "x-acs-dingtalk-access-token": token,
        }
        req = urllib.request.Request(DINGTALK_MSG_URL, data=body, headers=headers, method="POST")

        try:
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode("utf-8"))
            _log(f"[dingtalk] sendMessage ok")
            return True
        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            _log(f"[dingtalk] HTTPError {e.code}: {resp_body[:200]}")
            return False
        except Exception as e:
            _log(f"[dingtalk] 异常: {e}")
            return False

    @staticmethod
    def validate_credentials(app_key: str, app_secret: str) -> Dict[str, Any]:
        """验证钉钉凭据是否有效"""
        body = json.dumps({
            "appKey": app_key,
            "appSecret": app_secret,
        }).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(DINGTALK_TOKEN_URL, data=body, headers=headers, method="POST")

        try:
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("accessToken"):
                return {"ok": True, "message": "凭据验证成功"}
            return {"ok": False, "error": "未返回 accessToken"}
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"HTTP {e.code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def get_login_status(config: Dict[str, Any]) -> Dict[str, Any]:
        dt_config = config.get("dingtalk", {})
        app_key = dt_config.get("app_key", "")
        app_secret = dt_config.get("app_secret", "")
        user_id = dt_config.get("user_id", "")

        if not app_key or not app_secret:
            return {"logged_in": False, "message": "未配置 App Key / App Secret"}

        return {
            "logged_in": True,
            "user_id": user_id or None,
            "message": "钉钉已配置" if user_id else "已配置凭据，请发送消息给 Bot 以获取 User ID",
        }
```

- [ ] **Step 2: Commit**

```bash
git add channels/dingtalk.py
git commit -m "feat: add DingTalk notification channel"
```

---

### Task 4: Update channels/__init__.py

**Files:**
- Modify: `channels/__init__.py`

- [ ] **Step 1: Add imports and exports**

Replace the entire file with:

```python
from .base import NotificationChannel
from .windows_toast import WindowsToastChannel
from .weixin import WeixinChannel
from .qq import QQBotChannel
from .telegram import TelegramChannel
from .feishu import FeishuChannel
from .dingtalk import DingTalkChannel

__all__ = [
    "NotificationChannel",
    "WindowsToastChannel",
    "WeixinChannel",
    "QQBotChannel",
    "TelegramChannel",
    "FeishuChannel",
    "DingTalkChannel",
]
```

- [ ] **Step 2: Commit**

```bash
git add channels/__init__.py
git commit -m "feat: register new channels in __init__"
```

---

### Task 5: Extend Keepalive Daemon

**Files:**
- Modify: `weixin_keepalive.py`

- [ ] **Step 1: Add Telegram long polling loop**

Add this function after the `qq_thread_entry()` function (around line 436):

```python
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
```

- [ ] **Step 2: Add Feishu WebSocket loop**

Add this function after the Telegram loop:

```python
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
```

- [ ] **Step 3: Add DingTalk Stream loop**

Add this function after the Feishu loop:

```python
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
```

- [ ] **Step 4: Add thread entry functions and update main()**

Add thread entry functions after the DingTalk loop:

```python
def telegram_thread_entry():
    """Telegram 长轮询线程入口"""
    try:
        telegram_poll_loop()
    except Exception as e:
        log(f"[telegram] 线程异常退出: {e}")


def feishu_thread_entry():
    """飞书 WebSocket 线程入口"""
    try:
        feishu_websocket_loop()
    except Exception as e:
        log(f"[feishu] 线程异常退出: {e}")


def dingtalk_thread_entry():
    """钉钉 Stream 线程入口"""
    try:
        dingtalk_stream_loop()
    except Exception as e:
        log(f"[dingtalk] 线程异常退出: {e}")
```

Then update `main()` to start the new threads. Find this block:

```python
    # 启动 QQ WebSocket 线程（有凭据就启动，用于自动获取 user_openid）
    qq_thread = None
    cfg = load_config()
    if cfg.get("qq", {}).get("app_id") and cfg.get("qq", {}).get("app_secret"):
        qq_thread = threading.Thread(target=qq_thread_entry, daemon=True)
        qq_thread.start()
        log("[qq] WebSocket 监听线程已启动")
```

And add after it:

```python
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
```

Also update the join block at the end of `main()` to wait for all threads:

Find:
```python
        if qq_thread and qq_thread.is_alive():
            log("[qq] 微信保活已退出，继续等待 QQ 监听线程...")
            qq_thread.join()
```

Replace with:
```python
        # 等待所有监听线程退出
        for t in [qq_thread, tg_thread, fs_thread, dt_thread]:
            if t and t.is_alive():
                t.join()
```

- [ ] **Step 5: Commit**

```bash
git add weixin_keepalive.py
git commit -m "feat: add Feishu/DingTalk/Telegram listener threads to keepalive daemon"
```

---

### Task 6: Update notify.py

**Files:**
- Modify: `notify.py`

- [ ] **Step 1: Add imports**

Find:
```python
from channels.windows_toast import WindowsToastChannel
from channels.weixin import WeixinChannel
from channels.qq import QQBotChannel
```

Replace with:
```python
from channels.windows_toast import WindowsToastChannel
from channels.weixin import WeixinChannel
from channels.qq import QQBotChannel
from channels.telegram import TelegramChannel
from channels.feishu import FeishuChannel
from channels.dingtalk import DingTalkChannel
```

- [ ] **Step 2: Add DEFAULT_CONFIG entries**

Find the end of `DEFAULT_CONFIG` dict (the `}` after the `qq` section) and add the new sections:

```python
    "qq": {
        "enabled": False,
        "app_id": "",
        "app_secret": "",
        "target_id": "",
    },
    "telegram": {
        "enabled": False,
        "bot_token": "",
        "chat_id": "",
    },
    "feishu": {
        "enabled": False,
        "app_id": "",
        "app_secret": "",
        "receive_id": "",
    },
    "dingtalk": {
        "enabled": False,
        "app_key": "",
        "app_secret": "",
        "user_id": "",
    },
```

- [ ] **Step 3: Update collect_channels()**

Find:
```python
def collect_channels(config: dict):
    """收集所有已注册的通知渠道"""
    return [
        WindowsToastChannel(config),
        WeixinChannel(config),
        QQBotChannel(config),
    ]
```

Replace with:
```python
def collect_channels(config: dict):
    """收集所有已注册的通知渠道"""
    return [
        WindowsToastChannel(config),
        WeixinChannel(config),
        QQBotChannel(config),
        TelegramChannel(config),
        FeishuChannel(config),
        DingTalkChannel(config),
    ]
```

- [ ] **Step 4: Update keepalive startup condition**

Find:
```python
    wx_enabled = config.get("weixin", {}).get("enabled") and config.get("weixin", {}).get("bot_token")
    qq_enabled = config.get("qq", {}).get("enabled") and config.get("qq", {}).get("app_id")
    if wx_enabled or qq_enabled:
```

Replace with:
```python
    wx_enabled = config.get("weixin", {}).get("enabled") and config.get("weixin", {}).get("bot_token")
    qq_enabled = config.get("qq", {}).get("enabled") and config.get("qq", {}).get("app_id")
    tg_enabled = config.get("telegram", {}).get("enabled") and config.get("telegram", {}).get("bot_token")
    fs_enabled = config.get("feishu", {}).get("enabled") and config.get("feishu", {}).get("app_id")
    dt_enabled = config.get("dingtalk", {}).get("enabled") and config.get("dingtalk", {}).get("app_key")
    if wx_enabled or qq_enabled or tg_enabled or fs_enabled or dt_enabled:
```

- [ ] **Step 5: Commit**

```bash
git add notify.py
git commit -m "feat: register Feishu/DingTalk/Telegram in notify.py"
```

---

### Task 7: Update app.py — API Routes

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Add toggle validation for new channels**

In the `toggle_channel` function, find:
```python
            elif name == "qq":
                if not cfg["qq"].get("app_id") or not cfg["qq"].get("app_secret"):
                    return jsonify({"ok": False, "error": "请先配置 QQ Bot AppID 和 AppSecret"}), 400
                if not cfg["qq"].get("target_id"):
                    return jsonify({"ok": False, "error": "请先配置 Target ID"}), 400
```

Add after it:
```python
            elif name == "telegram":
                if not cfg["telegram"].get("bot_token"):
                    return jsonify({"ok": False, "error": "请先配置 Telegram Bot Token"}), 400
            elif name == "feishu":
                if not cfg["feishu"].get("app_id") or not cfg["feishu"].get("app_secret"):
                    return jsonify({"ok": False, "error": "请先配置飞书 App ID / App Secret"}), 400
            elif name == "dingtalk":
                if not cfg["dingtalk"].get("app_key") or not cfg["dingtalk"].get("app_secret"):
                    return jsonify({"ok": False, "error": "请先配置钉钉 App Key / App Secret"}), 400
```

- [ ] **Step 2: Add Telegram API routes**

After the `qq_logout` route (around line 268), add:

```python
    # --- Telegram 配置 ---
    @app.route("/api/telegram/validate", methods=["POST"])
    def telegram_validate():
        from channels.telegram import TelegramChannel
        from notify import load_config, save_config
        data = request.get_json(force=True)
        bot_token = data.get("bot_token", "").strip()
        if not bot_token:
            return jsonify({"ok": False, "error": "Bot Token 不能为空"}), 400

        result = TelegramChannel.validate_credentials(bot_token)
        if result.get("ok"):
            cfg = load_config()
            cfg["telegram"]["bot_token"] = bot_token
            save_config(cfg)
            _restart_keepalive()
        return jsonify(result)

    @app.route("/api/telegram/status", methods=["GET"])
    def telegram_status():
        from channels.telegram import TelegramChannel
        from notify import load_config
        cfg = load_config()
        return jsonify(TelegramChannel.get_login_status(cfg))

    @app.route("/api/telegram/logout", methods=["POST"])
    def telegram_logout():
        from notify import load_config, save_config
        cfg = load_config()
        cfg["telegram"]["bot_token"] = ""
        cfg["telegram"]["chat_id"] = ""
        cfg["telegram"]["enabled"] = False
        save_config(cfg)
        return jsonify({"ok": True, "message": "Telegram 信息已清除"})
```

- [ ] **Step 3: Add Feishu API routes**

After the Telegram routes, add:

```python
    # --- 飞书配置 ---
    @app.route("/api/feishu/validate", methods=["POST"])
    def feishu_validate():
        from channels.feishu import FeishuChannel
        from notify import load_config, save_config
        data = request.get_json(force=True)
        app_id = data.get("app_id", "").strip()
        app_secret = data.get("app_secret", "").strip()
        if not app_id or not app_secret:
            return jsonify({"ok": False, "error": "App ID 和 App Secret 不能为空"}), 400

        result = FeishuChannel.validate_credentials(app_id, app_secret)
        if result.get("ok"):
            cfg = load_config()
            cfg["feishu"]["app_id"] = app_id
            cfg["feishu"]["app_secret"] = app_secret
            save_config(cfg)
            _restart_keepalive()
        return jsonify(result)

    @app.route("/api/feishu/status", methods=["GET"])
    def feishu_status():
        from channels.feishu import FeishuChannel
        from notify import load_config
        cfg = load_config()
        return jsonify(FeishuChannel.get_login_status(cfg))

    @app.route("/api/feishu/logout", methods=["POST"])
    def feishu_logout():
        from notify import load_config, save_config
        cfg = load_config()
        cfg["feishu"]["app_id"] = ""
        cfg["feishu"]["app_secret"] = ""
        cfg["feishu"]["receive_id"] = ""
        cfg["feishu"]["enabled"] = False
        save_config(cfg)
        return jsonify({"ok": True, "message": "飞书信息已清除"})
```

- [ ] **Step 4: Add DingTalk API routes**

After the Feishu routes, add:

```python
    # --- 钉钉配置 ---
    @app.route("/api/dingtalk/validate", methods=["POST"])
    def dingtalk_validate():
        from channels.dingtalk import DingTalkChannel
        from notify import load_config, save_config
        data = request.get_json(force=True)
        app_key = data.get("app_key", "").strip()
        app_secret = data.get("app_secret", "").strip()
        if not app_key or not app_secret:
            return jsonify({"ok": False, "error": "App Key 和 App Secret 不能为空"}), 400

        result = DingTalkChannel.validate_credentials(app_key, app_secret)
        if result.get("ok"):
            cfg = load_config()
            cfg["dingtalk"]["app_key"] = app_key
            cfg["dingtalk"]["app_secret"] = app_secret
            save_config(cfg)
            _restart_keepalive()
        return jsonify(result)

    @app.route("/api/dingtalk/status", methods=["GET"])
    def dingtalk_status():
        from channels.dingtalk import DingTalkChannel
        from notify import load_config
        cfg = load_config()
        return jsonify(DingTalkChannel.get_login_status(cfg))

    @app.route("/api/dingtalk/logout", methods=["POST"])
    def dingtalk_logout():
        from notify import load_config, save_config
        cfg = load_config()
        cfg["dingtalk"]["app_key"] = ""
        cfg["dingtalk"]["app_secret"] = ""
        cfg["dingtalk"]["user_id"] = ""
        cfg["dingtalk"]["enabled"] = False
        save_config(cfg)
        return jsonify({"ok": True, "message": "钉钉信息已清除"})
```

- [ ] **Step 5: Add _restart_keepalive helper**

Before the toggle_channel route, add a helper function:

```python
    def _restart_keepalive():
        """重启 keepalive 守护进程"""
        try:
            from channels.weixin import start_keepalive, stop_keepalive
            stop_keepalive()
            import time; time.sleep(1)
            start_keepalive()
        except Exception:
            pass
```

Then update the QQ validate route to use this helper. Find:
```python
            try:
                from channels.weixin import start_keepalive, stop_keepalive
                stop_keepalive()
                import time; time.sleep(1)
                start_keepalive()
            except Exception:
                pass
```

Replace with:
```python
            _restart_keepalive()
```

- [ ] **Step 6: Commit**

```bash
git add app.py
git commit -m "feat: add Feishu/DingTalk/Telegram API routes to Web UI backend"
```

---

### Task 8: Update Web UI

**Files:**
- Modify: `static/index.html`

- [ ] **Step 1: Add Telegram tab**

In the tab navigation, add a new tab button for Telegram (after the QQ Bot tab):

```html
<button @click="activeTab='telegram'" :class="activeTab==='telegram' ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500'" class="whitespace-nowrap py-2 px-1 border-b-2 font-medium text-sm">Telegram</button>
```

Add the Telegram tab content panel:

```html
<!-- Telegram 配置 -->
<div x-show="activeTab==='telegram'" class="space-y-4">
    <h3 class="text-lg font-medium text-gray-900">Telegram Bot 配置</h3>
    <div class="bg-gray-50 rounded-lg p-4 space-y-3">
        <div>
            <label class="block text-sm font-medium text-gray-700">Bot Token</label>
            <input x-model="telegram.bot_token" type="password" placeholder="从 @BotFather 获取" class="mt-1 block w-full border border-gray-300 rounded-md shadow-sm py-2 px-3 focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm">
        </div>
        <div class="flex space-x-2">
            <button @click="validateTelegram()" class="bg-blue-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-blue-700">验证并保存</button>
            <button @click="logoutTelegram()" class="bg-gray-200 text-gray-700 px-4 py-2 rounded-md text-sm font-medium hover:bg-gray-300">清除</button>
        </div>
        <div x-show="telegram.status" class="text-sm" :class="telegram.status?.logged_in ? 'text-green-600' : 'text-gray-500'" x-text="telegram.status?.message"></div>
    </div>
</div>
```

- [ ] **Step 2: Add Feishu tab**

```html
<button @click="activeTab='feishu'" :class="activeTab==='feishu' ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500'" class="whitespace-nowrap py-2 px-1 border-b-2 font-medium text-sm">飞书</button>
```

```html
<!-- 飞书配置 -->
<div x-show="activeTab==='feishu'" class="space-y-4">
    <h3 class="text-lg font-medium text-gray-900">飞书 Bot 配置</h3>
    <div class="bg-gray-50 rounded-lg p-4 space-y-3">
        <div>
            <label class="block text-sm font-medium text-gray-700">App ID</label>
            <input x-model="feishu.app_id" type="text" placeholder="飞书开放平台 App ID" class="mt-1 block w-full border border-gray-300 rounded-md shadow-sm py-2 px-3 focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm">
        </div>
        <div>
            <label class="block text-sm font-medium text-gray-700">App Secret</label>
            <input x-model="feishu.app_secret" type="password" placeholder="飞书开放平台 App Secret" class="mt-1 block w-full border border-gray-300 rounded-md shadow-sm py-2 px-3 focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm">
        </div>
        <div class="flex space-x-2">
            <button @click="validateFeishu()" class="bg-blue-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-blue-700">验证并保存</button>
            <button @click="logoutFeishu()" class="bg-gray-200 text-gray-700 px-4 py-2 rounded-md text-sm font-medium hover:bg-gray-300">清除</button>
        </div>
        <div x-show="feishu.status" class="text-sm" :class="feishu.status?.logged_in ? 'text-green-600' : 'text-gray-500'" x-text="feishu.status?.message"></div>
    </div>
</div>
```

- [ ] **Step 3: Add DingTalk tab**

```html
<button @click="activeTab='dingtalk'" :class="activeTab==='dingtalk' ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500'" class="whitespace-nowrap py-2 px-1 border-b-2 font-medium text-sm">钉钉</button>
```

```html
<!-- 钉钉配置 -->
<div x-show="activeTab==='dingtalk'" class="space-y-4">
    <h3 class="text-lg font-medium text-gray-900">钉钉 Bot 配置</h3>
    <div class="bg-gray-50 rounded-lg p-4 space-y-3">
        <div>
            <label class="block text-sm font-medium text-gray-700">App Key</label>
            <input x-model="dingtalk.app_key" type="text" placeholder="钉钉开放平台 App Key" class="mt-1 block w-full border border-gray-300 rounded-md shadow-sm py-2 px-3 focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm">
        </div>
        <div>
            <label class="block text-sm font-medium text-gray-700">App Secret</label>
            <input x-model="dingtalk.app_secret" type="password" placeholder="钉钉开放平台 App Secret" class="mt-1 block w-full border border-gray-300 rounded-md shadow-sm py-2 px-3 focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm">
        </div>
        <div class="flex space-x-2">
            <button @click="validateDingTalk()" class="bg-blue-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-blue-700">验证并保存</button>
            <button @click="logoutDingTalk()" class="bg-gray-200 text-gray-700 px-4 py-2 rounded-md text-sm font-medium hover:bg-gray-300">清除</button>
        </div>
        <div x-show="dingtalk.status" class="text-sm" :class="dingtalk.status?.logged_in ? 'text-green-600' : 'text-gray-500'" x-text="dingtalk.status?.message"></div>
    </div>
</div>
```

- [ ] **Step 4: Add Alpine.js data and methods**

In the Alpine.js `x-data` object, add the new channel data:

```javascript
telegram: { bot_token: '', status: null },
feishu: { app_id: '', app_secret: '', status: null },
dingtalk: { app_key: '', app_secret: '', status: null },
```

Add the validation/logout methods:

```javascript
async validateTelegram() {
    const r = await fetch('/api/telegram/validate', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ bot_token: this.telegram.bot_token }) });
    const d = await r.json();
    if (d.ok) { this.telegram.bot_token = ''; this.loadTelegramStatus(); }
    else { alert(d.error); }
},
async loadTelegramStatus() {
    const r = await fetch('/api/telegram/status');
    this.telegram.status = await r.json();
},
async logoutTelegram() {
    await fetch('/api/telegram/logout', { method: 'POST' });
    this.telegram = { bot_token: '', status: null };
},

async validateFeishu() {
    const r = await fetch('/api/feishu/validate', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ app_id: this.feishu.app_id, app_secret: this.feishu.app_secret }) });
    const d = await r.json();
    if (d.ok) { this.feishu.app_id = ''; this.feishu.app_secret = ''; this.loadFeishuStatus(); }
    else { alert(d.error); }
},
async loadFeishuStatus() {
    const r = await fetch('/api/feishu/status');
    this.feishu.status = await r.json();
},
async logoutFeishu() {
    await fetch('/api/feishu/logout', { method: 'POST' });
    this.feishu = { app_id: '', app_secret: '', status: null };
},

async validateDingTalk() {
    const r = await fetch('/api/dingtalk/validate', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ app_key: this.dingtalk.app_key, app_secret: this.dingtalk.app_secret }) });
    const d = await r.json();
    if (d.ok) { this.dingtalk.app_key = ''; this.dingtalk.app_secret = ''; this.loadDingTalkStatus(); }
    else { alert(d.error); }
},
async loadDingTalkStatus() {
    const r = await fetch('/api/dingtalk/status');
    this.dingtalk.status = await r.json();
},
async logoutDingTalk() {
    await fetch('/api/dingtalk/logout', { method: 'POST' });
    this.dingtalk = { app_key: '', app_secret: '', status: null };
},
```

Add status loading calls in the `init()` function:

```javascript
this.loadTelegramStatus();
this.loadFeishuStatus();
this.loadDingTalkStatus();
```

- [ ] **Step 5: Add dashboard toggle cards**

In the dashboard channel cards section, add toggle cards for the three new channels (following the existing QQ card pattern):

```html
<!-- Telegram -->
<div class="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
    <div>
        <span class="font-medium text-gray-900">Telegram</span>
        <span class="text-xs text-gray-500 ml-2">Bot API</span>
    </div>
    <button @click="toggleChannel('telegram')" :class="config.telegram?.enabled ? 'bg-blue-600' : 'bg-gray-300'" class="relative inline-flex h-6 w-11 items-center rounded-full transition-colors">
        <span :class="config.telegram?.enabled ? 'translate-x-6' : 'translate-x-1'" class="inline-block h-4 w-4 transform rounded-full bg-white transition-transform"></span>
    </button>
</div>

<!-- 飞书 -->
<div class="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
    <div>
        <span class="font-medium text-gray-900">飞书</span>
        <span class="text-xs text-gray-500 ml-2">Open API</span>
    </div>
    <button @click="toggleChannel('feishu')" :class="config.feishu?.enabled ? 'bg-blue-600' : 'bg-gray-300'" class="relative inline-flex h-6 w-11 items-center rounded-full transition-colors">
        <span :class="config.feishu?.enabled ? 'translate-x-6' : 'translate-x-1'" class="inline-block h-4 w-4 transform rounded-full bg-white transition-transform"></span>
    </button>
</div>

<!-- 钉钉 -->
<div class="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
    <div>
        <span class="font-medium text-gray-900">钉钉</span>
        <span class="text-xs text-gray-500 ml-2">Stream API</span>
    </div>
    <button @click="toggleChannel('dingtalk')" :class="config.dingtalk?.enabled ? 'bg-blue-600' : 'bg-gray-300'" class="relative inline-flex h-6 w-11 items-center rounded-full transition-colors">
        <span :class="config.dingtalk?.enabled ? 'translate-x-6' : 'translate-x-1'" class="inline-block h-4 w-4 transform rounded-full bg-white transition-transform"></span>
    </button>
</div>
```

- [ ] **Step 6: Commit**

```bash
git add static/index.html
git commit -m "feat: add Feishu/DingTalk/Telegram config tabs to Web UI"
```

---

### Task 9: Update requirements.txt

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add new dependencies**

Append to `requirements.txt`:

```
lark-oapi>=1.0.0
dingtalk-stream>=1.0.0
```

- [ ] **Step 2: Commit**

```bash
git add requirements.txt
git commit -m "feat: add lark-oapi and dingtalk-stream dependencies"
```

---

### Task 10: Final Verification

- [ ] **Step 1: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: All packages install successfully.

- [ ] **Step 2: Run syntax check**

```bash
python -c "from channels.telegram import TelegramChannel; from channels.feishu import FeishuChannel; from channels.dingtalk import DingTalkChannel; print('All channels imported OK')"
```

Expected: `All channels imported OK`

- [ ] **Step 3: Run notify.py --test**

```bash
python notify.py --test
```

Expected: Test notification sent to all enabled channels (or "no enabled channels" if all disabled).

- [ ] **Step 4: Start Web UI and verify tabs**

```bash
python notify.py --ui
```

Expected: Web UI opens with 6 channel tabs (Windows Toast, WeChat, QQ Bot, Telegram, 飞书, 钉钉).

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat: multi-channel expansion complete (Feishu, DingTalk, Telegram)"
```
