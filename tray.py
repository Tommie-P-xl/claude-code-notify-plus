#!/usr/bin/env python3
"""ClaudeBeep Windows tray application."""

from __future__ import annotations

import ctypes
import json
import os
import ssl  # 提前导入，避免 PyInstaller --onefile 下 urllib 运行时从 base_library.zip 加载失败
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

APP_NAME = "ClaudeBeep"
APP_VERSION = "1.0.1"
SCRIPT_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", SCRIPT_DIR))
CONFIG_FILE = SCRIPT_DIR / "config.json"
HEARTBEAT_FILE = SCRIPT_DIR / "tray_heartbeat.json"
ICON_FILE = RESOURCE_DIR / "assets" / "icon.ico"

CHANNEL_LABELS = {
    "windows_toast": "Windows 通知",
    "weixin": "WeChat",
    "qq": "QQ Bot",
    "telegram": "Telegram",
    "feishu": "Feishu",
    "dingtalk": "DingTalk",
}

_mutex_handle = None
_ui_process: subprocess.Popen | None = None
_stop_event = threading.Event()


def main() -> None:
    if _should_delegate_to_notify():
        import notify
        notify.main()
        return

    if not _acquire_single_instance():
        _message_box("ClaudeBeep 已在运行。", APP_NAME, 0x40)
        return

    _ensure_runtime_dirs()
    _start_background_services()
    _run_tray()


def _should_delegate_to_notify() -> bool:
    args = set(sys.argv[1:])
    return bool(args & {"--type", "--install", "--uninstall", "--test", "--ui", "--from-stdin"})


def _ensure_runtime_dirs() -> None:
    (SCRIPT_DIR / "pending").mkdir(exist_ok=True)
    (SCRIPT_DIR / "responses").mkdir(exist_ok=True)
    (SCRIPT_DIR / "send_queue").mkdir(exist_ok=True)


def _acquire_single_instance() -> bool:
    global _mutex_handle
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    _mutex_handle = kernel32.CreateMutexW(None, False, "Global\\ClaudeBeepTray")
    return kernel32.GetLastError() != 183


def _start_background_services() -> None:
    threading.Thread(target=_heartbeat_loop, name="tray-heartbeat", daemon=True).start()
    threading.Thread(target=_cleanup_loop, name="cleanup", daemon=True).start()
    try:
        from channels.weixin import start_keepalive
        cfg = _load_config()
        if cfg.get("weixin", {}).get("enabled") and cfg.get("weixin", {}).get("bot_token"):
            start_keepalive()
    except Exception:
        pass


