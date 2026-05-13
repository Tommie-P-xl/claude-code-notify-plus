# ClaudeBeep

ClaudeBeep 是一个 Windows 系统托盘应用，为 [Claude Code](https://claude.ai/code) 提供多渠道通知和交互式审批回复。它将原有的 Python hook 工作流打包为单个可安装桌面应用 —— 一次安装，所有操作从系统托盘管理，仅在需要详细配置时打开 Web UI。

**版本：** `1.0.1`

## 功能

### 系统托盘

- **打开主界面** — 启动 Web UI，用于详细渠道配置、扫码登录和日志查看。
- **通知源管理** — 可展开的子菜单。已配置的通知源启用时显示对号；未配置的通知源置灰，无法勾选。
- **安装/卸载所有 Hooks** — 在 `~/.claude/settings.json` 中注册或移除 Claude Code hook 条目。
- **开机自启动** — 通过 Windows 注册表（`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`）切换开机自启。
- **检查更新** — 查询 GitHub Releases 最新版本；如有新版，直接下载安装包覆盖安装（无需卸载）。
- **退出** — 停止所有后台服务并退出。

### 通知渠道

| 渠道 | 协议 | 保活机制 | 回复监听 |
|------|------|----------|----------|
| Windows Toast | WinRT / `winotify` | 无（发后即忘） | 不适用 |
| 微信 | iLink Bot API | 托盘进程管理的 `getupdates` 长轮询 | keepalive 循环中直接分发 |
| QQ Bot | QQ 开放平台（OAuth2 + c2c/群） | 无（token 缓存） | `listener.py` WebSocket |
| Telegram | Telegram Bot API | 无 | `listener.py` 长轮询 |
| 飞书/Lark | 飞书开放平台（OAuth2） | 无（token 缓存） | `lark_oapi` WebSocket |
| 钉钉 | 钉钉开放平台（OAuth2） | 无（token 缓存） | `dingtalk_stream` |

### 交互式回复

当 Claude Code 提出问题（PermissionRequest / Elicitation）时，ClaudeBeep 向所有已启用渠道发送带编号选项的格式化通知。用户可从以下位置回复：
- 终端（直接键盘输入）
- 任意远程渠道（微信、QQ、Telegram、飞书、钉钉）

先到先得。响应通过临时文件重命名原子写入，防止竞态条件。

### 安全与可靠性

- **多实例防护** — Windows 全局互斥体（`Global\ClaudeBeepTray`）防止重复启动托盘进程。
- **自动清理** — 后台循环每 12 小时（可配置）运行一次，清理日志、过期的 pending/response 文件和队列残留。删除前检查文件是否仍被使用。
- **心跳监控** — 每 15 秒写入 `tray_heartbeat.json`，包含 PID 和渠道状态，支持跨进程协调。
- **优雅降级** — 如果 keepalive 进程未运行，微信回退到直接 HTTP 发送；如果某个渠道失败，其他渠道仍可送达。

## 架构

```
┌─────────────────────────────────────────────────────┐
│                   Claude Code                        │
│  hooks → notify.py --type stop|ask --from-stdin      │
└──────────────────────┬──────────────────────────────┘
                       │ (子进程)
                       ▼
┌─────────────────────────────────────────────────────┐
│              notify.py（hook 入口）                    │
│  • 读取 stdin 上下文                                  │
│  • 过滤自动批准的事件                                  │
│  • 创建待处理请求 (interaction.py)                     │
│  • 向所有已启用渠道发送通知                             │
│  • 等待响应（终端 + 远程监听器）                        │
│  • 向 stdout 输出 hook 响应 JSON                      │
└──────────────────────┬──────────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
   ┌──────────┐ ┌──────────┐ ┌──────────┐
   │ 微信     │ │ QQ/      │ │ Windows  │
   │ (队列)   │ │ TG/...   │ │ Toast    │
   └────┬─────┘ └──────────┘ └──────────┘
        │ IPC（文件队列）
        ▼
┌─────────────────────────────────────────────────────┐
│              tray.py（系统托盘进程）                    │
│  • 微信 keepalive 循环（getupdates 长轮询）            │
│  • 发送队列处理器（同进程 HTTP）                        │
│  • 心跳写入器                                        │
│  • 定时清理器                                        │
│  • Flask Web UI 启动器                               │
└─────────────────────────────────────────────────────┘
```

### 微信 iLink 协议深度解析

iLink Bot API 采用**双层令牌架构**：

| 层级 | 令牌 | 作用域 | 生命周期 | 传输位置 |
|------|------|--------|----------|----------|
| 身份层 | `bot_token` | 全局设备级认证 | 长效（直到重新扫码） | HTTP Header |
| 路由层 | `context_token` | 单次对话消息路由 | 短效（不活跃时过期） | HTTP Body |

**关键协议行为：**

1. **会话绑定** — iLink 服务器将 `bot_token` 绑定到维护 `getupdates` 的 TCP 连接。来自不同进程/连接的发送请求会被静默拒绝，返回 `ret=-2`。

2. **`ret=-2` 语义歧义** — 此错误码被重载：可能表示 `context_token` 过期、参数错误，或跨进程会话不匹配。`errmsg` 字段不可靠（有时为 `"unknown error"`，有时为空）。

3. **无令牌降级重试** — 当 `context_token` 过期时，从请求体中剥离它并重试可能成功。这是协议级别的"降级发送"机制。

4. **`errcode=-14`** — 唯一真正的会话过期信号。需要重新扫描二维码。

**ClaudeBeep 的微信策略：**

- 托盘进程拥有 `getupdates` 长轮询循环，维护活跃的 TCP 会话。
- 当 hook 进程调用 `send()` 时，消息被写入 `send_queue/` 作为 JSON 文件入队。
- keepalive 循环消费队列，通过自身的 HTTP 连接发送消息（同进程、同会话绑定）。
- 遇到 `ret=-2`：清除缓存的 `context_token`，不带 token 重试（无令牌降级）。
- 遇到 `errcode=-14`：禁用渠道，标记会话过期，提示重新登录。
- `context_token` 和 `to_user_id` 从入站消息动态更新 — 不依赖静态配置。

## 安装

从 [GitHub Releases](https://github.com/Tommie-P-xl/ClaudeBeep/releases) 下载最新的 `ClaudeBeep-Setup-x.x.x.exe` 并运行。选择安装目录 —— 所有运行时文件（`config.json`、`notify.log`、`pending/`、`responses/`、`send_queue/`）都保存在该目录中。

安装程序特性：
- 注册到"添加/删除程序"
- 创建开始菜单和可选的桌面快捷方式
- 通过互斥体检测正在运行的实例，覆盖安装前发出警告
- 支持静默安装：`ClaudeBeep-Setup.exe /SILENT /DIR="C:\MyPath"`

## 开发

```powershell
# 安装依赖
pip install -r requirements.txt

# 运行托盘应用
python tray.py

# 或运行单个命令
python notify.py --ui          # 仅 Web UI
python notify.py --install     # 仅安装 hooks
python notify.py --uninstall   # 仅卸载 hooks
python notify.py --test        # 测试所有已启用渠道
```

## 构建

```powershell
# 构建独立可执行文件
./build.ps1
```

生成 `dist/ClaudeBeep.exe`（单文件、窗口模式、UPX 压缩）。

### CI/CD

推送版本标签触发 GitHub Actions 工作流：

```
git tag v1.0.0
git push origin v1.0.0
```

工作流步骤：
1. 设置 Python 3.11
2. 运行 `build.ps1` 生成 EXE
3. 安装 Inno Setup 并构建安装程序
4. 将两者上传为 GitHub Release 资产

## 配置

`config.json` 在首次运行时自动创建，所有字段都有合理默认值：

```json
{
  "app": {
    "version": "1.0.0",
    "auto_cleanup": true,
    "cleanup_interval_hours": 12,
    "update_repo": "Tommie-P-xl/ClaudeBeep"
  },
  "windows_toast": { "enabled": true, "duration_ms": 5000 },
  "weixin": {
    "enabled": false,
    "bot_token": "",
    "baseurl": "https://ilinkai.weixin.qq.com",
    "to_user_id": "",
    "context_token": "",
    "sync_buf": ""
  },
  "qq": { "enabled": false, "app_id": "", "app_secret": "", "target_id": "" },
  "telegram": { "enabled": false, "bot_token": "", "chat_id": "" },
  "feishu": { "enabled": false, "app_id": "", "app_secret": "", "receive_id": "" },
  "dingtalk": { "enabled": false, "client_id": "", "client_secret": "", "user_id": "" },
  "interaction": { "enabled": true, "timeout_seconds": 0, "show_in_terminal": true }
}
```

敏感字段（`bot_token`、`app_secret` 等）在 API 响应中会被脱敏。

## 隐私

以下文件包含敏感或运行时数据，已从版本控制中排除：

- `config.json` — 渠道凭证和令牌
- `notify.log` — 运行日志
- `notify_state.json` — 跨进程去重状态
- `tray_heartbeat.json` — 进程心跳
- `send_queue/` — 瞬态消息队列
- `pending/` / `responses/` — 交互式回复生命周期文件
- `dist/` / `build/` — 构建产物

请勿提交本地令牌或生成的运行时状态。

## 许可证

MIT
