# ClaudeBeep — 让 AI 干活，你去摸鱼

[English](README_EN.md) | 中文

> Claude Code 在电脑上写代码，你在手机上审批决策。终端、微信、QQ、飞书、钉钉、Telegram 六端联动，随时随地掌控 AI 工作流。

当 Claude Code 需要你的输入时 — 无论是审批文件写入、选择技术方案，还是回答多选问题 — 你都可以直接在手机上回复，无需回到电脑前。

---

## 功能亮点

- **六端回复** — 终端、微信、QQ、飞书、钉钉、Telegram 任一渠道回复即可，先到先生效
- **审批遥控** — 远程批准/拒绝文件操作、命令执行，支持"本次通过"和"全部通过"
- **选择题遥控** — 远程回答 Claude Code 的单选、多选问题，支持多题同时作答
- **智能通知** — 自动过滤已授权操作，只通知真正需要你决策的内容
- **回复反馈** — QQ/微信回复后，同渠道收到确认通知
- **标签唯一** — 会话内标签单调递增（A→B→C→...→Z→AA），不会出现重复标签
- **跨渠道感知** — 某端审批后，其他渠道主动收到"已处理"通知；晚到回复也会收到反馈提示
- **零侵入** — 不修改 Claude Code 本身，通过 hooks 机制无缝集成

---

## 快速开始

### 安装

```bash
cd ClaudeBeep
pip install -r requirements.txt
```

### 启动

```bash
python notify.py --ui
```

浏览器打开 `http://localhost:5100`，在仪表盘中：
1. 开启 QQ 或微信通知
2. 开启交互模式
3. 安装 hooks

### 使用

Claude Code 工作时，审批通知会同时发到终端和手机：

```
【Claude Code - 审批请求 #A】
[D:\project] 写入文件: src/app.py

  1 - Yes
  2 - Yes, allow all edits during this session
  3 - No

回复: A 1
```

在 QQ/微信上回复 `1` 即可批准，终端自动继续执行。

---

## 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                       Claude Code                            │
│          hook 事件: PermissionRequest / Elicitation           │
└───────────────────────────┬─────────────────────────────────┘
                            │ subprocess (stdin = hook context JSON)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    notify.py --type ask --from-stdin          │
│                                                              │
│  1. 读取 hook 上下文，智能过滤                                │
│  2. 提取选项信息（审批/单选/多选）                            │
│  3. 创建 pending 请求文件                                     │
│  4. 发送带选项的通知到微信 + QQ                               │
│  5. 终端显示选项（CON/dev/tty，绕过 stdout）                  │
│  6. 阻塞等待响应文件                                          │
│  7. 收到响应 → 格式化为 hook JSON → stdout 输出给 Claude Code │
│  8. 向回复渠道发送确认反馈                                    │
└──────────┬────────────────┬────────────────┬────────────────┘
           │                │                │
           ▼                ▼                ▼
       终端 CON       微信/QQ/飞书/钉钉/TG     Web UI
       键盘输入         keepalive 守护进程       配置管理
           │           消息监听 + 回复处理
           │                │
           ▼                ▼
    ┌──────────────────────────────┐
    │    responses/{id}.json       │  ← 多渠道竞争，原子写入
    └──────────────────────────────┘
```

### 核心模块

| 文件 | 职责 |
|------|------|
| `notify.py` | 主入口：hook 回调、智能过滤、交互分支 |
| `interaction.py` | 交互核心：请求管理、回复解析、终端 I/O、文件轮询 |
| `weixin_keepalive.py` | 微信 session 保活 + QQ WebSocket 监听 + 消息回复处理 |
| `channels/` | 通知渠道实现（Windows Toast / 微信 / QQ） |
| `app.py` | Flask Web 管理界面后端 |
| `static/index.html` | Web UI 前端（Tailwind + Alpine.js） |

### 数据流

```
hook 触发 → 解析上下文 → 提取选项 → 创建 pending 文件
                                        │
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
               终端显示选项     微信/QQ/飞书/钉钉/TG     Web UI
               等待键盘输入        等待用户回复            配置管理
                    │                   │                   │
                    └───────────────────┼───────────────────┘
                                        ▼
                              responses/{id}.json
                              (谁先写入谁赢)
                                        │
                                        ▼
                              解析回复 → hook JSON 输出
                              → Claude Code 继续执行
