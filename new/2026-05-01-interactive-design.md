# claude-code-notify-plus 交互功能设计文档

> 项目名称: claude-code-notify-plus（原 claude-code-notify）
> 日期: 2026-05-01
> 状态: 设计完成，待实现

## 1. 背景与目标

当前项目是一个**单向通知系统**：Claude Code 触发 hook → 发送通知到微信/QQ/Windows Toast → 用户收到通知但无法直接回复。

**目标**：在现有基础上增加交互功能，当 Claude Code 触发审批通知时，用户可以通过以下任意渠道回复：
- **终端直接输入**（同一 Claude Code 终端窗口）
- **微信回复**
- **QQ 回复**

三个渠道竞争，**先到先得**，任一渠道回复即生效。

**硬约束**：交互功能为纯 opt-in 开关。关闭时（默认），所有行为与现有版本完全一致。

## 2. 整体架构

```
┌─────────────────────────────────────────────────────┐
│                    Claude Code                       │
│         hook 事件: PermissionRequest / Elicitation    │
└──────────────────────┬──────────────────────────────┘
                       │ subprocess (stdin = hook context JSON)
                       ▼
┌─────────────────────────────────────────────────────┐
│           notify.py --type ask --from-stdin           │
│                                                      │
│  交互模式 (interaction.enabled=true):                 │
│    1. 解析 hook 上下文，提取选项                       │
│    2. 创建 pending/{id}.json                          │
│    3. 格式化带选项编号的通知消息                        │
│    4. 发送到微信 + QQ                                 │
│    5. 终端显示选项提示（新线程读 CON/dev/tty）          │
│    6. 主线程轮询 responses/{id}.json                  │
│    7. 收到响应 → 清理 → stdout 输出给 Claude Code     │
│                                                      │
│  非交互模式 (默认):                                    │
│    → 行为与现有完全一致，发通知即退出                    │
└──────┬───────────────┬───────────────┬───────────────┘
       │               │               │
       ▼               ▼               ▼
   终端 CON        微信/QQ 消息     (备用)
   键盘输入      keepalive.py 处理
       │               │ 匹配 pending
       ▼               ▼
   ┌───────────────────────────────┐
   │     responses/{id}.json       │  ← 谁先写入谁赢
   └───────────────────────────────┘
```

### 核心变化点

| 组件 | 当前行为 | 交互模式行为 |
|------|---------|-------------|
| `notify.py` (hook 回调) | 发通知后立即退出 | 发通知 → 阻塞等待响应 → 输出响应 |
| `weixin_keepalive.py` | 仅保活 + 获取 token/openid | 增强：处理用户回复消息，写入 response 文件 |
| 终端 | 无 | 通过 CON/dev/tty 显示选项、读取键盘输入 |

## 3. 文件结构变更

```
D:\edge_load\claude-code-notify-plus\
├── interaction.py          # [新增] 交互核心逻辑
├── pending/                # [新增] 待响应请求目录
│   └── {id}.json           #     每个 pending 请求一个文件
├── responses/              # [新增] 用户响应目录
│   └── {id}.json           #     每个响应一个文件
├── notify.py               # [修改] main() 中增加交互分支
├── weixin_keepalive.py     # [修改] 增加消息处理逻辑
├── app.py                  # [修改] 新增交互配置 API
├── static/index.html       # [修改] Dashboard 新增交互开关
└── config.json             # [修改] 新增 interaction 节点
```

## 4. 数据格式

### 4.1 Pending 请求 (`pending/{id}.json`)

```json
{
  "id": "req_a1b2c3",
  "label": "A",
  "hook_type": "ask",
  "hook_event": "PermissionRequest",
  "context_text": "执行命令: npm install axios",
  "tool_name": "Bash",
  "tool_input": {},
  "options": [],
  "option_type": "approve_deny",
  "multi_select": false,
  "allow_custom": false,
  "created_at": 1746105600.0,
  "timeout": 0
}
```

字段说明：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 唯一请求 ID（`req_` + 随机 hex） |
| `label` | string | 用户可见的字母标签（A, B, C...） |
| `hook_event` | string | `PermissionRequest` 或 `Elicitation` |
| `context_text` | string | 人类可读的请求描述 |
| `options` | string[] | 可选项列表（AskUserQuestion 的 options） |
| `option_type` | string | `approve_deny` / `single_select` / `multi_select` |
| `multi_select` | bool | 是否多选 |
| `allow_custom` | bool | 是否允许自定义文本输入 |
| `timeout` | int | 超时秒数，0 = 无限等待 |

### 4.2 Response 响应 (`responses/{id}.json`)

