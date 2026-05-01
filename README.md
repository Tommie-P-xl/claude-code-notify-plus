# claude-code-notify-plus

当 Claude Code 完成输出、弹出询问、编辑文件或执行命令时，自动向 **Windows Toast**、**微信**和/或 **QQ** 发送通知提醒。支持**交互模式**：收到审批通知后，可通过终端、微信或 QQ 直接回复审批选项。

> **重要提示：请妥善保存本项目文件夹。** 本项目的所有功能（hooks 配置、通知发送、保活守护进程）都依赖于项目目录中的文件。如果删除或移动了项目文件夹，通知功能将失效，需要重新下载并重新配置。

---

## 第一部分：功能使用说明

### 功能概览

| 渠道 | 稳定性 | 说明 | 依赖 |
|------|--------|------|------|
| **QQ** | **推荐** | 通过 QQ Bot API 直接发送，稳定可靠 | 无需额外依赖，配置 AppID/AppSecret 即可 |
| **Windows Toast** | 稳定 | 系统原生弹窗通知，带提示音 | `winotify`（自动安装） |
| **微信** | 容易过期 | 通过 ilink Bot API 发送到微信，`context_token` 会过期需手动恢复 | 无需额外依赖，扫码登录即可 |

> **推荐使用 QQ 通知作为主要的远程通知渠道。** 微信通知依赖 `context_token`（会话上下文令牌），该令牌会随时间过期（通常数小时到一天），过期后需要用户在微信中给 bot 发送一条消息才能恢复。这是 ilink API 的协议限制，无法通过代码解决。QQ 通知没有此问题，配置完成后可长期稳定运行。

### 安装

```bash
cd claude-code-notify-plus
pip install -r requirements.txt
```

依赖列表：
- `flask>=2.3` — Web 管理界面后端
- `winotify>=1.1` — Windows 原生 Toast 通知（比 PowerShell 方案快 5-10 秒）
- `websockets>=12.0` — QQ Bot WebSocket 事件监听（自动获取 user_openid）

### 方式一：Web 管理界面（推荐）

```bash
python notify.py --ui
```

浏览器自动打开 `http://localhost:5100`，提供 5 个功能标签页：

| 标签页 | 功能 |
|--------|------|
| **仪表盘** | 三个渠道的开关、**交互模式开关**、**权限模式切换**、快捷操作、系统状态 |
| **微信** | 扫码登录、登录状态、手动配置 token/用户 ID |
| **QQ Bot** | AppID/AppSecret 输入、验证、Target ID 配置 |
| **Hooks** | 查看 hook 事件安装状态、一键安装/卸载 |
| **日志** | 查看/清除运行日志 |

**自动退出机制：** 通过 SSE（Server-Sent Events）持久连接检测标签页状态。每个标签页打开时建立 SSE 连接，关闭标签页时连接断开，服务端检测到所有连接断开后自动退出。

### 方式二：命令行

```bash
python notify.py --install    # 安装 Claude Code hooks
python notify.py --uninstall  # 卸载 hooks
python notify.py --test       # 测试所有已启用渠道
```

### 交互模式（新增）

交互模式允许你在收到审批通知后，直接通过终端、微信或 QQ 回复审批选项，无需回到电脑前操作。

#### 开启方式

1. 运行 `python notify.py --ui`
2. 在仪表盘找到"交互模式"卡片，打开开关
3. 可选配置：
   - **超时（秒）**：`0` 表示无限等待，大于 0 表示超时后自动取消
   - **终端显示选项**：是否在 Claude Code 终端窗口显示选项提示

#### 工作流程

```
Claude Code 触发审批通知
    │
    ├── 终端窗口显示选项（如: 1-批准, 2-拒绝）
    ├── 微信/QQ 收到带选项编号的通知
    │
    └── 三个渠道均可回复，先到先生效
        ├── 终端直接输入: 1
        ├── 微信回复: 1
        └── QQ 回复: 1
```

#### 回复格式

**审批请求（PermissionRequest）：**
```
1 或 批准    → 批准
2 或 拒绝    → 拒绝
```

**单选问题（AskUserQuestion）：**
```
1            → 选择第 1 个选项
自定义文本   → 使用自定义内容
```

**多选问题：**
```
1,3          → 选择第 1 和第 3 个选项
1,自定义     → 选择第 1 个 + 自定义内容
```

**多请求时：**
```
A 1          → 回复请求 A 的选项 1
1            → 回复最新请求的选项 1（省略标签）
```

#### 竞争机制

三个渠道（终端、微信、QQ）同时监听，谁先回复算谁的。回复后其他渠道自动取消等待。

### 微信通知配置流程