def _run_tray() -> None:
    try:
        import pystray
        from PIL import Image
    except Exception as exc:
        _message_box(f"托盘依赖缺失：{exc}", APP_NAME, 0x10)
        return

    image = Image.open(ICON_FILE if ICON_FILE.exists() else RESOURCE_DIR / "assets" / "icon.png")
    source_items = []
    for name, label in CHANNEL_LABELS.items():
        source_items.append(pystray.MenuItem(
            label,
            _make_toggle_action(name),
            checked=_make_checked_action(name),
            enabled=_make_enabled_action(name),
        ))

    menu = pystray.Menu(
        pystray.MenuItem("打开主界面", lambda icon, item: _open_ui()),
        pystray.MenuItem("通知源管理", pystray.Menu(*source_items)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("安装所有 Hook", lambda icon, item: threading.Thread(target=_install_hooks, daemon=True).start()),
        pystray.MenuItem("卸载所有 Hook", lambda icon, item: threading.Thread(target=_uninstall_hooks, daemon=True).start()),
        pystray.MenuItem(
            "开机自启动",
            lambda icon, item: _toggle_startup(icon),
            checked=lambda item: _is_startup_enabled(),
        ),
        pystray.MenuItem("检查更新", lambda icon, item: threading.Thread(target=_check_updates, daemon=True).start()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出托盘程序", lambda icon, item: _quit(icon)),
    )

    icon = pystray.Icon(APP_NAME, image, APP_NAME, menu)
    threading.Thread(target=_menu_refresh_loop, args=(icon,), name="menu-refresh", daemon=True).start()
    icon.run()


def _make_toggle_action(name: str):
    def _action(icon, item):
        _toggle_channel(name, icon)
    return _action


def _make_checked_action(name: str):
    def _checked(item):
        return _is_channel_enabled(name)
    return _checked


def _make_enabled_action(name: str):
    def _enabled(item):
        return _is_channel_configured(name)
    return _enabled


def _menu_refresh_loop(icon: Any) -> None:
    last_config_mtime = _mtime(CONFIG_FILE)
    last_startup_state = _is_startup_enabled()
    while not _stop_event.is_set():
        _stop_event.wait(2)
        config_mtime = _mtime(CONFIG_FILE)
        startup_state = _is_startup_enabled()
        if config_mtime != last_config_mtime or startup_state != last_startup_state:
            last_config_mtime = config_mtime
            last_startup_state = startup_state
            try:
                icon.update_menu()
            except Exception:
                pass


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _load_config() -> dict[str, Any]:
    import notify
    return notify.load_config()


def _save_config(cfg: dict[str, Any]) -> None:
    import notify
    notify.save_config(cfg)


def _is_channel_enabled(name: str) -> bool:
    return bool(_load_config().get(name, {}).get("enabled"))


def _is_channel_configured(name: str) -> bool:
    cfg = _load_config()
    data = cfg.get(name, {})
    if name == "windows_toast":
        return True
    if name == "weixin":
        return bool(data.get("bot_token") and data.get("to_user_id"))
    if name == "qq":
        return bool(data.get("app_id") and data.get("app_secret") and data.get("target_id"))
    if name == "telegram":
        return bool(data.get("bot_token") and data.get("chat_id"))
    if name == "feishu":
        return bool(data.get("app_id") and data.get("app_secret") and data.get("receive_id"))
    if name == "dingtalk":
        return bool(data.get("client_id") and data.get("client_secret") and data.get("user_id"))
    return False


def _toggle_channel(name: str, icon: Any = None) -> None:
    cfg = _load_config()
    cfg.setdefault(name, {})["enabled"] = not bool(cfg.get(name, {}).get("enabled"))
    _save_config(cfg)
    if name == "weixin":
        try:
            from channels.weixin import start_keepalive, stop_keepalive
            if cfg[name]["enabled"]:
                start_keepalive()
            else:
                stop_keepalive()
        except Exception:
            pass
    if icon:
        icon.update_menu()


def _install_hooks() -> None:
    try:
        import notify
        notify.install_hooks()
        _message_box("Claude Code hooks 已安装。", APP_NAME, 0x40)
    except Exception as exc:
        _message_box(f"安装 hooks 失败：\n{exc}", APP_NAME, 0x10)


def _uninstall_hooks() -> None:
    try:
        import notify
        notify.uninstall_hooks()
        _message_box("Claude Code hooks 已卸载。", APP_NAME, 0x40)
    except Exception as exc:
        _message_box(f"卸载 hooks 失败：\n{exc}", APP_NAME, 0x10)


def _open_ui() -> None:
    global _ui_process
    if _ui_process and _ui_process.poll() is None:
        webbrowser.open("http://localhost:5100")
        return
    if getattr(sys, "frozen", False):
        cmd = [str(Path(sys.executable).resolve()), "--ui"]
    else:
        cmd = [sys.executable, str(SCRIPT_DIR / "notify.py"), "--ui"]
    _ui_process = subprocess.Popen(cmd, cwd=str(SCRIPT_DIR), creationflags=_creationflags())


def _creationflags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def _is_startup_enabled() -> bool:
    if sys.platform != "win32":
        return False
    cfg = _load_config()
    if not cfg.get("app", {}).get("auto_start", False):
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except OSError:
        return False


def _toggle_startup(icon: Any = None) -> None:
    if sys.platform != "win32":
        return
    import winreg
    cfg = _load_config()
    app_cfg = cfg.setdefault("app", {})
    new_state = not _is_startup_enabled()
    app_cfg["auto_start"] = new_state
    _save_config(cfg)
    run_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_path, 0, winreg.KEY_SET_VALUE) as key:
        if not new_state:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except OSError:
                pass
        else:
            raw = sys.executable if getattr(sys, "frozen", False) else str(SCRIPT_DIR / "ClaudeBeep.exe")
            target = os.path.normpath(raw)  # 去除 \\?\ 前缀并规范化路径
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{target}"')
    if icon:
        icon.update_menu()


def _check_updates() -> None:
    cfg = _load_config()
    repo = cfg.get("app", {}).get("update_repo", "Tommie-P-xl/ClaudeBeep")
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"},
        )
        data = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"))
        latest = str(data.get("tag_name", "")).lstrip("v")
        if not latest or _version_tuple(latest) <= _version_tuple(APP_VERSION):
            _message_box("当前已是最新版本。", APP_NAME, 0x40)
            return
        if _message_box(f"检测到新版本 {latest}，是否现在安装？", APP_NAME, 0x24) != 6:
            return
        asset = _select_windows_asset(data.get("assets", []))
        if not asset:
            webbrowser.open(data.get("html_url", f"https://github.com/{repo}/releases/latest"))
            return
        _download_and_run_installer(asset["browser_download_url"], asset["name"])
    except Exception as exc:
        _message_box(f"检查更新失败：\n{exc}", APP_NAME, 0x10)


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for part in value.split("."):
        try:
            parts.append(int("".join(ch for ch in part if ch.isdigit()) or "0"))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _select_windows_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    for asset in assets:
        name = asset.get("name", "").lower()
        if name.endswith((".exe", ".msi")) and ("windows" in name or "setup" in name or "claudebeep" in name):
            return asset
    return None


