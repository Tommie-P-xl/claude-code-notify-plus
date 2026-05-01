# Interactive Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bidirectional interaction to claude-code-notify-plus so users can reply to approval notifications via terminal (same Claude Code window), WeChat, or QQ — first responder wins.

**Architecture:** File-based IPC with polling. Hook script creates a pending request file, sends enhanced notification, then blocks polling for a response file. Keepalive daemon processes incoming WeChat/QQ messages and writes response files. Terminal input via CON/dev/tty runs in a parallel thread. All three channels compete; atomic file writes ensure only one response is accepted.

**Tech Stack:** Python 3.10+, threading, tempfile, os (no new dependencies)

**Spec:** `new/2026-05-01-interactive-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `interaction.py` | Create | Core interaction logic: request management, response parsing, terminal I/O, polling |
| `notify.py:370-389` | Modify | Add `_extract_options()`, interactive branch in `main()` |
| `weixin_keepalive.py:146-154` | Modify | Process incoming WeChat messages for pending replies |
| `weixin_keepalive.py:285-300` | Modify | Process incoming QQ messages for pending replies |
| `app.py` | Modify | Add `/api/interaction` config endpoint |
| `static/index.html` | Modify | Add interaction toggle on Dashboard |
| `.gitignore` | Modify | Add `pending/` and `responses/` |

---

### Task 1: Create `interaction.py` — Data Types, Constants, Pure Utilities

**Files:**
- Create: `interaction.py`

- [ ] **Step 1: Create `interaction.py` with constants, data types, and pure utility functions**

```python
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
    # 超过26个用 AA, AB, ...
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
        return reply  # 自由文本

    if option_type in ("single_select", ""):
        return _parse_single_select(reply, options, pending.get("allow_custom", False))

    if option_type == "multi_select":
        return _parse_multi_select(reply, options)

    return reply


def _parse_single_select(reply: str, options: list, allow_custom: bool) -> str:
    """解析单选回复"""
    if reply.isdigit():
        idx = int(reply) - 1
        if 0 <= idx < len(options):
            return options[idx]
        # 数字超出范围，作为自定义文本返回
        return reply
    # 非数字 = 自定义文本
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
                selected.append(part)  # 超出范围的数字当自定义
        else:
            selected.append(part)  # 非数字 = 自定义
    return ",".join(selected) if selected else reply


def _extract_reply_parts(text: str) -> tuple[str, str]:
    """
    从用户消息中提取请求标签和选项。
    - "A 1" → ("A", "1")
    - "1" → ("", "1")  （无标签，默认最新请求）
    - "a1" → ("A", "1")  （紧凑格式：单字母后紧跟内容）
    """
    text = text.strip()
    if not text:
        return ("", "")

    # 尝试 "A 1" 格式（字母 + 空格 + 内容）
    if len(text) >= 3 and text[0].isalpha() and text[1] == " ":
        return (text[0].upper(), text[2:].strip())

    # 尝试 "A1" 格式（单字母紧跟内容）
    if len(text) >= 2 and text[0].isalpha() and not text[0:].isalpha():
        return (text[0].upper(), text[1:].strip())

    # 无标签
    return ("", text)
