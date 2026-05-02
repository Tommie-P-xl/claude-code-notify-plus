"""飞书通知渠道。通过飞书 Open API 发送消息，WebSocket 长连接接收回复。"""

import json
import urllib.request
import urllib.error
from typing import Dict, Any, Optional
from .base import NotificationChannel

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
