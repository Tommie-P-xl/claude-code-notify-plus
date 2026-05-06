"""
跨进程状态共享：通过复合 key 在 PermissionRequest 和 PreToolUse 之间协调通知。

原因：Claude Code 的 PermissionRequest hook context 不包含 tool_use_id 字段，
因此改用 tool_name + command 作为复合 key 去重。

时序：PreToolUse 始终先于 PermissionRequest 触发（Claude Code 行为），
所以 PreToolUse 记录已通知，PermissionRequest 检查已通知则跳过。

使用 JSON 文件存储，每个 key 设置 TTL 自动过期，避免文件无限增长。
"""

import hashlib
import json
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "notify_state.json"
STATE_TTL = 60  # 1 分钟，覆盖 PreToolUse → PermissionRequest 的间隔


def _load() -> dict:
    """读取状态文件，自动过滤过期条目"""
    try:
        if STATE_FILE.exists():
            raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            now = time.time()
            return {k: v for k, v in raw.items() if now - v.get("ts", 0) < STATE_TTL}
    except Exception:
        pass
    return {}


def _save(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _make_key(tool_name: str, command: str) -> str:
    """生成复合去重 key：tool_name + command 的短哈希"""
    raw = f"{tool_name}:{command}"
    return f"{tool_name}:{hashlib.md5(raw.encode()).hexdigest()[:12]}"


def mark_notified(tool_name: str, command: str) -> None:
    """标记某个工具调用已发送通知（由 PreToolUse 调用）"""
    key = _make_key(tool_name, command)
    if not key:
        return
    state = _load()
    state[key] = {"ts": time.time(), "notified": True}
    _save(state)


def was_notified(tool_name: str, command: str) -> bool:
    """检查某个工具调用是否已通知过（由 PermissionRequest 调用）"""
    key = _make_key(tool_name, command)
    if not key:
        return False
    return _load().get(key, {}).get("notified", False)