```

- [ ] **Step 2: Verify the module loads without errors**

Run: `python -c "import interaction; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add interaction.py
git commit -m "feat: add interaction.py with pure utility functions"
```

---

### Task 2: Add Request Management (Create, List, Cleanup Pending)

**Files:**
- Modify: `interaction.py`

- [ ] **Step 1: Add request management functions to `interaction.py`**

Append to `interaction.py`:

```python
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
```

- [ ] **Step 2: Verify module loads**

Run: `python -c "import interaction; print(interaction._generate_id()); print(interaction._get_next_label())"`
Expected: prints a req_xxxx ID and a letter label

- [ ] **Step 3: Commit**

```bash
git add interaction.py
git commit -m "feat: add pending request management to interaction module"
```

---

### Task 3: Add Response Handling (Atomic Write, Format Hook Output)

**Files:**
- Modify: `interaction.py`

- [ ] **Step 1: Add response functions to `interaction.py`**

Append to `interaction.py`:

```python
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
```

- [ ] **Step 2: Test atomic write**

Run: `python -c "import interaction, tempfile, os; interaction._ensure_dirs(); assert interaction.write_response('test1', 'hello', 'terminal') == True; assert interaction.write_response('test1', 'world', 'weixin') == False; r = interaction.read_response('test1'); assert r['reply'] == 'hello'; os.remove(str(interaction.RESPONSE_DIR / 'test1.json')); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add interaction.py
git commit -m "feat: add response handling and message formatting to interaction module"
```

---

### Task 4: Add Terminal I/O (CON / /dev/tty)

**Files:**
- Modify: `interaction.py`

- [ ] **Step 1: Add terminal I/O functions to `interaction.py`**

Append to `interaction.py`:

```python
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

        # 从 CON/dev/tty 读取用户输入
        console_path = _get_console_path()
        with open(console_path, "r", encoding="utf-8") as f:
            user_input = f.readline().strip()

        if not user_input:
            return

        # 检查 response 是否已被其他渠道写入
        if response_file.exists():
            return

        # 原子写入 response
        write_response(request_id, user_input, "terminal")

    except Exception:
        pass
```

- [ ] **Step 2: Verify module loads**

Run: `python -c "import interaction; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add interaction.py
git commit -m "feat: add terminal I/O via CON/dev/tty to interaction module"
```

---

### Task 5: Add `wait_for_response` (Polling + Terminal Thread)

**Files:**
- Modify: `interaction.py`

- [ ] **Step 1: Add the main wait function to `interaction.py`**

Append to `interaction.py`:

```python
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

    # 启动终端读取线程
    if show_terminal:
        t = threading.Thread(
            target=_console_reader_thread,
            args=(request_id, resp_file),
            daemon=True,
        )
        t.start()

    # 主线程轮询
    start = time.time()
    while True:
        if resp_file.exists():
            try:
                response = json.loads(resp_file.read_text(encoding="utf-8"))
                return response
            except Exception:
                pass

        if timeout > 0 and (time.time() - start) >= timeout:
            return None  # 超时

        time.sleep(poll_interval)
```

- [ ] **Step 2: Verify module loads**

Run: `python -c "import interaction; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add interaction.py
git commit -m "feat: add wait_for_response polling to interaction module"
```

---

### Task 6: Modify `notify.py` — Add `_extract_options()` and Interactive Branch

**Files:**
- Modify: `notify.py`

- [ ] **Step 1: Add `_extract_options()` function before `main()` in `notify.py`**

Insert after the `_extract_context_text` function (after line 550, before `if __name__`):

```python
def _extract_options(ctx: dict) -> dict:
    """从 hook 上下文中提取选项信息"""
    tool_name = ctx.get("tool_name", "")
    tool_input = ctx.get("tool_input", {})
    if not isinstance(tool_input, dict):
        tool_input = {}

    if tool_name == "AskUserQuestion":
        questions = tool_input.get("questions", [])
        if questions:
            q = questions[0]  # 取第一个问题
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
            }

    # PermissionRequest 或其他 - 默认审批/拒绝
    return {
        "options": [],
        "option_type": "approve_deny",
        "multi_select": False,
        "allow_custom": False,
        "question": "",
    }
```

- [ ] **Step 2: Add interactive branch in `main()` after notification dispatch**

Replace the notification dispatch block in `main()` (lines 382-389) with:

```python
    channels = collect_channels(config)

    # ── 交互模式分支 ──
    if hook_type == "ask" and _is_interaction_enabled(config):
        import interaction

        options_info = _extract_options(ctx)
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
        )

        # 发送带选项的通知
        interactive_message = interaction.format_notification_message(pending)
        for ch in channels:
            if ch.is_enabled():
                log(f"[{ch.name}] 发送交互通知: {interactive_message[:80]}")
                ok = ch.send(title, interactive_message)
                log(f"[{ch.name}] 发送结果: {'成功' if ok else '失败'}")

        # 等待响应（终端 + 文件轮询竞争）
        timeout = config.get("interaction", {}).get("timeout_seconds", 0)
        show_terminal = config.get("interaction", {}).get("show_in_terminal", True)
        response = interaction.wait_for_response(pending["id"], timeout, show_terminal)

        # 清理
        interaction.cleanup_request(pending["id"])

        # 输出响应给 Claude Code
        if response:
            reply_text = interaction.parse_reply(response["reply"], pending)
            hook_output = interaction.format_hook_response(reply_text)
            print(hook_output)
            log(f"交互响应: channel={response.get('channel','?')} reply={response['reply']!r} → {hook_output!r}")
        else:
            log("等待用户响应超时")

    else:
        # ── 现有行为（完全不变）──
        for ch in channels:
            if ch.is_enabled():
                log(f"[{ch.name}] 发送通知: {title} | {message[:80]}")
                ok = ch.send(title, message)
                log(f"[{ch.name}] 发送结果: {'成功' if ok else '失败'}")
            else:
                log(f"[{ch.name}] 已禁用，跳过")