```

---

## 通知渠道

| 渠道 | 稳定性 | 连接方式 | 说明 |
|------|--------|---------|------|
| **QQ** | **推荐** | WebSocket | 通过 QQ Bot API 发送，配置 AppID/AppSecret 即可 |
| **Telegram** | **推荐** | 长轮询 | 通过 Bot API 发送，@BotFather 创建 Bot 获取 Token |
| **飞书** | 稳定 | WebSocket | 通过飞书 Open API 发送，需创建企业自建应用 |
| **钉钉** | 稳定 | Stream | 通过钉钉 Open API 发送，需创建应用并添加机器人 |
| **Windows Toast** | 稳定 | 本地 | 系统原生弹窗通知，带提示音 |
| **微信** | 稳定 | 长轮询 | 通过 ilink Bot API 发送，扫码登录后自动保活 |

> **推荐 QQ 或 Telegram 作为主要远程通知渠道。** 微信通过 ilink Bot API 工作，扫码登录后 keepalive 守护进程自动维持 session，`context_token` 过期时自动降级发送。飞书和钉钉均使用出站长连接，无需公网 IP。

### 渠道配置说明

**微信：**
1. 在 Web UI 微信标签页点击"获取二维码"
2. 用微信扫描二维码，确认登录
3. 登录成功后，在微信中找到你的 Bot 并发送一条消息（如"你好"）
4. 系统自动捕获接收用户 ID（`to_user_id`），即可开始使用

> **注意：** 微信需要通过 keepalive 守护进程维持 session。如果长时间未使用导致 session 过期，重新扫码登录即可恢复。

**Telegram：**
1. 在 Telegram 中找 [@BotFather](https://t.me/BotFather)，发送 `/newbot` 创建 Bot
2. 获取 Bot Token，在 Web UI 验证并保存
3. 给 Bot 发送一条消息，系统自动获取 Chat ID

**飞书：**
1. 在 [飞书开放平台](https://open.feishu.cn/) 创建企业自建应用
2. 开启**机器人**能力
3. **权限管理** → 搜索并开启以下权限：
   - `im:message` — 获取与发送消息
   - `im:message.receive_v1` — 接收消息事件
   - `auth:user_access_token:read` — 用户信息
4. **事件订阅** → 连接方式选择 **WebSocket** → 添加事件 `im.message.receive_v1`
5. 发布应用（至少发布到企业内部），**权限变更后需重新发布版本才生效**
6. 在 Web UI 填入 App ID / App Secret，验证并保存
7. 在飞书中找到 Bot 发送一条消息，系统自动获取 Open ID

**钉钉：**
1. 在 [钉钉开放平台](https://open.dingtalk.com/) 创建应用
2. 添加**机器人**能力，在「消息接收模式」中选择 **Stream 模式**
3. **权限管理** → 搜索并开启以下权限：
   - `qyapi_robot_sendmsg` — 发送消息
   - `Robot.SingleChat.ReadWrite` — 读写单聊消息（**必须开启，否则收不到用户回复**）
4. 获取 Client ID 和 Client Secret（在应用凭证页面）
5. 发布应用
6. 在 Web UI 填入 Client ID / Client Secret，验证并保存
7. 在钉钉中找到 Bot 发送一条消息，系统自动获取 User ID

> **注意：** 钉钉机器人接收消息的能力内置于「机器人」能力中，无需在「事件订阅」中单独添加事件。如果收不到消息，请优先检查 `Robot.SingleChat.ReadWrite` 权限是否已开启。

---

## 交互模式

### 开启方式

Web UI 仪表盘 → 交互模式开关 → 开启

可选配置：
- **超时（秒）**：`0` = 无限等待，`>0` = 超时后自动取消
- **终端显示选项**：是否在 Claude Code 终端窗口显示选项提示

### 支持的通知类型

| 类型 | 触发场景 | 回复方式 | hook 响应 |
|------|---------|---------|----------|
| **审批请求** | 文件写入、命令执行 | `1`=批准, `2`=全部批准, `3`=拒绝 | `PermissionRequest` JSON |
| **单选问题** | AskUserQuestion 单选 | `1`/`2`/`3` 或自定义文本 | `Elicitation` JSON |
| **多选问题** | AskUserQuestion 多选 | `1,3,5`（支持中英文逗号） | `Elicitation` JSON |

### 回复格式

**单选：**
```
A 1          → 选择第 1 个选项
A 自定义文本  → 使用自定义内容
1            → 省略标签，默认回复最新请求
```

**多选：**
```
A 1,3        → 选择第 1 和第 3 个选项
A 1，3       → 中文逗号同样支持
```

**多题：**
```
A 1,3|2      → 第 1 题选 1,3；第 2 题选 2（用 | 分隔）
A 1,3。2     → 中文句号同样支持
A 1,3.2      → 英文句号同样支持
A 1,3        → 只答一题时默认回答第 1 题
a 1,3        → 字母大小写均可
```

**审批：**
```
A 1          → 批准本次
A 2          → 批准本次 + 同类操作自动通过
A 3          → 拒绝
A 是         → 关键词匹配：是/yes/ok/批准 → 批准
```

### 竞争机制

所有渠道同时监听，谁先回复算谁的。回复后：
- 回复渠道收到确认反馈（如 QQ 回复 → QQ 收到"已收到回复"）
- 其他远程渠道主动收到"已处理"通知（如终端审批 → QQ/Telegram 收到"#A 已由终端处理，无需再次回复"）
- 如果在其他渠道已处理后才回复，会收到"#A 已由【xx】处理，您的回复已忽略"的反馈
- 终端直接输入则不发送终端反馈，但其他渠道仍会收到已处理通知

### 多请求处理

连续触发多个审批时，终端显示所有 pending 列表：

```
==================================================
  Claude Code 等待回复 (2 个请求)
