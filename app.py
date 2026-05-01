"""Flask 后端 — Claude Code 通知管理器 Web UI。"""

import json
import sys
import time
import threading
import subprocess
import uuid
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory, Response

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
LOG_FILE = SCRIPT_DIR / "notify.log"
CLAUDECODE_SETTINGS = Path.home() / ".claude" / "settings.json"

sys.path.insert(0, str(SCRIPT_DIR))

# SSE 连接跟踪：浏览器关闭后自动退出
_sse_connections = set()
_sse_lock = threading.Lock()
_SSE_SHUTDOWN_DELAY = 2  # 秒，所有连接断开后等待时间


def _shutdown_after_all_disconnect():
    """所有 SSE 连接断开后延迟退出"""
    time.sleep(_SSE_SHUTDOWN_DELAY)
    with _sse_lock:
        if len(_sse_connections) > 0:
            return  # 有新连接，不退出
    print(f"\n[INFO] 所有浏览器标签页已关闭，自动退出。")
    try:
        from channels.weixin import stop_keepalive
        stop_keepalive()
    except Exception:
        pass
    import os
    os._exit(0)


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(SCRIPT_DIR / "static"))

    # 启动微信 session 保活守护进程（如果已有 token）
    try:
        from notify import load_config
        from channels.weixin import start_keepalive, stop_keepalive
        cfg = load_config()
        if cfg.get("weixin", {}).get("bot_token"):
            start_keepalive()
    except Exception:
        pass

    # --- 静态文件 ---
    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    # --- SSE 持久连接（标签页关闭检测） ---
    @app.route("/api/stream")
    def stream():
        conn_id = str(uuid.uuid4())
        with _sse_lock:
            _sse_connections.add(conn_id)

        def generate():
            try:
                while True:
                    yield f"data: {json.dumps({'ts': time.time()})}\n\n"
                    time.sleep(3)
            except GeneratorExit:
                pass
            finally:
                with _sse_lock:
                    _sse_connections.discard(conn_id)
                if len(_sse_connections) == 0:
                    try:
                        threading.Thread(target=_shutdown_after_all_disconnect, daemon=True).start()
                    except RuntimeError:
                        pass

        return Response(generate(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # --- 配置 API ---
    @app.route("/api/config", methods=["GET"])
    def get_config():
        from notify import load_config
        cfg = load_config()
        safe = json.loads(json.dumps(cfg))
        # 脱敏
        if safe.get("weixin", {}).get("bot_token"):
            t = safe["weixin"]["bot_token"]
            safe["weixin"]["bot_token"] = t[:8] + "..." if len(t) > 8 else "***"
        if safe.get("qq", {}).get("app_secret"):
            s = safe["qq"]["app_secret"]
            safe["qq"]["app_secret"] = s[:4] + "****" if len(s) > 4 else "***"
        return jsonify(safe)

    @app.route("/api/config", methods=["PUT"])
    def update_config():
        from notify import load_config, save_config
        data = request.get_json(force=True)
        cfg = load_config()
        # 敏感字段：空值不覆盖已有值
        SENSITIVE_KEYS = {"bot_token", "app_secret", "app_id"}
        for channel_name, channel_conf in data.items():
            if channel_name in cfg and isinstance(cfg[channel_name], dict):
                for k, v in channel_conf.items():
                    if k in SENSITIVE_KEYS and (v is None or v == ""):
                        continue  # 跳过空值，保留已有配置
                    cfg[channel_name][k] = v
            else:
                cfg[channel_name] = channel_conf
        save_config(cfg)
        return jsonify({"ok": True, "message": "配置已保存"})

    # --- 通知渠道开关 ---
    @app.route("/api/channel/<name>/toggle", methods=["POST"])
    def toggle_channel(name: str):
        from notify import load_config, save_config
        data = request.get_json(force=True)
        enabled = data.get("enabled", False)
        cfg = load_config()

        if name not in cfg:
            return jsonify({"ok": False, "error": f"未知渠道: {name}"}), 400

        if enabled:
            if name == "weixin":
                if not cfg["weixin"].get("bot_token"):
                    return jsonify({"ok": False, "error": "请先完成微信扫码登录"}), 400
                if not cfg["weixin"].get("to_user_id"):
                    return jsonify({"ok": False, "error": "请先配置接收用户 ID（to_user_id）"}), 400
            elif name == "qq":
                if not cfg["qq"].get("app_id") or not cfg["qq"].get("app_secret"):
                    return jsonify({"ok": False, "error": "请先配置 QQ Bot AppID 和 AppSecret"}), 400
                if not cfg["qq"].get("target_id"):
                    return jsonify({"ok": False, "error": "请先配置 Target ID"}), 400

        cfg[name]["enabled"] = enabled
        save_config(cfg)
        status = "启用" if enabled else "禁用"
        return jsonify({"ok": True, "message": f"{name} 通知已{status}"})

    # --- 测试通知 ---
    @app.route("/api/test", methods=["POST"])
    def test_notification():
        from notify import load_config, collect_channels
        cfg = load_config()
        results = []
        for ch in collect_channels(cfg):
            if ch.is_enabled():
                ok = ch.send("Claude Code 测试", "这是一条来自 Web UI 的测试通知")
                results.append({"channel": ch.name, "success": ok})
        return jsonify({"ok": True, "results": results})

    # --- 微信登录（直接 ilink API） ---
    @app.route("/api/weixin/qr", methods=["POST"])
    def weixin_qr_login():
        from channels.weixin import WeixinChannel
        result = WeixinChannel.start_qr_login()
        status = 200 if result.get("ok") else 400
        return jsonify(result), status

    @app.route("/api/weixin/qr/status", methods=["GET"])
    def weixin_qr_status():
        from channels.weixin import WeixinChannel
        from notify import load_config, save_config
        status = WeixinChannel.get_qr_status()

        # 登录成功后自动更新 config.json
        if status.get("status") == "confirmed" and status.get("bot_token"):
            cfg = load_config()
            cfg["weixin"]["bot_token"] = status["bot_token"]
            cfg["weixin"]["baseurl"] = status.get("baseurl", "https://ilinkai.weixin.qq.com")
            cfg["weixin"]["ilink_bot_id"] = status.get("ilink_bot_id", "")
            cfg["weixin"]["ilink_user_id"] = status.get("ilink_user_id", "")
            # 默认 to_user_id 为扫码用户自己
            if not cfg["weixin"].get("to_user_id") and status.get("ilink_user_id"):
                cfg["weixin"]["to_user_id"] = status["ilink_user_id"]
            save_config(cfg)

        return jsonify(status)

    @app.route("/api/weixin/status", methods=["GET"])
    def weixin_status():
        from channels.weixin import WeixinChannel
        from notify import load_config
        cfg = load_config()
        return jsonify(WeixinChannel.get_login_status(cfg))

    @app.route("/api/weixin/logout", methods=["POST"])
    def weixin_logout():
        from channels.weixin import WeixinChannel, stop_keepalive
        from notify import load_config, save_config
        stop_keepalive()
        WeixinChannel.clear_login()
        cfg = load_config()
        cfg["weixin"]["bot_token"] = ""
        cfg["weixin"]["baseurl"] = "https://ilinkai.weixin.qq.com"
        cfg["weixin"]["ilink_bot_id"] = ""
        cfg["weixin"]["ilink_user_id"] = ""
        cfg["weixin"]["to_user_id"] = ""
        cfg["weixin"]["enabled"] = False
        save_config(cfg)
        return jsonify({"ok": True, "message": "微信登录信息已清除"})

    # --- QQ 登录 ---
    @app.route("/api/qq/validate", methods=["POST"])
    def qq_validate():
        from channels.qq import QQBotChannel
        from notify import load_config, save_config
        data = request.get_json(force=True)
        app_id = data.get("app_id", "").strip()
        app_secret = data.get("app_secret", "").strip()
        target_id = data.get("target_id", "").strip()

        if not app_id or not app_secret:
            return jsonify({"ok": False, "error": "AppID 和 AppSecret 不能为空"}), 400

        result = QQBotChannel.validate_credentials(app_id, app_secret)
        if result.get("ok"):
            cfg = load_config()
            cfg["qq"]["app_id"] = app_id
            cfg["qq"]["app_secret"] = app_secret
            if target_id:
                cfg["qq"]["target_id"] = target_id
            save_config(cfg)
            # 强制重启 keepalive 守护进程（含 QQ WebSocket 监听）
            try:
                from channels.weixin import start_keepalive, stop_keepalive
                stop_keepalive()
                import time; time.sleep(1)
                start_keepalive()
            except Exception:
                pass
        return jsonify(result)

    @app.route("/api/qq/status", methods=["GET"])
    def qq_status():
        from channels.qq import QQBotChannel
        from notify import load_config
        cfg = load_config()
        return jsonify(QQBotChannel.get_login_status(cfg))

    @app.route("/api/qq/save_target", methods=["POST"])
    def qq_save_target():
        from notify import load_config, save_config
        data = request.get_json(force=True)
        target_id = data.get("target_id", "").strip()
        if not target_id:
            return jsonify({"ok": False, "error": "Target ID 不能为空"}), 400
        cfg = load_config()
        cfg["qq"]["target_id"] = target_id
        save_config(cfg)
        return jsonify({"ok": True, "message": "Target ID 已保存"})

    @app.route("/api/qq/logout", methods=["POST"])
    def qq_logout():
        from notify import load_config, save_config
        cfg = load_config()
        cfg["qq"]["app_id"] = ""
        cfg["qq"]["app_secret"] = ""
        cfg["qq"]["target_id"] = ""
        cfg["qq"]["enabled"] = False
        save_config(cfg)
        return jsonify({"ok": True, "message": "QQ Bot 信息已清除"})

    # --- Hooks 管理 ---
    @app.route("/api/hooks", methods=["GET"])
    def get_hooks():
        from notify import NOTIFY_HOOK_EVENTS
        hooks_info = {"installed": False, "events": {}}
        if CLAUDECODE_SETTINGS.exists():
            try:
                with open(CLAUDECODE_SETTINGS, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                hooks = settings.get("hooks", {})
                for event in NOTIFY_HOOK_EVENTS:
                    entries = hooks.get(event, [])
                    has_notify = False
                    for entry in entries:
                        cmds = _extract_commands(entry)
                        if any("notify" in c.lower() for c in cmds):
                            has_notify = True
                            break
                    hooks_info["events"][event] = has_notify
                hooks_info["installed"] = any(hooks_info["events"].values())
            except Exception:
                pass
        return jsonify(hooks_info)

    @app.route("/api/hooks/install", methods=["POST"])
    def install_hooks():
        from notify import install_hooks as do_install
        try:
            do_install()
            return jsonify({"ok": True, "message": "Hooks 已安装"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/hooks/uninstall", methods=["POST"])
    def uninstall_hooks():
        from notify import uninstall_hooks as do_uninstall
        try:
            do_uninstall()
            return jsonify({"ok": True, "message": "Hooks 已卸载"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # --- 权限模式管理 ---
    @app.route("/api/permission-mode", methods=["GET"])
    def get_permission_mode():
        settings = {}
        try:
            if CLAUDECODE_SETTINGS.exists():
                with open(CLAUDECODE_SETTINGS, "r", encoding="utf-8") as f:
                    settings = json.load(f)
        except Exception:
            pass
        mode = settings.get("permissions", {}).get("defaultMode", "default")
        return jsonify({"mode": mode})

    @app.route("/api/permission-mode", methods=["PUT"])
    def set_permission_mode():
        data = request.get_json(force=True)
        mode = data.get("mode", "default")
        if mode not in ("default", "acceptEdits", "bypassPermissions"):
            return jsonify({"ok": False, "error": f"无效模式: {mode}"}), 400

        settings = {}
        try:
            if CLAUDECODE_SETTINGS.exists():
                with open(CLAUDECODE_SETTINGS, "r", encoding="utf-8") as f:
                    settings = json.load(f)
        except Exception:
            pass

        permissions = settings.setdefault("permissions", {})
        if mode == "default":
            permissions.pop("defaultMode", None)
        else:
            permissions["defaultMode"] = mode

        try:
            CLAUDECODE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            with open(CLAUDECODE_SETTINGS, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
            return jsonify({"ok": True, "message": f"权限模式已切换为 {mode}"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # --- 交互功能配置 ---
    @app.route("/api/interaction", methods=["GET", "POST"])
    def api_interaction():
        """交互功能配置 API"""
        from notify import load_config, save_config
        if request.method == "GET":
            cfg = load_config()
            interaction = cfg.get("interaction", {
                "enabled": False,
                "timeout_seconds": 0,
                "show_in_terminal": True,
            })
            return jsonify(interaction)

        data = request.get_json(silent=True) or {}
        cfg = load_config()
        interaction = cfg.get("interaction", {})
        if "enabled" in data:
            interaction["enabled"] = bool(data["enabled"])
        if "timeout_seconds" in data:
            interaction["timeout_seconds"] = max(0, int(data["timeout_seconds"]))
        if "show_in_terminal" in data:
            interaction["show_in_terminal"] = bool(data["show_in_terminal"])
        cfg["interaction"] = interaction
        save_config(cfg)
        return jsonify({"ok": True, "interaction": interaction})

    # --- 系统状态 ---
    @app.route("/api/status", methods=["GET"])
    def system_status():
        from notify import load_config
        cfg = load_config()

        return jsonify({
            "python_version": sys.version,
            "config_exists": CONFIG_FILE.exists(),
            "hooks_installed": _check_hooks_installed(),
            "channels": {
                "windows_toast": cfg.get("windows_toast", {}).get("enabled", False),
                "weixin": cfg.get("weixin", {}).get("enabled", False),
                "qq": cfg.get("qq", {}).get("enabled", False),
            },
        })

    # --- 日志 ---
    @app.route("/api/logs", methods=["GET"])
    def get_logs():
        lines = int(request.args.get("lines", 50))
        if not LOG_FILE.exists():
            return jsonify({"lines": []})
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
            return jsonify({"lines": [l.rstrip() for l in all_lines[-lines:]]})
        except Exception:
            return jsonify({"lines": []})

    @app.route("/api/logs/clear", methods=["POST"])
    def clear_logs():
        try:
            if LOG_FILE.exists():
                LOG_FILE.write_text("", encoding="utf-8")
            return jsonify({"ok": True, "message": "日志已清除"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    return app


def _extract_commands(entry: dict) -> list:
    if "hooks" in entry and isinstance(entry["hooks"], list):
        return [h.get("command", "") for h in entry["hooks"] if h.get("type") == "command"]
    if "command" in entry:
        return [entry["command"]]
    return []


def _check_hooks_installed() -> bool:
    if not CLAUDECODE_SETTINGS.exists():
        return False
    try:
        with open(CLAUDECODE_SETTINGS, "r", encoding="utf-8") as f:
            settings = json.load(f)
        hooks = settings.get("hooks", {})
        from notify import NOTIFY_HOOK_EVENTS
        for event in NOTIFY_HOOK_EVENTS:
            for entry in hooks.get(event, []):
                cmds = _extract_commands(entry)
                if any("notify" in c.lower() for c in cmds):
                    return True
    except Exception:
        pass
    return False


if __name__ == "__main__":
    import webbrowser
    app = create_app()
    webbrowser.open("http://localhost:5100")
    app.run(host="127.0.0.1", port=5100, debug=False)