```

- [ ] **Step 3: Add `_is_interaction_enabled()` helper**

Insert before `_extract_options`:

```python
def _is_interaction_enabled(config: dict) -> bool:
    """检查交互功能是否启用（避免在 main 中直接 import interaction）"""
    return config.get("interaction", {}).get("enabled", False) is True
```

- [ ] **Step 4: Fix variable scoping — `ctx` and `hook_event` must be accessible in interactive branch**

The variables `ctx` and `hook_event` are defined inside the `if args.from_stdin:` block. Move their initialization before the block so they're available in the interactive branch. Change lines 338-368 to:

```python
    context_text = ""
    hook_type = args.type
    ctx = {}
    hook_event = ""

    if args.from_stdin:
        try:
            if not sys.stdin.isatty():
                raw = sys.stdin.read()
                if raw.strip():
                    ctx = json.loads(raw)
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
                    hook_event = ctx.get("hook_event_name", ctx.get("hookEvent", ""))
                    if hook_event in ("Elicitation", "PermissionRequest", "Notification"):
                        hook_type = "ask"
        except (json.JSONDecodeError, IOError):
            pass
```

- [ ] **Step 5: Verify `notify.py` loads without syntax errors**

Run: `python -c "import notify; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add notify.py
git commit -m "feat: add interactive mode branch to notify.py main()"
```

---

### Task 7: Modify `weixin_keepalive.py` — Add Message Processing

**Files:**
- Modify: `weixin_keepalive.py`

- [ ] **Step 1: Add imports and constants at the top of `weixin_keepalive.py`**

After the existing imports (after line 19), add:

```python
# 交互功能：处理用户回复消息
PENDING_DIR = SCRIPT_DIR / "pending"
RESPONSE_DIR = SCRIPT_DIR / "responses"
```

- [ ] **Step 2: Add message processing functions**

Before the `weixin_keepalive_loop` function (before line 103), add:

```python
# ── 交互消息处理 ────────────────────────────────────────

def _extract_reply_parts(text: str) -> tuple:
    """
    从用户消息中提取请求标签和选项。
    - "A 1" → ("A", "1")
    - "1" → ("", "1")  （无标签，默认最新请求）
    - "a1" → ("A", "1")  （紧凑格式）
    """
    text = text.strip()
    if not text:
        return ("", "")

    # "A 1" 格式
    if len(text) >= 3 and text[0].isalpha() and text[1] == " ":
        return (text[0].upper(), text[2:].strip())

    # "A1" 格式（单字母紧跟内容）
    if len(text) >= 2 and text[0].isalpha() and not text[1:].isalpha():
        return (text[0].upper(), text[1:].strip())

    return ("", text)


