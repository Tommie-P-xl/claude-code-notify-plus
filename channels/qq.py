"""QQ Bot 通知渠道。直接调用 QQ Bot API 发送消息，不依赖 openclaw。"""

import json
import time
import urllib.request
import urllib.error
from typing import Dict, Any, Optional
from .base import NotificationChannel

# QQ Bot API 常量
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
API_BASE = "https://api.sgroup.qq.com"


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


class QQBotChannel(NotificationChannel):
    """通过 QQ Bot API 直接发送消息"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._qq_config = config.get("qq", {})
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0

    @property
    def name(self) -> str:
        return "qq"

    def is_enabled(self) -> bool:
        return self._qq_config.get("enabled", False)

    def _get_access_token(self) -> Optional[str]:
        """获取 access_token（自动缓存和刷新）"""
        now = time.time()
        if self._access_token and now < self._token_expires_at:
            return self._access_token

        app_id = self._qq_config.get("app_id", "")
        app_secret = self._qq_config.get("app_secret", "")

        if not app_id or not app_secret:
            _log("[qq] app_id 或 app_secret 为空")
            return None

        body = json.dumps({
            "appId": app_id,
            "clientSecret": app_secret,
        }).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(TOKEN_URL, data=body, headers=headers, method="POST")

        try:
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode("utf-8"))
            self._access_token = data.get("access_token", "")
            expires_in = int(data.get("expires_in", 7200))
            # 提前 5 分钟刷新
            self._token_expires_at = now + expires_in - 300
            _log(f"[qq] 获取 access_token 成功，有效期 {expires_in}s")
            return self._access_token
        except Exception as e:
            _log(f"[qq] 获取 access_token 失败: {e}")
            return None

    def _parse_target(self, target_id: str) -> tuple:
        """解析 Target ID，返回 (type, id)"""
        target = target_id.strip()
        if target.startswith("qqbot:c2c:"):
            return ("c2c", target[len("qqbot:c2c:"):])
        if target.startswith("qqbot:group:"):
            return ("group", target[len("qqbot:group:"):])
        if target.startswith("c2c:"):
            return ("c2c", target[len("c2c:"):])
        if target.startswith("group:"):
            return ("group", target[len("group:"):])
        # 默认为 c2c（私聊）
        return ("c2c", target)

    def send(self, title: str, message: str) -> bool:
        """通过 QQ Bot API 发送消息"""
        token = self._get_access_token()
        if not token:
            return False

        target_id = self._qq_config.get("target_id", "")
        if not target_id:
            _log("[qq] target_id 为空")
            return False

        target_type, openid = self._parse_target(target_id)
        full_text = f"【{title}】\n{message}"

        # 构建请求 URL
        if target_type == "group":
            url = f"{API_BASE}/v2/groups/{openid}/messages"
        else:
            url = f"{API_BASE}/v2/users/{openid}/messages"

        body = json.dumps({
            "content": full_text,
            "msg_type": 0,
        }, ensure_ascii=False).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"QQBot {token}",
        }

        _log(f"[qq] POST {url}")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")

        try:
            resp = urllib.request.urlopen(req, timeout=15)
            resp_body = resp.read().decode("utf-8", errors="replace")
            _log(f"[qq] HTTP {resp.status} | 响应: {resp_body[:200]}")
            return 200 <= resp.status < 300
        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            _log(f"[qq] HTTPError {e.code} | 响应: {resp_body[:200]}")
            return False
        except Exception as e:
            _log(f"[qq] 异常: {e}")
            return False

    @staticmethod
    def validate_credentials(app_id: str, app_secret: str) -> Dict[str, Any]:
        """验证 QQ Bot 凭据是否有效（通过获取 access_token）"""
        body = json.dumps({
            "appId": app_id,
            "clientSecret": app_secret,
        }).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(TOKEN_URL, data=body, headers=headers, method="POST")

        try:
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("access_token"):
                return {"ok": True, "message": "凭据验证成功"}
            return {"ok": False, "error": "未返回 access_token"}
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"HTTP {e.code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def get_login_status(config: Dict[str, Any]) -> Dict[str, Any]:
        """获取 QQ Bot 配置状态"""
        qq_config = config.get("qq", {})
        app_id = qq_config.get("app_id", "")
        app_secret = qq_config.get("app_secret", "")
        target_id = qq_config.get("target_id", "")

        if not app_id or not app_secret:
            return {"logged_in": False, "message": "未配置 AppID/AppSecret"}

        return {
            "logged_in": True,
            "app_id": app_id,
            "target_id": target_id or None,
            "message": "QQ Bot 已配置" if target_id else "已配置凭据，请设置 Target ID",
        }
