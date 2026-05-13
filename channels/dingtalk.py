"""钉钉通知渠道。通过钉钉 Open API 发送消息，Stream 长连接接收回复。"""

import json
import sys
import time
import urllib.request
import urllib.error
from typing import Dict, Any, Optional
from .base import NotificationChannel
from .text import sanitize_text

DINGTALK_TOKEN_URL = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
DINGTALK_MSG_URL = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"


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

        client_id = self._dt_config.get("client_id", "")
        client_secret = self._dt_config.get("client_secret", "")
        if not client_id or not client_secret:
            _log("[dingtalk] client_id 或 client_secret 为空")
            return None

        body = json.dumps({
            "appKey": client_id,
            "appSecret": client_secret,
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

        client_id = self._dt_config.get("client_id", "")
        user_id = self._dt_config.get("user_id", "")
        if not client_id or not user_id:
            _log("[dingtalk] client_id 或 user_id 为空")
            return False

        full_text = sanitize_text(f"【{title}】\n{message}")
        body = json.dumps({
            "robotCode": client_id,
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
    def validate_credentials(client_id: str, client_secret: str) -> Dict[str, Any]:
        """验证钉钉凭据是否有效"""
        body = json.dumps({
            "appKey": client_id,
            "appSecret": client_secret,
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
        client_id = dt_config.get("client_id", "")
        client_secret = dt_config.get("client_secret", "")
        user_id = dt_config.get("user_id", "")

        if not client_id or not client_secret:
            return {"logged_in": False, "message": "未配置 Client ID / Client Secret"}

        return {
            "logged_in": True,
            "user_id": user_id or None,
            "message": "钉钉已配置" if user_id else "已配置凭据，请发送消息给 Bot 以获取 User ID",
        }
