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
_LABEL_SEQ_FILE = PENDING_DIR / ".label_seq"

# ── 纯工具函数 ──────────────────────────────────────────

def is_interactive_enabled(config: dict) -> bool:
    """检查交互功能是否启用"""
    return config.get("interaction", {}).get("enabled", False) is True


def _generate_id() -> str:
    """生成唯一请求 ID: req_ + 8位随机hex"""
    return "req_" + "".join(random.choices("0123456789abcdef", k=8))


def _get_next_label() -> str:
    """获取下一个字母标签（A, B, C...），会话内单调递增，不因请求清理而重复"""
    _ensure_dirs()
    # 如果没有 pending 文件，说明是新会话，重置计数器
    existing_pending = list(PENDING_DIR.glob("*.json"))
    if not existing_pending:
        try:
            _LABEL_SEQ_FILE.unlink(missing_ok=True)
        except Exception:
            pass
    try:
        count = int(_LABEL_SEQ_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        count = 0
    # 原子写入，保证计数完整性
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(PENDING_DIR), suffix=".tmp")
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(str(count + 1))
        os.replace(tmp_path, str(_LABEL_SEQ_FILE))
    except Exception:
        pass
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
    - 多问题: 用 | 分隔每个问题的答案，返回 JSON dict
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

    # 检查是否为多问题（含 | 。 . 分隔符）
    tool_input = pending.get("tool_input", {})
    questions = tool_input.get("questions", []) if isinstance(tool_input, dict) else []
    if len(questions) > 1 and any(c in reply for c in ("|", "。", ".")):
        return _parse_multi_question_reply(reply, questions)

    if option_type in ("single_select", ""):
        return _parse_single_select(reply, options, pending.get("allow_custom", False))

    if option_type == "multi_select":
        return _parse_multi_select(reply.replace("，", ","), options)

    return reply


# PermissionRequest 选项 → Claude Code 决策关键词映射
_PERMISSION_DECISION_MAP = {
    "approve": {"approve", "yes", "y", "是", "批准", "ok", "好", "同意", "1"},
    "approve_all": {"approve_all", "allow_all", "allow all", "yes, allow all", "2"},
    "deny": {"deny", "no", "n", "否", "拒绝", "不", "不同意", "3"},
}


def _parse_permission_select(reply: str, options: list) -> str:
    """解析权限选择回复，返回 Claude Code 决策关键词: approve / approve_all / deny"""
    low = reply.strip().lower()

    # 按数字选择: 1=approve, 2=approve_all, 3=deny
    if reply.strip().isdigit():
        idx = int(reply.strip()) - 1
        if idx == 0:
            return "approve"
        elif idx == 1:
            return "approve_all"
        elif idx == 2:
            return "deny"

    # 关键词匹配
    for decision, keywords in _PERMISSION_DECISION_MAP.items():
        if low in keywords:
            return decision

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


def _parse_multi_question_reply(reply: str, questions: list) -> str:
    """
    解析多问题回复（用 | 。或. 分隔每个问题的答案）。
    返回 JSON dict 字符串，key 为 field_name，value 为答案文本。
    例: "1,3|2" 或 "1,3。2" 或 "1,3.2" → '{"q1": "Python,Rust", "q2": "Git"}'
    """
    import re
    parts = [p.strip() for p in re.split(r'[|。.]', reply) if p.strip()]
    result = {}
    for i, q in enumerate(questions):
        field = q.get("field", f"q{i}")
        q_options = []
        for o in q.get("options", []):
            label = o.get("label", "") or o.get("description", "")
            q_options.append(label)
        is_multi = q.get("multiSelect", False)

        if i < len(parts) and parts[i]:
            part = parts[i].replace("，", ",")
            if is_multi:
                parsed = _parse_multi_select(part, q_options)
            else:
                parsed = _parse_single_select(part, q_options, True)
            result[field] = parsed
        else:
            result[field] = ""

    return json.dumps(result, ensure_ascii=False)


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
    question: str = "",
    as_elicitation: bool = False,
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
        "question": question,
        "as_elicitation": as_elicitation,
        "pid": os.getpid(),  # 记录 hook 进程 PID，用于判断请求是否活跃
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
    # 会话结束时重置标签序号，下次从 A 开始
    try:
        _LABEL_SEQ_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _is_process_running(pid: int) -> bool:
    """检查进程是否还在运行"""
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError):
        return False


def cleanup_stale():
    """清理 hook 进程已退出的残留 pending 请求（不影响活跃请求）"""
    _ensure_dirs()
    for f in PENDING_DIR.glob("*.json"):
        try:
            req = json.loads(f.read_text(encoding="utf-8"))
            pid = req.get("pid", 0)
            # 只清理进程已退出的请求
            if pid and not _is_process_running(pid):
                f.unlink()
                resp = RESPONSE_DIR / f"{req.get('id', '')}.json"
                resp.unlink(missing_ok=True)
        except Exception:
            pass