def _download_and_run_installer(url: str, name: str) -> None:
    dest = Path(tempfile.gettempdir()) / name
    urllib.request.urlretrieve(url, dest)
    subprocess.Popen([str(dest)], cwd=str(dest.parent), creationflags=_creationflags())
    _message_box("安装程序已启动，ClaudeBeep 将退出。", APP_NAME, 0x40)
    os._exit(0)


def _heartbeat_loop() -> None:
    while not _stop_event.is_set():
        try:
            from channels.weixin import get_keepalive_status
            status = get_keepalive_status()
            HEARTBEAT_FILE.write_text(json.dumps({
                "ts": time.time(),
                "pid": os.getpid(),
                "weixin_keepalive": bool(status.get("running")),
            }), encoding="utf-8")
        except Exception:
            pass
        _stop_event.wait(15)


def _cleanup_loop() -> None:
    while not _stop_event.is_set():
        try:
            _cleanup_runtime_files()
        except Exception:
            pass
        cfg = _load_config()
        hours = int(cfg.get("app", {}).get("cleanup_interval_hours", 12) or 12)
        _stop_event.wait(max(1, hours) * 3600)


def _cleanup_runtime_files() -> None:
    import interaction
    interaction.cleanup_stale()
    now = time.time()
    for folder, max_age in ((SCRIPT_DIR, 24 * 3600), (SCRIPT_DIR / "responses", 7 * 24 * 3600)):
        if not folder.exists():
            continue
        for path in folder.glob("*.tmp"):
            _safe_unlink(path, now, max_age)
        if folder.name == "responses":
            for path in folder.glob("*.json"):
                _safe_unlink(path, now, max_age)
    # 清理微信发送队列中的过期文件
    send_queue = SCRIPT_DIR / "send_queue"
    if send_queue.exists():
        for path in send_queue.glob("*"):
            _safe_unlink(path, now, 120)
        try:
            if not any(send_queue.iterdir()):
                send_queue.rmdir()
        except Exception:
            pass
    _trim_log(SCRIPT_DIR / "notify.log", max_lines=1200)


def _safe_unlink(path: Path, now: float, max_age: int) -> None:
    try:
        if now - path.stat().st_mtime < max_age:
            return
        with open(path, "a", encoding="utf-8"):
            pass
        path.unlink(missing_ok=True)
    except Exception:
        pass


def _trim_log(path: Path, max_lines: int) -> None:
    try:
        if not path.exists() or time.time() - path.stat().st_mtime < 60:
            return
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) <= max_lines:
            return
        path.write_text("\n".join(lines[-max_lines:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def _quit(icon: Any) -> None:
    _stop_event.set()
    try:
        from channels.weixin import stop_keepalive
        stop_keepalive()
    except Exception:
        pass
    try:
        HEARTBEAT_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    icon.stop()


def _message_box(text: str, title: str, flags: int) -> int:
    if sys.platform == "win32":
        return ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    print(f"{title}: {text}")
    return 0


if __name__ == "__main__":
    main()
