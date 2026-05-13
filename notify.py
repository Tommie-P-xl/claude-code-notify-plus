#!/usr/bin/env python3
"""
Claude Code 通知管理器。
当 Claude Code 完成响应、弹出询问或执行工具时，自动发送通知到 Windows Toast、微信和/或 QQ。
通过 Claude Code hooks 机制自动触发，支持多渠道扩展。

用法:
  python notify.py --type stop [--message "可选消息"]
  python notify.py --type ask  [--message "可选消息"]
  python notify.py --install   安装 Claude Code hooks 配置
  python notify.py --uninstall 卸载 Claude Code hooks 配置
  python notify.py --test      测试所有已启用渠道
  python notify.py --ui        启动 Web 管理界面
"""

import sys
import os
import json
import argparse
from pathlib import Path

SCRIPT_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from channels.text import sanitize_data, sanitize_text

# Channel imports are deferred to collect_channels() to speed up hook cold-start.
# Only the text utilities are needed at module level for sanitize_text/sanitize_data.

CONFIG_FILE = SCRIPT_DIR / "config.json"

DEFAULT_CONFIG = {
    "app": {
        "version": "1.0.1",
        "auto_start": False,
        "auto_cleanup": True,
        "cleanup_interval_hours": 12,
        "update_repo": "Tommie-P-xl/ClaudeBeep",
    },
    "windows_toast": {
        "enabled": True,
        "duration_ms": 5000,
    },
    "weixin": {
        "enabled": False,
        "bot_token": "",
        "baseurl": "https://ilinkai.weixin.qq.com",
        "ilink_bot_id": "",
        "ilink_user_id": "",
        "to_user_id": "",
        "context_token": "",
        "sync_buf": "",
        "session_expired": False,
    },
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
        "client_id": "",
        "client_secret": "",
        "user_id": "",
    },
    "interaction": {
        "enabled": True,
        "timeout_seconds": 0,
        "show_in_terminal": True,
    },
}

CLAUDECODE_SETTINGS = Path.home() / ".claude" / "settings.json"
LOG_FILE = SCRIPT_DIR / "notify.log"
PYTHON_EXE = str(sys.executable).replace(chr(92), "/")

# 需要通知的 hook 事件
# 只在 PermissionRequest 触发时通知（用户需要手动批准的场景）
# PreToolUse 不再发送通知，因为自动批准的工具也会触发 PreToolUse，无法区分
NOTIFY_HOOK_EVENTS = [
    "Stop",              # Claude 完成输出
    "Elicitation",       # MCP 服务器请求用户输入
    "PermissionRequest", # 权限弹窗出现时（用户需手动批准的场景）
]


def _find_python_exe() -> str | None:
    """查找可用的 Python 解释器路径，找不到返回 None。"""
    import shutil
    for name in ("python", "python3"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _hook_bat_path() -> str:
    """返回 notify_hook.bat 的路径（Windows）或 notify.py 的路径（其他平台）"""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve())
    bat = SCRIPT_DIR / "notify_hook.bat"
    if sys.platform == "win32" and bat.exists():
        return str(bat).replace("/", "\\")
    return (SCRIPT_DIR / "notify.py").as_posix()


def _get_hook_base_cmd() -> str:
    """生成 hook 基础命令（不含 --type 等参数）。

    当从 frozen EXE 运行时，优先使用 Python 直接调用 notify.py（~200ms），
    跳过 PyInstaller --onefile 的解压开销（~3-5s）。找不到 Python 则回退到 EXE。
    """
    if sys.platform == "win32":
        if getattr(sys, "frozen", False):
            py = _find_python_exe()
            if py:
                script = SCRIPT_DIR / "notify.py"
                if script.exists():
                    return f'"{py}" "{script}"'
            return f'"{Path(sys.executable).resolve()}"'
        bat = SCRIPT_DIR / "notify_hook.bat"
        if bat.exists():
            return f'"{str(bat).replace(chr(47), chr(92))}"'
        py = _find_python_exe()
        return f'"{py}" "{SCRIPT_DIR / "notify.py"}"' if py else f'"{SCRIPT_DIR / "notify.py"}"'
    return f"{PYTHON_EXE} {(SCRIPT_DIR / 'notify.py').as_posix()}"