```json
{
  "request_id": "req_a1b2c3",
  "reply": "1",
  "channel": "terminal",
  "received_at": 1746105660.0
}
```

`channel` 取值：`terminal` / `weixin` / `qq`

### 4.3 Config 配置 (`config.json` 新增)

```json
{
  "interaction": {
    "enabled": false,
    "timeout_seconds": 0,
    "show_in_terminal": true
  }
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 是否启用交互功能。关闭时所有行为不变 |
| `timeout_seconds` | `0` | 等待响应超时。`0` = 无限等待 |
| `show_in_terminal` | `true` | 是否在终端显示选项提示 |

## 5. 各 Hook 事件处理

### 5.1 PermissionRequest（审批请求）

**通知消息格式：**
```
【Claude Code - 审批请求 #A】
执行命令: npm install axios

  1 - 批准
  2 - 拒绝

回复: A 1（字母为请求编号，数字为选项）
终端/微信/QQ 均可回复，先到先生效。
```

**响应解析：**
- `1` / `y` / `yes` / `是` / `批准` → stdout: `approve`
- `2` / `n` / `no` / `否` / `拒绝` → stdout: `deny`
- 其他文本 → stdout: 原样输出（作为自由文本决策）

### 5.2 Elicitation（含 AskUserQuestion）

**通知消息格式（单选）：**
```
【Claude Code - 需要选择 #B】
选择要使用的数据库

  1 - PostgreSQL
  2 - MySQL
  3 - SQLite
  0 - 其他（直接输入自定义内容）

回复: B 1
终端/微信/QQ 均可回复，先到先生效。
```

**通知消息格式（多选）：**
```
【Claude Code - 需要选择 #C（多选）】
选择要启用的功能

  1 - 认证
  2 - 日志
  3 - 缓存
  0 - 其他

多选用逗号分隔，如: C 1,3
回复: C <选项>
终端/微信/QQ 均可回复，先到先生效。
```

**响应解析（单选）：**
- 输入数字 `N` → stdout: 第 N 个选项的文本
- 输入 `0` 或任意非数字文本 → stdout: 用户输入的文本（自定义）

**响应解析（多选）：**
- 输入 `1,3` → stdout: `选项1文本,选项3文本`
- 输入 `1,自定义内容` → stdout: `选项1文本,自定义内容`

### 5.3 Stop（任务完成）

**不进入交互模式**，行为与现有完全一致：发通知后退出。

## 6. 核心模块设计

### 6.1 `interaction.py`（新增）

职责：交互功能的核心逻辑，被 `notify.py` 调用。

```python
# 主要函数

def is_interactive_enabled(config: dict) -> bool:
    """检查交互功能是否启用"""

def create_pending_request(
    hook_event: str, context_text: str, tool_name: str,
    tool_input: dict, options: list, option_type: str,
    multi_select: bool, allow_custom: bool, timeout: int
) -> str:
    """创建 pending 请求，返回 request_id"""

def get_next_label() -> str:
    """获取下一个字母标签（A, B, C...），基于当前 pending 数量"""

def format_notification_message(pending: dict) -> str:
    """格式化带选项编号的通知消息"""

def format_terminal_prompt(pending_list: list[dict]) -> str:
    """格式化终端显示的选项提示（支持多请求列表）"""

def wait_for_response(request_id: str, timeout: int, show_terminal: bool) -> dict | None:
    """
    等待用户响应。
    - 轮询 responses/{id}.json
    - 如果 show_terminal=True，同时启动终端读取线程
    - 任一来源先写入 response 文件即返回
    - 超时返回 None
    """

def parse_reply(reply: str, pending: dict) -> str:
    """
    解析用户回复，返回要输出给 Claude Code 的文本。
    - PermissionRequest: "approve" / "deny" / 自由文本
    - Elicitation 单选: 选项文本 / 自定义文本
    - Elicitation 多选: 逗号分隔的选项文本
    """

def format_hook_response(parsed: dict, pending: dict) -> str:
    """将解析后的回复格式化为 hook stdout 输出"""

def cleanup_request(request_id: str):
    """清理 pending 和 response 文件"""

def cleanup_expired():
    """清理所有过期的 pending 请求"""
```

### 6.2 终端读取（CON / /dev/tty）

```python
def _read_from_console(prompt: str, request_id: str):
    """
    在终端显示提示并读取用户输入。
    - Windows: 打开 CON 设备读写
    - Unix: 打开 /dev/tty 读写
    - 读取到输入后写入 responses/{id}.json
    - 如果 response 文件已存在（被其他渠道抢先），静默退出
    """

