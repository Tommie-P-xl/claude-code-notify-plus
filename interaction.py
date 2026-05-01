"""
交互功能核心模块。
管理 pending 请求、响应解析、终端 I/O、文件轮询。
"""

import json
import os
import sys
import time
import random
import string
import tempfile
import threading
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
PENDING_DIR = SCRIPT_DIR / "pending"
RESPONSE_DIR = SCRIPT_DIR / "responses"

# ── 纯工具函数 ──────────────────────────────────────────

def is_interactive_enabled(config: dict) -> bool:
    """检查交互功能是否启用"""
    return config.get("interaction", {}).get("enabled", False) is True


def _generate_id() -> str:
    """生成唯一请求 ID: req_ + 8位随机hex"""
    return "req_" + "".join(random.choices("0123456789abcdef", k=8))


def _get_next_label() -> str:
    """获取下一个字母标签（A, B, C...），基于当前 pending 文件数量"""
    _ensure_dirs()
    existing = list(PENDING_DIR.glob("*.json"))
    count = len(existing)
    if count < 26:
        return chr(ord("A") + count)
    first = chr(ord("A") + (count // 26 - 1) % 26)
    second = chr(ord("A") + count % 26)
    return first + second


def _ensure_dirs():
    """确保 pending 和 responses 目录存在"""
    PENDING_DIR.mkdir(exist_ok=True)
    RESPONSE_DIR.mkdir(exist_ok=True)


# ── 回复解析 ────────────────────────────────────────────

_APPROVE_KEYWORDS = {"1", "y", "yes", "是", "批准", "approve", "ok", "好", "同意"}
_DENY_KEYWORDS = {"2", "n", "no", "否", "拒绝", "deny", "不", "不同意"}


def parse_reply(reply: str, pending: dict) -> str:
    """
    解析用户回复，返回要输出给 Claude Code 的文本。
    - PermissionRequest: "approve" / "deny" / 自由文本
    - permission_select: 按选项列表映射
    - Elicitation 单选: 选项文本 / 自定义文本
    - Elicitation 多选: 逗号分隔的选项文本
    """
    reply = reply.strip()
    option_type = pending.get("option_type", "approve_deny")
    options = pending.get("options", [])

    if option_type == "approve_deny":
        low = reply.lower()
        if low in _APPROVE_KEYWORDS:
            return "approve"
        if low in _DENY_KEYWORDS:
            return "deny"
        return reply

    if option_type == "permission_select":
        return _parse_permission_select(reply, options)

    if option_type in ("single_select", ""):
        return _parse_single_select(reply, options, pending.get("allow_custom", False))

    if option_type == "multi_select":
        return _parse_multi_select(reply, options)

    return reply


def _parse_permission_select(reply: str, options: list) -> str:
    """解析权限选择回复，按选项列表映射"""
    if reply.isdigit():
        idx = int(reply) - 1
        if 0 <= idx < len(options):
            return options[idx]
    # 非数字或超出范围，尝试关键词匹配
    low = reply.lower()
    if low in _APPROVE_KEYWORDS:
        return options[0] if options else "approve"
    if low in _DENY_KEYWORDS:
        return options[-1] if options else "deny"
    return reply


def _parse_single_select(reply: str, options: list, allow_custom: bool) -> str:
    """解析单选回复"""
    if reply.isdigit():
        idx = int(reply) - 1
        if 0 <= idx < len(options):
            return options[idx]
        return reply
    return reply


def _parse_multi_select(reply: str, options: list) -> str:
    """解析多选回复，返回逗号分隔的选项文本"""
    parts = [p.strip() for p in reply.split(",") if p.strip()]
    selected = []
    for part in parts:
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(options):
                selected.append(options[idx])
            else:
                selected.append(part)
        else:
            selected.append(part)
    return ",".join(selected) if selected else reply


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


# ── 请求管理 ────────────────────────────────────────────

def create_request(
    hook_event: str,
    context_text: str,
    tool_name: str,
    tool_input: dict,
    options: list,
    option_type: str,
    multi_select: bool,
    allow_custom: bool,
    timeout: int,
) -> dict:
    """创建 pending 请求，返回 pending dict"""
    _ensure_dirs()
    request_id = _generate_id()
    label = _get_next_label()

    pending = {
        "id": request_id,
        "label": label,
        "hook_type": "ask",
        "hook_event": hook_event,
        "context_text": context_text,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "options": options,
        "option_type": option_type,
        "multi_select": multi_select,
        "allow_custom": allow_custom,
        "created_at": time.time(),
        "timeout": timeout,
    }

    pending_file = PENDING_DIR / f"{request_id}.json"
    pending_file.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    return pending


def list_requests() -> list[dict]:
    """列出所有 pending 请求，按创建时间排序（最新在前）"""
    _ensure_dirs()
    requests = []
    for f in PENDING_DIR.glob("*.json"):
        try:
            req = json.loads(f.read_text(encoding="utf-8"))
            requests.append(req)
        except Exception:
            continue
    requests.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    return requests


def get_latest_request() -> Optional[dict]:
    """获取最新的 pending 请求"""
    reqs = list_requests()
    return reqs[0] if reqs else None


def get_request_by_label(label: str) -> Optional[dict]:
    """根据标签获取 pending 请求"""
    for req in list_requests():
        if req.get("label", "").upper() == label.upper():
            return req
    return None


def cleanup_request(request_id: str):
    """清理指定请求的 pending 和 response 文件"""
    try:
        (PENDING_DIR / f"{request_id}.json").unlink(missing_ok=True)
    except Exception:
        pass
    try:
        (RESPONSE_DIR / f"{request_id}.json").unlink(missing_ok=True)
    except Exception:
        pass


def cleanup_all():
    """清理所有 pending 和 response 文件"""
    _ensure_dirs()
    for f in PENDING_DIR.glob("*.json"):
        try:
            f.unlink()
        except Exception:
            pass
    for f in RESPONSE_DIR.glob("*.json"):
        try:
            f.unlink()
        except Exception:
            pass


# ── 响应处理 ────────────────────────────────────────────

def write_response(request_id: str, reply: str, channel: str) -> bool:
    """
    原子写入 response 文件。
    如果文件已存在（被其他渠道抢先），返回 False。
    """
    _ensure_dirs()
    resp_file = RESPONSE_DIR / f"{request_id}.json"
    if resp_file.exists():
        return False

    response = {
        "request_id": request_id,
        "reply": reply,
        "channel": channel,
        "received_at": time.time(),
    }

    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(RESPONSE_DIR), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(response, f, ensure_ascii=False)
        os.rename(tmp_path, str(resp_file))
        return True
    except FileExistsError:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return False
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return False


def read_response(request_id: str) -> Optional[dict]:
    """读取 response 文件，不存在返回 None"""
    resp_file = RESPONSE_DIR / f"{request_id}.json"
    if not resp_file.exists():
        return None
    try:
        return json.loads(resp_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def format_hook_response(reply_text: str) -> str:
    """将解析后的回复文本格式化为 hook stdout 输出"""
    return reply_text


def format_notification_message(pending: dict) -> str:
    """格式化带选项编号的通知消息（发到微信/QQ）"""
    label = pending.get("label", "A")
    context = pending.get("context_text", "")
    options = pending.get("options", [])
    option_type = pending.get("option_type", "approve_deny")
    multi_select = pending.get("multi_select", False)

    lines = []

    if option_type == "approve_deny":
        title = f"审批请求 #{label}"
        lines.append(f"【Claude Code - {title}】")
        if context:
            lines.append(context)
        lines.append("")
        lines.append("  1 - 批准")
        lines.append("  2 - 拒绝")
        lines.append("")
        lines.append(f"回复: {label} 1（字母为请求编号，数字为选项）")

    elif option_type == "permission_select":
        title = f"审批请求 #{label}"
        lines.append(f"【Claude Code - {title}】")
        if context:
            lines.append(context)
        lines.append("")
        for i, opt in enumerate(options, 1):
            lines.append(f"  {i} - {opt}")
        lines.append("")
        lines.append(f"回复: {label} 1（字母为请求编号，数字为选项）")

    elif option_type == "multi_select":
        title = f"需要选择 #{label}（多选）"
        lines.append(f"【Claude Code - {title}】")
        if context:
            lines.append(context)
        lines.append("")
        for i, opt in enumerate(options, 1):
            lines.append(f"  {i} - {opt}")
        lines.append("  0 - 其他")
        lines.append("")
        lines.append(f"多选用逗号分隔，如: {label} 1,3")
        lines.append(f"回复: {label} <选项>")

    else:  # single_select
        title = f"需要选择 #{label}"
        lines.append(f"【Claude Code - {title}】")
        if context:
            lines.append(context)
        lines.append("")
        for i, opt in enumerate(options, 1):
            lines.append(f"  {i} - {opt}")
        if pending.get("allow_custom", False):
            lines.append("  0 - 其他（直接输入自定义内容）")
        lines.append("")
        lines.append(f"回复: {label} 1")

    lines.append("终端/微信/QQ 均可回复，先到先生效。")
    return "\n".join(lines)


# ── 终端 I/O ────────────────────────────────────────────

def _get_console_path() -> str:
    """返回控制台设备路径"""
    if sys.platform == "win32":
        return "CON"
    return "/dev/tty"


def write_to_console(text: str):
    """
    向终端写入文本（绕过 stdout）。
    stdout 用于返回 hook 响应给 Claude Code，不能混用。
    """
    try:
        console_path = _get_console_path()
        with open(console_path, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
    except Exception:
        pass


def format_terminal_prompt(pending_list: list[dict]) -> str:
    """格式化终端显示的选项提示（支持多请求列表）"""
    if not pending_list:
        return ""

    count = len(pending_list)
    lines = []
    lines.append("")
    lines.append("=" * 50)
    lines.append(f"  Claude Code 等待回复 ({count} 个请求)")
    lines.append("=" * 50)

    for i, req in enumerate(pending_list):
        marker = " ← 最新" if i == 0 else ""
        label = req.get("label", "?")
        context = req.get("context_text", "")[:50]
        option_type = req.get("option_type", "approve_deny")

        if option_type == "approve_deny":
            lines.append(f"  #{label} {context}  [批准/拒绝]{marker}")
        else:
            opts = req.get("options", [])
            opts_preview = ", ".join(f"{j+1}={o}" for j, o in enumerate(opts[:3]))
            if len(opts) > 3:
                opts_preview += ", ..."
            lines.append(f"  #{label} {context}  [{opts_preview}]{marker}")

    lines.append("")
    latest_label = pending_list[0].get("label", "A")
    if count == 1:
        lines.append(f'  直接输入选项，如 "1"')
    else:
        lines.append(f'  回复格式: <字母> <选项>，如 "{latest_label} 1"')
        lines.append(f'  直接输入 "1" 回复最新请求 #{latest_label}')
    lines.append("  三个渠道均可回复，先到先生效。")
    lines.append("=" * 50)
    lines.append("请输入: ")

    return "\n".join(lines)


def _console_reader_thread(request_id: str, response_file: Path):
    """
    终端读取线程：显示提示，读取用户输入，写入 response 文件。
    如果 response 文件已存在（被其他渠道抢先），静默退出。
    """
    try:
        pending_list = list_requests()
        if not pending_list:
            return

        prompt = format_terminal_prompt(pending_list)
        write_to_console(prompt)

        console_path = _get_console_path()
        with open(console_path, "r", encoding="utf-8") as f:
            user_input = f.readline().strip()

        if not user_input:
            return

        if response_file.exists():
            return

        write_response(request_id, user_input, "terminal")

    except Exception:
        pass


# ── 主等待逻辑 ──────────────────────────────────────────

def wait_for_response(request_id: str, timeout: int, show_terminal: bool) -> Optional[dict]:
    """
    等待用户响应。
    - 主线程轮询 responses/{id}.json
    - 如果 show_terminal=True，同时启动终端读取线程
    - 任一来源先写入 response 文件即返回
    - timeout=0 表示无限等待
    - 超时返回 None
    """
    _ensure_dirs()
    resp_file = RESPONSE_DIR / f"{request_id}.json"
    poll_interval = 2  # 秒

    if show_terminal:
        t = threading.Thread(
            target=_console_reader_thread,
            args=(request_id, resp_file),
            daemon=True,
        )
        t.start()

    start = time.time()
    while True:
        if resp_file.exists():
            try:
                response = json.loads(resp_file.read_text(encoding="utf-8"))
                return response
            except Exception:
                pass

        if timeout > 0 and (time.time() - start) >= timeout:
            return None

        time.sleep(poll_interval)