def _process_incoming_message(text: str, channel: str):
    """
    处理收到的消息，匹配 pending 请求并写入 response。
    """
    if not PENDING_DIR.exists():
        return
    pending_files = list(PENDING_DIR.glob("*.json"))
    if not pending_files:
        return  # 无 pending 请求，忽略消息

    # 解析回复
    label, reply = _extract_reply_parts(text)
    if not reply:
        return

    # 找到目标 pending 请求
    target_pending = None
    if label:
        # 按标签匹配
        for pf in pending_files:
            try:
                req = json.loads(pf.read_text(encoding="utf-8"))
                if req.get("label", "").upper() == label:
                    target_pending = req
                    break
            except Exception:
                continue
    if not target_pending:
        # 无标签或未匹配到，取最新请求
        pending_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        try:
            target_pending = json.loads(pending_files[0].read_text(encoding="utf-8"))
        except Exception:
            return

    if not target_pending:
        return

    request_id = target_pending["id"]

    # 原子写入 response
    resp_file = RESPONSE_DIR / f"{request_id}.json"
    if resp_file.exists():
        return  # 已被其他渠道抢先

    import tempfile
    response = {
        "request_id": request_id,
        "reply": reply,
        "channel": channel,
        "received_at": __import__("time").time(),
    }

    try:
        RESPONSE_DIR.mkdir(exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(RESPONSE_DIR), suffix=".tmp")
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(response, f, ensure_ascii=False)
        os.rename(tmp_path, str(resp_file))
        log(f"[{channel}] 交互回复: {text[:50]} → {request_id}")
    except FileExistsError:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    except Exception as e:
        log(f"[{channel}] 写入 response 失败: {e}")
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
```

- [ ] **Step 3: Add message processing call in `weixin_keepalive_loop()`**

In the `weixin_keepalive_loop()` function, inside the `if msgs:` block (after the context_token extraction around line 154), add:

```python
                    # [新增] 处理用户回复消息
                    if PENDING_DIR.exists() and any(PENDING_DIR.glob("*.json")):
                        for item in msg.get("item_list", []):
                            if item.get("type") == 1:
                                msg_text = item.get("text_item", {}).get("text", "")
                                if msg_text.strip():
                                    _process_incoming_message(msg_text.strip(), "weixin")
                                break
```

- [ ] **Step 4: Add message processing call in `qq_websocket_loop()`**

In the `qq_websocket_loop()` function, inside the `C2C_MESSAGE_CREATE` event handler (after the existing user_openid extraction around line 300), add:

```python
                                # [新增] 处理用户回复消息
                                content = event_data.get("content", "").strip()
                                if content and PENDING_DIR.exists() and any(PENDING_DIR.glob("*.json")):
                                    _process_incoming_message(content, "qq")
```

- [ ] **Step 5: Verify `weixin_keepalive.py` loads without syntax errors**

Run: `python -c "import weixin_keepalive; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add weixin_keepalive.py
git commit -m "feat: add message processing for interactive replies to keepalive daemon"
```

---

### Task 8: Modify `app.py` — Add Interaction Config API

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Read `app.py` to understand the API pattern**

- [ ] **Step 2: Add interaction config API endpoint**

Add a new route in `app.py` following the existing config API pattern. Add after the existing config-related routes:

```python
@app.route("/api/interaction", methods=["GET", "POST"])
def api_interaction():
    """交互功能配置 API"""
    if request.method == "GET":
        cfg = load_config()
        interaction = cfg.get("interaction", {
            "enabled": False,
            "timeout_seconds": 0,
            "show_in_terminal": True,
        })
        return jsonify(interaction)

    # POST: 更新交互配置
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
```

- [ ] **Step 3: Verify `app.py` loads without errors**

Run: `python -c "from app import create_app; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat: add interaction config API endpoint"
```

---

### Task 9: Modify `static/index.html` — Add Interaction Toggle on Dashboard

**Files:**
- Modify: `static/index.html`

- [ ] **Step 1: Read `static/index.html` to understand the UI pattern**

- [ ] **Step 2: Add interaction toggle card on the Dashboard tab**

Find the Dashboard tab content area and add a new card following the existing UI pattern. Add after the existing dashboard cards:

```html
<!-- 交互模式卡片 -->
<div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6">
  <div class="flex items-center justify-between mb-4">
    <div>
      <h3 class="text-lg font-semibold text-gray-900 dark:text-white">交互模式</h3>
      <p class="text-sm text-gray-500 dark:text-gray-400">
        开启后，审批通知可直接通过终端/微信/QQ 回复
      </p>
    </div>
    <button
      @click="toggleInteraction()"
      :class="interactionEnabled ? 'bg-blue-600' : 'bg-gray-300 dark:bg-gray-600'"
      class="relative inline-flex h-6 w-11 items-center rounded-full transition-colors"
    >
      <span
        :class="interactionEnabled ? 'translate-x-6' : 'translate-x-1'"
        class="inline-block h-4 w-4 transform rounded-full bg-white transition-transform"
      ></span>
    </button>
  </div>

  <div x-show="interactionEnabled" x-transition class="space-y-3 mt-4 pt-4 border-t border-gray-200 dark:border-gray-700">
    <div class="flex items-center justify-between">
      <label class="text-sm text-gray-600 dark:text-gray-400">超时（秒，0=无限）</label>
      <input
        type="number"
        x-model.number="interactionTimeout"
        @change="saveInteraction()"
        min="0"
        class="w-24 px-2 py-1 text-sm border rounded dark:bg-gray-700 dark:border-gray-600 dark:text-white"
      >
    </div>
    <div class="flex items-center justify-between">
      <label class="text-sm text-gray-600 dark:text-gray-400">终端显示选项</label>
      <button
        @click="interactionShowTerminal = !interactionShowTerminal(); saveInteraction()"
        :class="interactionShowTerminal ? 'bg-blue-600' : 'bg-gray-300 dark:bg-gray-600'"
        class="relative inline-flex h-5 w-9 items-center rounded-full transition-colors"
      >
        <span
          :class="interactionShowTerminal ? 'translate-x-5' : 'translate-x-0.5'"
          class="inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform"
        ></span>
      </button>
    </div>
  </div>