==================================================
  #A 执行命令: npm install axios          ← 最新
  #B 编辑文件: src/app.ts

  回复格式: <字母> <选项>，如 "A 1"
  直接输入 "1" 回复最新请求 #A
  所有渠道均可回复，先到先生效。
==================================================
```

Claude Code 会话关闭后，残留请求会自动清理，标签从 A 重新开始。会话内的请求不受影响，随时可以回复。

---

## 配置

### config.json

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
    "to_user_id": ""
  },
  "qq": {
    "enabled": false,
    "app_id": "",
    "app_secret": "",
    "target_id": ""
  },
  "telegram": {
    "enabled": false,
    "bot_token": "",
    "chat_id": ""
  },
  "feishu": {
    "enabled": false,
    "app_id": "",
    "app_secret": "",
    "receive_id": ""
  },
  "dingtalk": {
    "enabled": false,
    "app_key": "",
    "app_secret": "",
    "user_id": ""
  },
  "interaction": {
    "enabled": false,
    "timeout_seconds": 0,
    "show_in_terminal": true
  }
}
```

### Hook 事件

| Hook 事件 | 触发时机 | 交互模式 |
|-----------|---------|----------|
| `Stop` | Claude 完成输出 | 仅通知，不交互 |
| `Elicitation` | MCP 请求用户输入 | 进入交互 |
| `PermissionRequest` | 权限弹窗（需用户批准） | 进入交互 |

### 智能过滤

系统按优先级判断是否跳过通知：

1. `bypassPermissions` 模式 → 跳过所有
2. `acceptEdits` 模式 → 跳过 Edit/Write/Read
3. `auto_approved == true` → 跳过
4. `Stop` 事件 → 无条件通知
5. `PermissionRequest` → 通知（用户需关注）

---

## Web 管理界面

```bash
python notify.py --ui
```

| 标签页 | 功能 |
|--------|------|
| **仪表盘** | 渠道开关、交互模式、权限模式、系统状态 |
| **微信** | 扫码登录、登录状态 |
| **QQ Bot** | AppID/AppSecret 配置 |
| **Telegram** | Bot Token 配置 |
| **飞书** | App ID / App Secret 配置 |
| **钉钉** | App Key / App Secret 配置 |
| **Hooks** | 安装/卸载 hook |
| **日志** | 运行日志查看 |