def _write_to_console(text: str):
    """
    向终端写入文本。
    - Windows: 写入 CON
    - Unix: 写入 /dev/tty
    - 绕过 stdout（stdout 用于返回 hook 响应给 Claude Code）
    """
```

关键实现细节：
- 使用 `threading.Thread` 启动终端读取线程，主线程继续轮询 response 文件
- 写入 response 文件前检查文件是否已存在（原子竞争保护）
- 使用 `tempfile.mkstemp` + `os.rename` 实现原子写入

### 6.3 `notify.py` 修改

`main()` 函数中增加交互分支（伪代码）：

```python
def main():
    # ... 现有的配置加载、hook 上下文解析、过滤逻辑不变 ...

    if hook_type == "ask" and interaction.is_interactive_enabled(config):
        # ── 交互模式 ──
        options_info = _extract_options(ctx)
        request_id = interaction.create_pending_request(...)
        message = interaction.format_notification_message(pending)

        # 发送通知到微信/QQ
        for ch in channels:
            if ch.is_enabled():
                ch.send(title, message)

        # 等待响应（终端 + 文件轮询竞争）
        timeout = config.get("interaction", {}).get("timeout_seconds", 0)
        show_terminal = config.get("interaction", {}).get("show_in_terminal", True)
        response = interaction.wait_for_response(request_id, timeout, show_terminal)

        # 清理
        interaction.cleanup_request(request_id)

        # 输出响应给 Claude Code
        if response:
            parsed = interaction.parse_reply(response["reply"], pending)
            print(interaction.format_hook_response(parsed, pending))
        else:
            # 超时
            log("等待用户响应超时")

    else:
        # ── 现有行为（完全不变）──
        for ch in channels:
            if ch.is_enabled():
                ch.send(title, message)
```

### 6.4 `weixin_keepalive.py` 修改

增强消息处理：当收到用户消息且存在 pending 请求时，解析回复并写入 response 文件。

```python
# 新增
PENDING_DIR = SCRIPT_DIR / "pending"
RESPONSE_DIR = SCRIPT_DIR / "responses"

def _process_incoming_message(text: str, channel: str):
    """
    处理收到的消息。
    - 检查 PENDING_DIR 是否有 pending 请求
    - 解析回复文本（提取请求标签 + 选项）
    - 写入 responses/{id}.json
    - 如果 response 文件已存在（被其他渠道抢先），静默退出
    """

def _extract_reply_parts(text: str) -> tuple[str, str]:
    """
    从用户消息中提取请求标签和选项。
    - "A 1" → ("A", "1")
    - "1" → ("", "1")  （无标签，默认最新请求）
    - "a1" → ("A", "1")  （紧凑格式）
    """
```

在微信 `getupdates` 消息循环中增加调用：

```python
# weixin_keepalive_loop() 中，处理 msgs 的部分
for msg in msgs:
    # ... 现有的 context_token 提取不变 ...

    # [新增] 处理用户回复消息
    if PENDING_DIR.exists() and any(PENDING_DIR.glob("*.json")):
        items = msg.get("item_list", [])
        for item in items:
            if item.get("type") == 1:
                text = item.get("text_item", {}).get("text", "")
                if text.strip():
                    _process_incoming_message(text.strip(), "weixin")
                break
```

在 QQ WebSocket 事件处理中增加调用：

```python
# qq_websocket_loop() 中，C2C_MESSAGE_CREATE 事件处理
if event_type == "C2C_MESSAGE_CREATE":
    # ... 现有的 user_openid 提取不变 ...

    # [新增] 处理用户回复消息
    content = event_data.get("content", "").strip()
    if content and PENDING_DIR.exists() and any(PENDING_DIR.glob("*.json")):
        _process_incoming_message(content, "qq")
```

## 7. 多请求处理

### 7.1 请求标签分配

- 第一个 pending 请求标签为 `A`，第二个为 `B`，依此类推
- 标签基于当前 pending 目录中的文件数量递增
- 超过 26 个请求后使用 `AA`, `AB`...（实际上不太可能达到）

### 7.2 终端显示

多请求时终端显示所有 pending 列表：

```
═══════════════════════════════════════════
  Claude Code 等待回复 (2 个请求)
═══════════════════════════════════════════
  #A 执行命令: npm install axios          ← 最新
  #B 编辑文件: src/app.ts

  回复格式: <字母> <选项>，如 "A 1"
  直接输入 "1" 回复最新请求 #A
  三个渠道均可回复，先到先生效。