</div>
```

- [ ] **Step 3: Add Alpine.js data and methods for interaction**

In the Alpine.js `data()` section, add:

```javascript
// 交互模式状态
interactionEnabled: false,
interactionTimeout: 0,
interactionShowTerminal: true,
```

Add methods:

```javascript
async loadInteraction() {
  try {
    const resp = await fetch('/api/interaction');
    const data = await resp.json();
    this.interactionEnabled = data.enabled || false;
    this.interactionTimeout = data.timeout_seconds || 0;
    this.interactionShowTerminal = data.show_in_terminal !== false;
  } catch (e) { console.error('load interaction failed', e); }
},

async toggleInteraction() {
  this.interactionEnabled = !this.interactionEnabled;
  await this.saveInteraction();
},

async saveInteraction() {
  try {
    await fetch('/api/interaction', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        enabled: this.interactionEnabled,
        timeout_seconds: this.interactionTimeout,
        show_in_terminal: this.interactionShowTerminal,
      }),
    });
  } catch (e) { console.error('save interaction failed', e); }
},
```

Call `this.loadInteraction()` in the existing `init()` method.

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "feat: add interaction mode toggle to Dashboard UI"
```

---

### Task 10: Update `.gitignore`

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add `pending/` and `responses/` to `.gitignore`**

Append to `.gitignore`:

```
# 交互功能运行时文件
pending/
responses/
```

- [ ] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: add pending/ and responses/ to gitignore"
```

---

### Task 11: End-to-End Verification

- [ ] **Step 1: Verify `interaction.py` loads completely**

Run: `python -c "import interaction; print('Functions:', [f for f in dir(interaction) if not f.startswith('_')])"`
Expected: lists all public functions

- [ ] **Step 2: Verify `notify.py` loads completely**

Run: `python -c "import notify; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Verify `weixin_keepalive.py` loads completely**

Run: `python -c "import weixin_keepalive; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Verify `app.py` loads completely**

Run: `python -c "from app import create_app; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Test interaction disabled (default behavior unchanged)**

Run: `python -c "import notify; cfg = notify.load_config(); print('interaction enabled:', cfg.get('interaction', {}).get('enabled', False))"`
Expected: `interaction enabled: False`

- [ ] **Step 6: Manual integration test**

1. Set `"interaction": {"enabled": true}` in `config.json`
2. Run `python notify.py --type ask --message "test approval"`
3. Verify terminal shows options
4. Type `1` and press Enter
5. Verify the hook outputs `approve`

- [ ] **Step 7: Final commit**

```bash
git add -A
git commit -m "feat: interactive mode complete — terminal/WeChat/QQ reply support"
```