1. 运行 `python notify.py --ui`
2. 切换到"微信"标签页
3. 点击"获取二维码"
4. 用微信扫描显示的二维码并确认授权
5. Token 和用户 ID 自动保存
6. 开启微信通知开关

**Token 持久化：** 扫码登录后 token 保存在 `config.json` 中，后续使用无需重复扫码。Token 过期时，keepalive 守护进程会自动检测并提示重新登录。

### QQ Bot 通知配置流程

1. 访问 [QQ 开放平台](https://q.qq.com/) 创建 Bot，获取 AppID 和 AppSecret
2. 运行 `python notify.py --ui`，切换到"QQ Bot"标签页
3. 填入 AppID 和 AppSecret，点击"验证并保存"
4. **在 QQ 中给 bot 发送一条消息**（如"你好"），系统会自动获取你的 OpenID 并完成配置
5. 开启 QQ 通知开关

系统通过 WebSocket 自动监听 QQ 消息事件，无需手动查找或填入 OpenID。

> QQ Bot API 有主动消息限制（约 4 条/用户/月），如果通知发送失败可能是配额用尽。

### 通知事件覆盖

安装 hooks 后，以下场景会触发通知：

| Hook 事件 | 触发时机 | 交互模式 |
|-----------|---------|----------|
| `Stop` | Claude 完成输出 | 不进入交互（仅通知） |
| `Elicitation` | MCP 服务器请求用户输入 | 进入交互（带选项） |
| `PermissionRequest` | 权限弹窗出现时（需用户手动批准） | 进入交互（批准/拒绝） |

### 配置文件说明

配置文件位于 `config.json`：

```json
{
  "windows_toast": {
    "enabled": true,
    "duration_ms": 5000,
    "sound": "reminder"
  },
  "weixin": {
    "enabled": false,
    "bot_token": "",
    "baseurl": "https://ilinkai.weixin.qq.com",
    "to_user_id": "",
    "context_token": ""
  },
  "qq": {
    "enabled": false,
    "app_id": "",
    "app_secret": "",
    "target_id": ""
  },
  "interaction": {
    "enabled": false,
    "timeout_seconds": 0,
    "show_in_terminal": true
  }
}
```

| `interaction` 字段 | 默认值 | 说明 |
|---|---|---|
| `enabled` | `false` | 是否启用交互功能。关闭时所有行为不变 |
| `timeout_seconds` | `0` | 等待响应超时。`0` = 无限等待 |
| `show_in_terminal` | `true` | 是否在终端显示选项提示 |

### 常见问题

**Q: 通知没有弹出？**
- 检查 Windows 通知设置中是否允许"Claude Code"显示通知
- 运行 `python notify.py --test` 测试各渠道
- 查看 `notify.log` 确认 hook 是否被触发

**Q: 微信收不到通知？**
- 检查 `notify.log` 中是否有 `session timeout` 错误
- 如有，说明 token 已过期，需在 Web UI 重新扫码登录
- 确保 `weixin_keepalive.py` 进程在运行（检查 `keepalive.pid` 文件）

**Q: 交互模式下终端没有显示选项？**
- 确认 `config.json` 中 `interaction.enabled` 为 `true`
- 确认 `interaction.show_in_terminal` 为 `true`
- 检查是否触发的是 `Stop` 事件（Stop 不进入交互模式）

**Q: 微信/QQ 回复了但 Claude Code 没收到？**
- 检查 `notify.log` 中是否有 `交互回复` 日志
- 确认回复格式正确（如 `1` 或 `A 1`）
- 确认 pending 请求未超时

**Q: 如何临时禁用通知？**
- 在 Web UI 仪表盘关闭对应渠道开关
- 或卸载 hooks：`python notify.py --uninstall`

---

## 第二部分：逻辑实现详解

### 整体架构

```
claude-code-notify-plus/
├── notify.py                 # 主入口：hook 回调 + CLI + 智能过滤 + 交互分支
├── interaction.py            # 交互核心：请求管理、响应解析、终端 I/O、文件轮询
├── notify_state.py           # 跨进程状态：tool_use_id 去重
├── notify_hook.bat           # Windows 启动脚本（设置 PYTHONUTF8=1）
├── weixin_keepalive.py       # 微信 session 保活 + QQ WebSocket 监听 + 消息回复处理
├── app.py                    # Flask Web 后端
├── config.json               # 渠道配置 + 交互配置
├── pending/                  # 运行时：待响应的交互请求
├── responses/                # 运行时：用户回复的响应文件
├── channels/
│   ├── base.py               # 抽象基类 NotificationChannel
│   ├── windows_toast.py      # Windows Toast 实现
│   ├── weixin.py             # 微信 ilink Bot API 实现
│   └── qq.py                 # QQ Bot API 实现
├── static/
│   └── index.html            # Web UI 前端（Tailwind + Alpine.js）
└── notify.log                # 运行日志
```

### 交互模式架构

```
Claude Code (hook 事件)
    │
    ▼
notify.py --type ask --from-stdin
    │
    ├─ 解析 hook 上下文，提取选项
    ├─ 创建 pending/{id}.json
    ├─ 格式化带选项编号的通知消息
    ├─ 发送到微信 + QQ
    ├─ 终端显示选项提示（新线程读 CON/dev/tty）
    └─ 主线程轮询 responses/{id}.json
            │
            ▼
        responses/{id}.json  ← 谁先写入谁赢
            │
    ┌───────┼───────┐
    │       │       │
  终端    微信     QQ
  CON    keepalive  keepalive
```

**核心模块 `interaction.py`：**

| 函数 | 职责 |
|------|------|
| `is_interactive_enabled()` | 检查配置中交互是否启用 |
| `create_request()` | 创建 pending 请求文件 |
| `list_requests()` | 列出所有 pending 请求 |
| `format_notification_message()` | 格式化带选项的通知消息 |
| `format_terminal_prompt()` | 格式化终端显示的选项提示 |
| `wait_for_response()` | 轮询等待响应（终端线程 + 文件轮询竞争） |
| `parse_reply()` | 解析用户回复（approve/deny/选项文本/自定义） |
| `write_response()` | 原子写入 response 文件（竞争保护） |
| `cleanup_request()` | 清理 pending 和 response 文件 |

**竞争保护：** 使用 `tempfile.mkstemp()` + `os.rename()` 实现原子写入。如果 response 文件已存在（被其他渠道抢先），写入失败并返回 `False`。

### 核心流程：Hook 触发到通知发送

```
Claude Code 触发 hook 事件
    │
    ▼
notify.py main()
    │
    ├── 1. 加载 config.json
    ├── 2. 启动 keepalive 守护进程（如果微信已启用）
    ├── 3. 从 stdin 读取 hook 上下文 JSON
    ├── 4. 智能过滤判断 (_is_auto_approved)
    ├── 5. 提取上下文文本 (_extract_context_text)
    │
    ├── 6a. 交互模式（hook_type == "ask" 且 interaction.enabled）:
    │       ├── 提取选项 (_extract_options)
    │       ├── 创建 pending 请求
    │       ├── 发送带选项的通知
    │       ├── 等待响应（终端 + 微信 + QQ 竞争）
    │       ├── 解析回复 → stdout 输出给 Claude Code
    │       └── 清理 pending/response 文件
    │
    └── 6b. 普通模式（默认）:
            └── 遍历所有已启用渠道，调用 ch.send(title, message)
```

### Hook 注册机制

`install_hooks()` 在 `~/.claude/settings.json` 的 `hooks` 字段中注册 3 个事件：

```json
{
  "hooks": {
    "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "..."}]}],
    "Elicitation": [{"matcher": "", "hooks": [{"type": "command", "command": "..."}]}],
    "PermissionRequest": [{"matcher": "", "hooks": [{"type": "command", "command": "..."}]}]
  }
}
```

- `Stop`：Claude 完成输出，发送完成通知（不进入交互）
- `Elicitation`：MCP 请求用户输入，发送通知 + 进入交互
- `PermissionRequest`：权限弹窗，发送通知 + 进入交互

### 微信通知实现（channels/weixin.py）

使用 ilink Bot API（微信官方/半官方 bot 通道），不依赖 OpenClaw。

**Session 保活机制（weixin_keepalive.py）：**

运行独立的后台守护进程，定期调用 `getupdates` API 保持 session 存活。同时监听用户回复消息，匹配 pending 请求并写入 response 文件。

### QQ Bot 通知实现（channels/qq.py）

直接调用 QQ Bot API，不依赖 OpenClaw。通过 WebSocket 自动获取 `user_openid`，同时监听用户回复消息。

### Windows Toast 通知实现（channels/windows_toast.py）

双方案策略：
1. **优先使用 winotify**（快速，~50ms）
2. **回退到 PowerShell**（慢，~3-5s）

### 参考项目

- [CLI-WeChat-Bridge](https://github.com/UNLINEARITY/CLI-WeChat-Bridge) — 微信 ilink Bot API 实现参考
- [openclaw-weixin](https://www.npmjs.com/package/@tencent-weixin/openclaw-weixin) — ilink Bot API 官方 Node.js 实现
- [openclaw-qqbot](https://github.com/tencent-connect/openclaw-qqbot) — QQ Bot API 官方实现参考