---

## 命令行

```bash
python notify.py --install    # 安装 hooks
python notify.py --uninstall  # 卸载 hooks
python notify.py --test       # 测试通知渠道
python notify.py --ui         # 启动 Web 界面
```

---

## 常见问题

**Q: QQ/微信/飞书/钉钉回复后终端没反应？**
- 检查 keepalive 守护进程是否在运行（`keepalive.pid` 文件）
- 确认 `interaction.enabled` 为 `true`
- 查看 `notify.log` 中是否有 `收到消息` 和 `交互回复` 日志
- 如果日志中没有 `收到消息`，说明消息未到达 keepalive 进程，检查平台权限配置

**Q: 钉钉/飞书收不到用户消息？**
- **钉钉**：确认已开启 `Robot.SingleChat.ReadWrite` 权限（单聊必须）
- **飞书**：确认事件订阅中已添加 `im.message.receive_v1`，且连接方式选择 WebSocket
- 查看 `notify.log` 中是否有 `收到消息` 日志，如果没有说明连接未收到事件推送
- 权限变更后需重新发布应用版本才生效

**Q: 通知没有弹出？**
- 运行 `python notify.py --test` 测试各渠道
- 查看 `notify.log` 确认 hook 是否被触发

**Q: 微信收不到通知？**
- 检查 keepalive 守护进程是否在运行（`keepalive.pid` 文件）
- 确认已配置 `to_user_id`（需要先在微信中给 bot 发一条消息自动获取）
- 如果日志显示 `bot session 过期 (errcode=-14)`，需重新扫码登录
- `context_token` 过期会自动降级（不带 token 发送），不影响消息投递

**Q: 标签用完了怎么办？**
- Claude Code 会话关闭后，残留请求会自动清理，标签从 A 重新开始。会话内的请求随时可以回复，不受时间限制
- 会话内标签单调递增（A→B→C→...→Z→AA→AB），不会因请求被清理而复用旧标签，避免混淆

**Q: 关闭浏览器后 Flask 没退出？**
- SSE 连接断开后约 2 秒自动退出

---

## 文件结构

```
ClaudeBeep/
├── notify.py                 # 主入口
├── interaction.py            # 交互核心模块
├── notify_state.py           # 跨进程状态（去重）
├── notify_hook.bat           # Windows 启动脚本
├── weixin_keepalive.py       # 微信保活 + QQ 监听 + 消息处理
├── app.py                    # Flask Web 后端
├── config.json               # 配置文件（运行时生成）
├── pending/                  # 待响应请求（运行时，自动清理）
├── responses/                # 用户响应（运行时，自动清理）
├── channels/
│   ├── base.py               # 通知渠道基类
│   ├── windows_toast.py      # Windows Toast 实现
│   ├── weixin.py             # 微信 ilink Bot API
│   ├── qq.py                 # QQ Bot API
│   ├── telegram.py           # Telegram Bot API
│   ├── feishu.py             # 飞书 Open API
│   └── dingtalk.py           # 钉钉 Open API
├── static/
│   └── index.html            # Web UI（Tailwind + Alpine.js）
└── new/                      # 设计文档和实现计划
```

---

## 更新日志

### 2026-05-06（微信渠道全面修复）

**请求格式修复（`channels/weixin.py`）：**
- 修复 `json.dumps` 缺少 `ensure_ascii=False` 导致中文消息被转义为 `\uXXXX` 的问题
- 修复 JSON 序列化格式：使用紧凑格式 `separators=(",",":")`，与 iLink API 协议一致
- 补全缺失的请求头 `iLink-App-Id` 和 `iLink-App-ClientVersion`
- `channel_version` 从 `2.3.1` 降级为 `2.2.0`，与 Hermes Agent 保持一致