# ── 响应处理 ────────────────────────────────────────────

def write_response(request_id: str, reply: str, channel: str, label: str = "") -> bool:
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
    if label:
        response["label"] = label

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


def format_hook_response(reply_text: str, hook_event: str = "", question: str = "", tool_input: dict = None) -> str:
    """将解析后的回复文本格式化为 hook stdout JSON 输出"""
    import json as _json
    if tool_input is None:
        tool_input = {}

    if hook_event == "PermissionRequest":
        decision = reply_text.strip().lower()
        if decision == "approve":
            return _json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {"behavior": "allow"}
                }
            })
        elif decision == "approve_all":
            return _json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {
                        "behavior": "allow",
                        "updatedPermissions": [{
                            "type": "setMode",
                            "mode": "acceptEdits",
                            "destination": "session"
                        }]
                    }
                }
            })
        elif decision == "deny":
            return _json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {"behavior": "deny", "message": "User denied"}
                }
            })

    if hook_event == "Elicitation":
        # 检查是否为多问题 JSON dict 回复
        answers = {}
        stripped = reply_text.strip()
        if stripped.startswith("{"):
            try:
                answers = _json.loads(stripped)
            except _json.JSONDecodeError:
                pass

        if not answers:
            # 单问题：使用原有逻辑
            field_name = question if question else "response"
            answers = {field_name: stripped}

        updated_input = {"answers": answers}
        # Echo back the original questions array (Claude Code needs it)
        questions = tool_input.get("questions", [])
        if questions:
            updated_input["questions"] = questions
        return _json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {
                    "behavior": "allow",
                    "updatedInput": updated_input
                }
            }
        })

    # 兜底：纯文本
    return reply_text.strip()


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
        # 检查是否有多个问题（AskUserQuestion 可能包含多个问题）
        tool_input = pending.get("tool_input", {})
        questions = tool_input.get("questions", []) if isinstance(tool_input, dict) else []

        if len(questions) > 1:
            title = f"需要选择 #{label}（多题多选）"
            lines.append(f"【Claude Code - {title}】")
            if context:
                lines.append(context)
            lines.append("")
            for qi, q in enumerate(questions):
                q_text = q.get("question", f"问题 {qi+1}")
                lines.append(f"  [{qi+1}] {q_text}")
                for i, o in enumerate(q.get("options", []), 1):
                    opt_label = o.get("label", "") or o.get("description", "")
                    lines.append(f"      {i} - {opt_label}")
                lines.append("")
            lines.append(f"多选用逗号分隔，多题用 | 或。分隔")
            lines.append(f"回复: {label} 1,3|2  （第1题选1,3；第2题选2）")
            lines.append(f"只答一题: {label} 1,3  （默认回答第1题）")
        else:
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
        # 检查是否有多个问题
        tool_input = pending.get("tool_input", {})
        questions = tool_input.get("questions", []) if isinstance(tool_input, dict) else []

        if len(questions) > 1:
            title = f"需要选择 #{label}（多题）"
            lines.append(f"【Claude Code - {title}】")
            if context:
                lines.append(context)
            lines.append("")
            for qi, q in enumerate(questions):
                q_text = q.get("question", f"问题 {qi+1}")
                lines.append(f"  [{qi+1}] {q_text}")
                for i, o in enumerate(q.get("options", []), 1):
                    opt_label = o.get("label", "") or o.get("description", "")
                    lines.append(f"      {i} - {opt_label}")
                if q.get("allowCustom", True):
                    lines.append(f"      0 - 其他")
                lines.append("")
            lines.append(f"多题用 | 或。分隔")
            lines.append(f"回复: {label} 1|2  （第1题选1；第2题选2）")
            lines.append(f"只答一题: {label} 1  （默认回答第1题）")
        else:
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

    lines.append("终端/微信/QQ/飞书/钉钉/Telegram 均可回复，先到先生效。")
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
        elif option_type == "permission_select":
            opts = req.get("options", [])
            opts_preview = ", ".join(f"{j+1}={o}" for j, o in enumerate(opts[:3]))
            lines.append(f"  #{label} {context}  [{opts_preview}]{marker}")
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
    lines.append("  所有渠道均可回复，先到先生效。")
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

        # 查找当前请求的 label，写入 response 供其他渠道检测
        label = ""
        for req in pending_list:
            if req.get("id") == request_id:
                label = req.get("label", "")
                break
        write_response(request_id, user_input, "terminal", label=label)

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
