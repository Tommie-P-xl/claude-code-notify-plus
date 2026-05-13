"""Telegram Bot 通知渠道。通过 Telegram Bot API 发送消息，长轮询接收回复。"""

import json
import sys
import time
import urllib.request
import urllib.error
from typing import Dict, Any, Optional
from .base import NotificationChannel
from .text import sanitize_text

BOT_API_BASE = "https://api.telegram.org"


def _log(msg: str):
    from pathlib import Path
    from datetime import datetime
    base_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    log_file = base_dir / "notify.log"
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

        full_text = sanitize_text(f"【{title}】\n{message}")
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