def hook_command(notify_type: str = "stop", extra_msg: str = "") -> str:
    """生成 hook 命令"""
    cmd = f'{_get_hook_base_cmd()} --type {notify_type}'
    if extra_msg:
        cmd += f' --message "{extra_msg}"'
    return cmd


def stdin_hook_command(notify_type: str = "stop") -> str:
    """生成从 stdin 读取上下文的 hook 命令（适用于 PreToolUse 等）"""
    return f'{_get_hook_base_cmd()} --type {notify_type} --from-stdin'


def stdin_hook_env() -> dict:
    """返回 hook 命令的环境变量（非 Windows 时使用）"""
    if sys.platform == "win32":
        return {"PYTHONUTF8": "1"}
    return {"PYTHONUTF8": "1"}


def log(msg: str) -> None:
    """记录日志到文件"""
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = sanitize_text(msg)
    try:
        lines = []
        if LOG_FILE.exists():
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
        lines.append(f"[{timestamp}] {msg}\n")
        if len(lines) > 500:
            lines = lines[-500:]
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception:
        pass


def load_config() -> dict:
    """加载配置文件"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for key, val in DEFAULT_CONFIG.items():
                if key not in cfg:
                    cfg[key] = val
                elif isinstance(val, dict):
                    for k, v in val.items():
                        cfg[key].setdefault(k, v)
            return cfg
        except (json.JSONDecodeError, IOError) as e:
            print(f"[WARN] 配置文件读取失败，使用默认配置: {e}")
    save_config(DEFAULT_CONFIG)
    return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> None:
    """保存配置文件"""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def collect_channels(config: dict):
    """收集所有已注册的通知渠道（延迟导入，加速 hook 冷启动）"""
    from channels.windows_toast import WindowsToastChannel
    from channels.weixin import WeixinChannel
    from channels.qq import QQBotChannel
    from channels.telegram import TelegramChannel
    from channels.feishu import FeishuChannel
    from channels.dingtalk import DingTalkChannel
    return [
        WindowsToastChannel(config),
        WeixinChannel(config),
        QQBotChannel(config),
        TelegramChannel(config),
        FeishuChannel(config),
        DingTalkChannel(config),
    ]


def _clean_notify_hooks(hooks: dict, event: str) -> int:
    """清理指定事件中所有 notify 相关的 hook 条目，返回删除数量"""
    entries = hooks.get(event, [])
    new_entries = []
    removed = 0
    for entry in entries:
        cmds = _extract_commands(entry)
        if any(("notify" in c.lower() or "claudebeep" in c.lower()) for c in cmds):
            removed += 1
            continue
        new_entries.append(entry)
    if new_entries:
        hooks[event] = new_entries
    elif event in hooks:
        del hooks[event]
    return removed


def _extract_commands(entry: dict) -> list:
    """从 hook 条目中提取所有命令字符串"""
    if "hooks" in entry and isinstance(entry["hooks"], list):
        return [h.get("command", "") for h in entry["hooks"] if h.get("type") == "command"]
    if "command" in entry:
        return [entry["command"]]
    return []


def install_hooks() -> bool:
    """在 Claude Code 用户级 settings.json 中安装所有通知 hooks"""
    settings_path = CLAUDECODE_SETTINGS

    if settings_path.exists():
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except (json.JSONDecodeError, IOError):
            settings = {}
    else:
        settings = {}
        settings_path.parent.mkdir(parents=True, exist_ok=True)

    hooks = settings.setdefault("hooks", {})

    # 先清理旧的 notify.py hooks（包括旧版可能注册的 PreToolUse/Notification hook）
    all_events_to_clean = NOTIFY_HOOK_EVENTS + ["PreToolUse", "Notification"]
    for event in all_events_to_clean:
        _clean_notify_hooks(hooks, event)

    hook_env = stdin_hook_env()

    # 1. Stop hook: Claude 执行完毕（读取 stdin 获取上下文，用于判断是否需要通知）
    entries = hooks.setdefault("Stop", [])
    entries.append({
        "matcher": "",
        "hooks": [{"type": "command", "command": stdin_hook_command("stop"), "env": hook_env}]
    })

    # 2. Elicitation hook: MCP 服务器请求用户输入
    entries = hooks.setdefault("Elicitation", [])
    entries.append({
        "matcher": "",
        "hooks": [{"type": "command", "command": stdin_hook_command("ask"), "env": hook_env}]
    })

    # 3. PermissionRequest hook: 权限弹窗出现时通知（用户需要手动批准的场景）
    entries = hooks.setdefault("PermissionRequest", [])
    entries.append({
        "matcher": "",
        "hooks": [{"type": "command", "command": stdin_hook_command("ask"), "env": hook_env}]
    })

    settings["hooks"] = hooks

    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

    print(f"Hooks 已安装到: {settings_path}")
    print(f"  Stop              → Claude 完成时通知")
    print(f"  Elicitation       → MCP 请求用户输入时通知")
    print(f"  PermissionRequest → 需要用户批准时通知")
    print(f"  PermissionRequest → 权限弹窗出现时通知")
    return True


def uninstall_hooks() -> bool:
    """从 Claude Code 设置中移除所有通知 hooks"""
    settings_path = CLAUDECODE_SETTINGS

    if not settings_path.exists():
        print("未找到 Claude Code 配置文件，无需卸载。")
        return True

    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except (json.JSONDecodeError, IOError):
        print("配置文件读取失败，无需卸载。")
        return True

    hooks = settings.get("hooks", {})
    total_removed = 0

    # 同时清理旧版可能注册过的 PreToolUse/Notification
    all_events_to_clean = NOTIFY_HOOK_EVENTS + ["PreToolUse", "Notification"]
    for event in all_events_to_clean:
        total_removed += _clean_notify_hooks(hooks, event)

    if total_removed == 0:
        print("未找到已安装的通知 hooks，无需卸载。")
        return True

    settings["hooks"] = hooks

    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

    print(f"已移除 {total_removed} 个通知 hooks。")
    return True


def test_channels(config: dict) -> None:
    """测试所有已启用的渠道"""
    channels = collect_channels(config)
    tested = 0
    for ch in channels:
        if ch.is_enabled():
            print(f"测试渠道: {ch.name} ... ", end="", flush=True)
            ok = ch.send("Claude Code 测试通知", "如果你看到这条消息，说明通知功能配置成功！")
            print("成功" if ok else "失败")
            tested += 1
        else:
            print(f"渠道 {ch.name}: 已禁用，跳过")
    if tested == 0:
        print("没有启用任何通知渠道。")


def _read_stdin_utf8() -> str:
    """Read hook JSON as UTF-8 bytes to avoid Windows codepage mojibake."""
    try:
        data = sys.stdin.buffer.read()
        return data.decode("utf-8", errors="replace")
    except Exception:
        return sys.stdin.read()


def _is_interaction_enabled(config: dict) -> bool:
    """检查交互功能是否启用（避免在 main 中直接 import interaction）"""
    return config.get("interaction", {}).get("enabled", False) is True


def _extract_options(ctx: dict) -> dict:
    """从 hook 上下文中提取选项信息"""
    tool_name = ctx.get("tool_name", "")
    tool_input = ctx.get("tool_input", {})
    hook_event = ctx.get("hook_event_name", ctx.get("hookEvent", ""))
    if not isinstance(tool_input, dict):
        tool_input = {}

    # AskUserQuestion（可能触发 PermissionRequest 或 Elicitation）→ 提取问题选项
    if tool_name == "AskUserQuestion":
        questions = tool_input.get("questions", [])
        if questions:
            q = questions[0]
            options = []
            for o in q.get("options", []):
                label = o.get("label", "")
                desc = o.get("description", "")
                options.append(label if label else desc)
            is_multi = q.get("multiSelect", False)
            return {
                "options": options,
                "option_type": "multi_select" if is_multi else "single_select",
                "multi_select": is_multi,
                "allow_custom": True,
                "question": q.get("question", ""),
                "as_elicitation": True,  # 标记为 Elicitation 格式输出
            }

    # PermissionRequest（真正的权限请求）→ 标准 3 选项
    if hook_event == "PermissionRequest":
        suggestions = ctx.get("permission_suggestions", [])
        log(f"permission_suggestions: {json.dumps(suggestions, ensure_ascii=False)[:300]}")
        return {
            "options": [
                "Yes",
                "Yes, allow all edits during this session",
                "No",
            ],
            "option_type": "permission_select",
            "multi_select": False,
            "allow_custom": False,
            "question": "",
            "as_elicitation": False,
        }

    return {
        "options": [],
        "option_type": "approve_deny",
        "multi_select": False,
        "allow_custom": False,
        "question": "",
        "as_elicitation": False,
    }


def main():
    log(f"notify.py invoked: args={sys.argv[1:]}")
    parser = argparse.ArgumentParser(
        description="Claude Code 通知管理器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--type", choices=["stop", "ask"], default="stop",
        help="通知类型: stop (执行完毕) / ask (询问问题)"
    )
    parser.add_argument("--message", default="", help="自定义通知消息")
    parser.add_argument("--from-stdin", action="store_true", help="从 stdin 读取 hook 上下文")
    parser.add_argument("--install", action="store_true", help="安装 Claude Code hooks")
    parser.add_argument("--uninstall", action="store_true", help="卸载 Claude Code hooks")
    parser.add_argument("--test", action="store_true", help="测试所有通知渠道")
    parser.add_argument("--ui", action="store_true", help="启动 Web 管理界面")

    args = parser.parse_args()

    if args.install:
        install_hooks()
        return
    if args.uninstall:
        uninstall_hooks()
        return

    config = load_config()

    if args.test:
        test_channels(config)
        return

    if args.ui:
        from app import create_app
        app = create_app()
        import webbrowser
        webbrowser.open("http://localhost:5100")
        app.run(host="127.0.0.1", port=5100, debug=False)
        return

    # --- 正常通知流程 ---
    context_text = ""
    hook_type = args.type
    ctx = {}
    hook_event = ""

    if args.from_stdin:
        try:
            if not sys.stdin.isatty():
                raw = _read_stdin_utf8()
                if raw.strip():
                    ctx = sanitize_data(json.loads(raw))
                    log(f"hook ctx keys={list(ctx.keys())} tool={ctx.get('tool_name','?')} event={ctx.get('hook_event_name', ctx.get('hookEvent', '?'))} auto_approved={ctx.get('auto_approved', 'NOT_PRESENT')}")
                    # 调试：记录完整上下文（排除大字段）
                    debug_ctx = {k: v for k, v in ctx.items() if k not in ('transcript_path',)}
                    log(f"hook ctx detail: {json.dumps(debug_ctx, ensure_ascii=False, default=str)[:500]}")

                    # 核心过滤：已自动放行的权限不通知
                    approved, reason = _is_auto_approved(ctx)
                    if approved:
                        log(f"过滤跳过: {reason}")
                        return  # 静默退出，不发通知

                    # 记录未被过滤的命令，方便排查
                    cmd_preview = ""
                    if isinstance(ctx.get("tool_input"), dict):
                        cmd_preview = ctx["tool_input"].get("command", "")[:80]
                    log(f"发送通知: tool={ctx.get('tool_name','?')} cmd={cmd_preview!r}")

                    context_text = _extract_context_text(ctx)
                    # 在通知消息前加上工作目录
                    cwd = ctx.get("cwd", "")
                    if cwd:
                        context_text = f"[{cwd}] {context_text}" if context_text else cwd
                    hook_event = ctx.get("hook_event_name", ctx.get("hookEvent", ""))
                    if hook_event in ("Elicitation", "PermissionRequest", "Notification"):
                        hook_type = "ask"
        except (json.JSONDecodeError, IOError):
            pass

    final_message = sanitize_text(args.message or context_text)

    if hook_type == "ask":
        title = "Claude Code - 询问"
        default_msg = "Claude 正在等待您的回复..."
    else:
        title = "Claude Code - 完成"
        default_msg = "Claude 已执行完毕，请查看结果。"

    message = sanitize_text(final_message if final_message else default_msg)

    channels = collect_channels(config)

    # ── 交互模式分支 ──
    if hook_type == "ask" and _is_interaction_enabled(config):
        import interaction

        options_info = _extract_options(ctx)
        interaction.cleanup_stale()  # 清理已退出进程的残留请求
        pending = interaction.create_request(
            hook_event=hook_event,
            context_text=context_text,
            tool_name=ctx.get("tool_name", ""),
            tool_input=ctx.get("tool_input", {}),
            options=options_info["options"],
            option_type=options_info["option_type"],
            multi_select=options_info["multi_select"],
            allow_custom=options_info["allow_custom"],
            timeout=config.get("interaction", {}).get("timeout_seconds", 0),
            question=options_info.get("question", ""),
            as_elicitation=options_info.get("as_elicitation", False),
        )

        # 发送带选项的通知
        interactive_message = sanitize_text(interaction.format_notification_message(pending))
        for ch in channels:
            if ch.is_enabled():
                log(f"[{ch.name}] 发送交互通知: {interactive_message[:80]}")
                ok = ch.send(title, interactive_message)
                log(f"[{ch.name}] 发送结果: {'成功' if ok else '失败'}")

        # 等待响应（终端 + 文件轮询竞争）
        timeout = config.get("interaction", {}).get("timeout_seconds", 0)
        show_terminal = config.get("interaction", {}).get("show_in_terminal", True)
        response = interaction.wait_for_response(
            pending["id"], timeout, show_terminal, config, pending
        )

        # 清理：只删 pending 文件，保留 response 文件供其他渠道检测"已处理"
        try:
            (interaction.PENDING_DIR / f"{pending['id']}.json").unlink(missing_ok=True)
        except Exception:
            pass

        # 输出响应给 Claude Code
        if response:
            reply_text = interaction.parse_reply(response["reply"], pending)
            # AskUserQuestion 触发的是 PermissionRequest 事件，但需要按 Elicitation 格式输出
            output_event = "Elicitation" if pending.get("as_elicitation") else hook_event
            hook_output = interaction.format_hook_response(reply_text, output_event, pending.get("question", ""), pending.get("tool_input", {}))
            log(f"交互响应: channel={response.get('channel','?')} reply={response['reply']!r} → parsed={reply_text!r} → stdout={hook_output!r}")
            print(hook_output, flush=True)

            # 向其他远程渠道主动推送"已处理"通知
            # 注意：回复渠道的确认反馈已由 listener.py 的 _send_confirmation 处理，此处不再重复发送
            resp_channel = response.get("channel", "")
            _REMOTE_CHANNEL_NAMES = {"weixin", "qq", "telegram", "feishu", "dingtalk"}
            label = pending.get("label", "?")
            # resp_channel 为空时说明是终端回复
            handled_by = resp_channel if resp_channel else "终端"
            done_msg = f"#{label} 已由【{handled_by}】处理，无需再次回复"
            for ch in channels:
                if (ch.is_enabled()
                        and ch.name in _REMOTE_CHANNEL_NAMES
                        and ch.name != resp_channel):
                    ok = ch.send("Claude Code - 已处理", done_msg)
                    log(f"[{ch.name}] 已处理通知: {'成功' if ok else '失败'}")
        else:
            log("等待用户响应超时")
            # 超时：清理 pending 和 response 文件
            interaction.cleanup_request(pending["id"])

    else:
        # ── 现有行为（完全不变）──
        for ch in channels:
            if ch.is_enabled():
                log(f"[{ch.name}] 发送通知: {title} | {message[:80]}")
                ok = ch.send(title, message)
                log(f"[{ch.name}] 发送结果: {'成功' if ok else '失败'}")
            else:
                log(f"[{ch.name}] 已禁用，跳过")


def _load_claude_settings() -> dict:
    """读取 ~/.claude/settings.json，失败返回空 dict"""
    try:
        if CLAUDECODE_SETTINGS.exists():
            with open(CLAUDECODE_SETTINGS, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _find_claude_dir(start: Path) -> Path | None:
    """从 start 向上查找包含 .claude/ 的目录（类似 git 查找 .git/）"""
    current = start.resolve()
    for _ in range(20):
        claude_dir = current / ".claude"
        if claude_dir.is_dir():
            return claude_dir
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _load_project_settings(cwd: str = "") -> dict:
    """读取项目级 .claude/settings.local.json 和 .claude/settings.json
    从 cwd 向上查找 .claude/ 目录（类似 git 查找 .git/）。"""
    if not cwd:
        return {}
    merged = {}
    claude_dir = _find_claude_dir(Path(cwd))
    if not claude_dir:
        return merged
    for name in ("settings.json", "settings.local.json"):
        path = claude_dir / name
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.items():
                    if k == "permissions" and isinstance(v, dict) and k in merged:
                        for pk, pv in v.items():
                            if pk == "allow" and isinstance(pv, list):
                                merged[k].setdefault("allow", []).extend(pv)
                            else:
                                merged[k][pk] = pv
                    else:
                        merged[k] = v
        except Exception:
            pass
    return merged


def _load_permissions_allow(cwd: str = "") -> list:
    """读取 permissions.allow 列表（合并用户级 + 项目级设置）"""
    allow = _load_claude_settings().get("permissions", {}).get("allow", [])
    project_allow = _load_project_settings(cwd).get("permissions", {}).get("allow", [])
    if project_allow:
        allow = list(set(allow + project_allow))
    return allow


def _get_permission_mode() -> str:
    """
    从 ~/.claude/settings.json 读取 permissions.defaultMode（兜底方案）。
    优先应从 hook ctx.get("permission_mode") 读取，此函数仅作为 fallback。
    """
    settings = _load_claude_settings()
    return settings.get("permissions", {}).get("defaultMode", "")


def _is_auto_approved(ctx: dict) -> tuple[bool, str]:
    """
    判断此次工具调用是否跳过通知。
    返回 (是否跳过, 原因说明)

    只有 PermissionRequest 事件会触发通知（用户需要手动批准时）。
    PreToolUse 不再发送通知，因为自动批准的工具也会触发 PreToolUse，无法区分。
    """
    tool_name = ctx.get("tool_name", "")
    hook_event = ctx.get("hook_event_name", ctx.get("hookEvent", ""))

    # ── 层0：权限模式 ────────────────────────────────────────────
    permission_mode = ctx.get("permission_mode", "") or _get_permission_mode()

    if permission_mode == "bypassPermissions":
        # bypassPermissions 下 PermissionRequest/Stop/Elicitation 仍需通知用户
        if hook_event in ("PermissionRequest", "Stop", "Elicitation"):
            return False, ""
        return True, f"bypassPermissions 模式，跳过 {tool_name}"

    if permission_mode == "acceptEdits":
        if tool_name in ("Edit", "Write", "Read", "MultiEdit"):
            return True, f"acceptEdits 模式，跳过 {tool_name}"

    # ── 层1：auto_approved 标记 ───────────────────────────────────
    if ctx.get("auto_approved") is True:
        return True, "auto_approved=true"

    # ── 层2：Stop / Elicitation 直接放行 ─────────────────────────
    if hook_event in ("Stop", "Elicitation") or not tool_name:
        return False, ""

    # ── 层3：PermissionRequest 事件 ──────────────────────────────
    # PermissionRequest 只在需要用户批准时触发，自动批准的工具不触发
    if hook_event == "PermissionRequest":
        return False, ""

    # 其他事件（如 PreToolUse）不再发送通知
    return True, f"跳过 {hook_event} 事件（仅 PermissionRequest 发送通知）"


def _extract_context_text(ctx: dict) -> str:
    """从 hook 上下文中提取有意义的文本描述"""
    # 优先使用 message / text / content 字段
    msg = ctx.get("message", "") or ctx.get("text", "") or ctx.get("content", "")
    if msg:
        return msg

    # PermissionRequest / PreToolUse 场景：从 tool_name + tool_input 构建描述
    tool_name = ctx.get("tool_name", "")
    tool_input = ctx.get("tool_input", {})
    if not isinstance(tool_input, dict):
        tool_input = {}

    if tool_name:
        if tool_name == "Bash":
            cmd = tool_input.get("command", "")
            desc = tool_input.get("description", "")
            if desc:
                return f"执行: {desc}"
            if cmd:
                return f"执行命令: {cmd[:120]}"
            return "执行 Bash 命令"
        elif tool_name == "Edit":
            fp = tool_input.get("file_path", "")
            old = tool_input.get("old_string", "")[:60]
            return f"编辑文件: {fp}" + (f"\n{old}..." if old else "")
        elif tool_name == "Write":
            fp = tool_input.get("file_path", "")
            return f"写入文件: {fp}" if fp else "写入文件"
        elif tool_name == "AskUserQuestion":
            questions = tool_input.get("questions", [])
            if questions:
                texts = [q.get("question", "") for q in questions if q.get("question")]
                return "\n".join(texts[:3]) if texts else "Claude 正在询问您的意见"
            return tool_input.get("question", "") or "Claude 正在询问您的意见"
        elif tool_name == "Agent":
            desc = tool_input.get("description", "")
            return f"启动子代理: {desc}" if desc else "启动子代理"
        elif tool_name.startswith("mcp__"):
            return f"MCP 工具: {tool_name}"
        else:
            return f"工具调用: {tool_name}"

    # Stop 事件：尝试提取 stop_reason
    stop_reason = ctx.get("stop_reason", "")
    if stop_reason:
        return f"完成原因: {stop_reason}"

    return ""


if __name__ == "__main__":
    main()