**context_token 过期处理（`channels/weixin.py`）：**
- `errcode=-14`（session 过期）时不再盲目重试，直接提示需要重新登录
- 其他 API 错误时自动不带 `context_token` 重试一次（优雅降级）
- 发送请求添加详细日志（请求体 + 请求头），便于排查

**to_user_id 自动获取（`weixin_keepalive.py`）：**
- keepalive 收到用户消息时自动提取 `from_user_id` 作为 `to_user_id`（与 QQ/Telegram 行为一致）
- 扫码登录后自动清空旧的 `to_user_id`，等待 keepalive 从新消息中重新获取
- `_init_session_after_login` 同时提取 `context_token` 和 `to_user_id`

**消息处理循环修复（`weixin_keepalive.py`）：**
- 修复用户回复处理代码在 `for msg in msgs:` 循环外的缩进 bug
- 现在每条收到的消息都会被检查和处理，而非只处理最后一条

**keepalive 启动重试（`weixin_keepalive.py`）：**
- 启动时如果配置未就绪，等待重试（最多 5 次，每次 2 秒）而非直接退出
- 解决扫码登录后 keepalive 因 config 未保存完成而立即退出的问题

**Web UI 改进（`static/index.html`）：**
- 扫码登录成功后自动显示"等待获取接收用户 ID"提醒框（参照钉钉/飞书）
- 每 2 秒轮询 config，获取到 `to_user_id` 后自动关闭提醒
- 2 分钟超时后提示用户手动操作

### 2026-05-03（交互体验改进）

**标签唯一性（`interaction.py`）：**
- 引入持久化单调递增计数器（`pending/.label_seq`），会话内标签只增不减（A→B→C→...→Z→AA→AB）
- 解决多 agent 场景下标签重复导致用户混淆的问题
- `cleanup_all()` 时重置计数器，下次会话从 A 重新开始

**晚到回复反馈（`weixin_keepalive.py`）：**
- 新增 `_send_feedback_to_channel()` 辅助函数，支持向任意渠道发送反馈消息
- 重写 `_process_incoming_message()`：格式正确的命令消息在无 pending 请求、标签不存在、已被其他渠道处理时，均向用户发送明确反馈
- 普通聊天消息（无标签前缀）仍然静默忽略，不触发反馈

**跨渠道已处理通知（`notify.py`）：**
- 某端审批后，主动向其他远程渠道推送"#X 已由【渠道】处理，无需再次回复"通知
- 配合晚到回复反馈，实现全链路状态感知

### 2026-05-03

**配置改进：**
- 钉钉配置说明增加 `Robot.SingleChat.ReadWrite` 权限（单聊接收消息必须）
- 飞书配置说明增加 WebSocket 连接模式和权限细节
- Web UI 配置步骤同步更新
- 修正 `requirements.txt` 中 `dingtalk-stream` 版本号（`>=1.0.0` → `>=0.24.0`）

**连接稳定性改进（`weixin_keepalive.py`）：**
- **多实例保护**：启动时自动检测并终止旧的 keepalive 进程，避免多实例争抢连接
- **消息去重**：新增 `MessageDedup` 类（5 分钟 TTL），防止重连后 SDK 重放旧消息导致重复处理
- **钉钉心跳优化**：子类化 `DingTalkStreamClient`，心跳间隔从默认 60 秒缩短到 10 秒，更快检测连接断开
- **飞书重连优化**：看门狗超时从 5 分钟缩短到 2 分钟，重连延迟从 5 秒降到 2 秒
- **日志增强**：全链路日志（连接建立 → 收到消息 → 解析 → 匹配 → 写入 response），连接状态变化有 emoji 标记

---

## 参考

- [Claude Code Hooks 文档](https://code.claude.com/docs/en/hooks)
- [CLI-WeChat-Bridge](https://github.com/UNLINEARITY/CLI-WeChat-Bridge)
- [QQ Bot API](https://bot.q.qq.com/wiki/develop/api/)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [飞书开放平台](https://open.feishu.cn/)
- [钉钉开放平台](https://open.dingtalk.com/)
- [cc-connect](https://github.com/chenhg5/cc-connect) — 多渠道 AI Agent 桥接工具（Go 实现）