═══════════════════════════════════════════
请输入: _
```

### 7.3 回复匹配规则

**终端输入：**
- `A 1` → 回复请求 A，选项 1
- `1` → 回复最新请求（标签最大的），选项 1
- `B 是` → 回复请求 B，"是"

**微信/QQ 输入：**
- `A 1` → 回复请求 A，选项 1
- `1` → 回复最新请求，选项 1
- `是` → 回复最新请求，"是"

**解析优先级：**
1. 检查是否以字母标签开头（`A`-`Z`）→ 提取标签 + 剩余文本
2. 无标签 → 默认匹配最新 pending 请求
3. 数字 → 选项选择
4. 非数字 → 自由文本

## 8. 竞争与并发保护

### 8.1 响应文件原子写入

多个渠道可能同时尝试写入 response 文件。使用原子写入确保只有一个生效：

```python
def atomic_write_response(request_id: str, response: dict) -> bool:
    """
    原子写入 response 文件。
    如果文件已存在（被其他渠道抢先），返回 False。
    使用 temp file + rename 实现原子性。
    """
    resp_file = RESPONSE_DIR / f"{request_id}.json"
    if resp_file.exists():
        return False  # 已被其他渠道抢先

    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(RESPONSE_DIR), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
            json.dump(response, f, ensure_ascii=False)
        os.rename(tmp_path, str(resp_file))
        return True
    except FileExistsError:
        os.unlink(tmp_path)
        return False
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return False
```

### 8.2 终端线程与轮询竞争

- 终端读取线程和 response 文件轮询在 `wait_for_response` 中并行运行
- 任一先得到结果，另一个自动退出
- 终端线程通过检查 response 文件是否存在来判断是否需要退出

## 9. 超时处理

| `timeout_seconds` 值 | 行为 |
|---|---|
| `0` | 无限等待，直到有回复 |
| `> 0` | 等待指定秒数，超时后 hook 退出，不输出任何内容（Claude Code 使用默认行为） |

超时时：
1. 清理 pending 文件和 response 文件
2. 终端显示超时提示
3. hook 脚本静默退出（不输出到 stdout）
4. Claude Code 收到空响应，按自身逻辑处理

## 10. 向后兼容

### 10.1 默认行为不变

- `config.json` 中 `interaction` 不存在或 `enabled: false` 时：
  - `notify.py` 的 `main()` 流程完全不变
  - `weixin_keepalive.py` 的消息处理逻辑不变（不检查 pending 目录）
  - 不创建 `pending/` 和 `responses/` 目录

### 10.2 新增文件不影响现有

- `interaction.py` 是独立模块，仅在 `notify.py` 中条件性导入
- `pending/` 和 `responses/` 在 gitignore 中已忽略（或新增忽略规则）

### 10.3 Web UI 兼容

- Dashboard tab 新增"交互模式"开关，不影响现有 tab
- 配置 API 兼容：读取时 `interaction` 节点缺失则使用默认值

## 11. 实现文件清单

| 文件 | 操作 | 改动说明 |
|------|------|---------|
| `interaction.py` | 新增 | 交互核心逻辑（~200 行） |
| `notify.py` | 修改 | `main()` 增加交互分支，`_extract_options()` 函数 |
| `weixin_keepalive.py` | 修改 | 消息处理增加 `_process_incoming_message()` 调用 |
| `app.py` | 修改 | 新增交互配置 API endpoint |
| `static/index.html` | 修改 | Dashboard 新增交互开关 UI |
| `config.json` | 运行时 | 新增 `interaction` 节点 |
| `.gitignore` | 修改 | 新增 `pending/` 和 `responses/` |

## 12. 测试场景

1. **基本审批流程**：触发 PermissionRequest → 终端显示选项 → 输入 `1` → Claude Code 收到 `approve`
2. **微信回复**：触发审批 → 终端显示选项 → 微信回复 `1` → Claude Code 收到 `approve` → 终端自动取消等待
3. **QQ 回复**：同上，通过 QQ 渠道
4. **多请求**：连续触发两个审批 → 终端显示 A/B 列表 → 输入 `B 2` → 请求 B 收到响应
5. **自定义文本**：AskUserQuestion 触发 → 输入自定义内容 → Claude Code 收到自定义文本
6. **多选**：多选问题 → 输入 `1,3` → Claude Code 收到逗号分隔的选项文本
7. **超时**：设置 60 秒超时 → 无回复 → hook 退出，Claude Code 使用默认行为
8. **关闭交互**：`interaction.enabled=false` → 发通知后立即退出，行为与原版一致
9. **竞争**：同时在终端和微信回复 → 只有一个生效，另一个静默忽略
