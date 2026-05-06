"""Windows 原生 Toast 通知渠道。优先使用 winotify（快），回退到 PowerShell + WinRT（慢）。"""

import subprocess
from typing import Dict, Any
from .base import NotificationChannel

# 尝试导入 winotify（快速方案）
try:
    from winotify import Notification as WinNotification, audio as win_audio
    HAS_WINOTIFY = True
except ImportError:
    HAS_WINOTIFY = False

# 可用的提示音映射
_SOUND_MAP = {
    "default": "Default",
    "reminder": "Reminder",
    "alarm": "LoopingAlarm",
    "call": "LoopingCall",
    "mail": "Mail",
    "im": "IM",
    "sms": "SMS",
    "silent": "Silent",
}


class WindowsToastChannel(NotificationChannel):
    """发送 Windows Toast 通知"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._toast_config = config.get("windows_toast", {})
        self._app_id = "Claude Code"

    @property
    def name(self) -> str:
        return "windows_toast"

    def is_enabled(self) -> bool:
        return self._toast_config.get("enabled", True)

    def send(self, title: str, message: str) -> bool:
        """发送 Windows Toast 通知"""
        if HAS_WINOTIFY:
            return self._send_winotify(title, message)
        return self._send_powershell(title, message)

    def _send_winotify(self, title: str, message: str) -> bool:
        """使用 winotify 发送（快速，无 PowerShell 开销）"""
        try:
            toast = WinNotification(
                app_id=self._app_id,
                title=title,
                msg=message,
                duration="short",
            )
            # 设置提示音（默认使用 Reminder，比 Default 更明显）
            sound_name = self._toast_config.get("sound", "reminder").lower()
            sound_attr = _SOUND_MAP.get(sound_name, "Reminder")
            audio_obj = getattr(win_audio, sound_attr, win_audio.Reminder)
            toast.set_audio(audio_obj, loop=False)
            toast.show()
            return True
        except Exception as e:
            print(f"[WARN] winotify error: {e}")
            return False

    def _send_powershell(self, title: str, message: str) -> bool:
        """回退方案：PowerShell + WinRT"""
        duration_ms = self._toast_config.get("duration_ms", 5000)
        sound_name = self._toast_config.get("sound", "reminder").lower()
        sound_attr = _SOUND_MAP.get(sound_name, "Reminder")
        audio_src = f"ms-winsoundevent:Notification.{sound_attr}"
        toast_xml = f"""<toast duration="short">
  <visual>
    <binding template="ToastText02">
      <text id="1">{self._escape_xml(title)}</text>
      <text id="2">{self._escape_xml(message)}</text>
    </binding>
  </visual>
  <audio src="{audio_src}" />
</toast>"""
        ps_script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('{toast_xml}')
$toast = New-Object Windows.UI.Notifications.ToastNotification($xml)
$toast.ExpirationTime = [DateTimeOffset]::Now.AddMilliseconds({duration_ms})
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{self._app_id}").Show($toast)
"""
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                print(f"[WARN] Windows toast PowerShell error: {result.stderr.strip()}")
                return False
            return True
        except subprocess.TimeoutExpired:
            print("[WARN] Windows toast PowerShell timed out")
            return False
        except FileNotFoundError:
            print("[WARN] PowerShell not found on this system")
            return False

    @staticmethod
    def _escape_xml(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")
