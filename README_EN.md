# Claude Code Remote — Let AI Work While You Relax

English | [中文](README.md)

> Claude Code writes code on your PC, you approve decisions from your phone. Terminal, WeChat, QQ, Feishu, DingTalk, Telegram — six channels working together, control your AI workflow from anywhere.

When Claude Code needs your input — whether it's approving file writes, choosing a technical approach, or answering multiple-choice questions — you can reply directly from your phone without returning to your computer.

---

## Features

- **Six-channel reply** — Terminal, WeChat, QQ, Feishu, DingTalk, Telegram — first reply wins
- **Remote approval** — Approve/reject file operations and command execution, with "approve this" and "approve all" options
- **Remote selection** — Answer Claude Code's single-choice and multiple-choice questions remotely, with multi-question support
- **Smart notifications** — Auto-filters authorized operations, only notifies for decisions that need your attention
- **Reply feedback** — Receive confirmation on the same channel after replying via QQ/WeChat
- **Zero intrusion** — Doesn't modify Claude Code itself, integrates seamlessly via hooks

---

## Quick Start

### Install

```bash
cd claude-code-notify-plus
pip install -r requirements.txt
```

### Launch

```bash
python notify.py --ui
```

Open `http://localhost:5100` in your browser, then from the dashboard:
1. Enable QQ or WeChat notifications
2. Enable interactive mode
3. Install hooks

### Usage

When Claude Code is working, approval notifications are sent to both the terminal and your phone:

```
【Claude Code - Approval Request #A】
[D:\project] Write file: src/app.py

  1 - Yes
  2 - Yes, allow all edits during this session
  3 - No

Reply: A 1
```

Reply `1` on QQ/WeChat to approve, and the terminal continues automatically.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       Claude Code                            │
│          Hook Events: PermissionRequest / Elicitation         │
└───────────────────────────┬─────────────────────────────────┘
                            │ subprocess (stdin = hook context JSON)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    notify.py --type ask --from-stdin          │
│                                                              │
│  1. Read hook context, smart filtering                       │
│  2. Extract option info (approval/single/multi-select)       │
│  3. Create pending request file                              │
│  4. Send notification with options to WeChat + QQ            │
│  5. Display options in terminal (CON/dev/tty, bypass stdout) │
│  6. Block and wait for response file                         │
│  7. Receive response → format as hook JSON → stdout          │
│  8. Send confirmation feedback to the replying channel       │
└──────────┬────────────────┬────────────────┬────────────────┘
           │                │                │
           ▼                ▼                ▼
       Terminal CON     WeChat/QQ         Web UI
       Keyboard input   keepalive daemon   Config management
                        Message listening + reply handling
           │                │
           ▼                ▼
    ┌──────────────────────────────┐
    │    responses/{id}.json       │  ← Three channels compete, atomic write
    └──────────────────────────────┘
```

### Core Modules

| File | Responsibility |
|------|---------------|
| `notify.py` | Main entry: hook callbacks, smart filtering, interactive branching |
| `interaction.py` | Interaction core: request management, reply parsing, terminal I/O, file polling |
| `weixin_keepalive.py` | WeChat session keepalive + QQ/Telegram/Feishu/DingTalk message listening |
| `channels/` | Notification channel implementations (Windows Toast / WeChat / QQ / Telegram / Feishu / DingTalk) |
| `app.py` | Flask Web management backend |
| `static/index.html` | Web UI frontend (Tailwind + Alpine.js) |

---

## Notification Channels

| Channel | Stability | Connection | Description |
|---------|-----------|------------|-------------|
| **QQ** | **Recommended** | WebSocket | Via QQ Bot API, configure AppID/AppSecret |
| **Telegram** | **Recommended** | Long polling | Via Bot API, create bot via @BotFather for Token |
| **Feishu** | Stable | WebSocket | Via Feishu Open API, requires enterprise app |
| **DingTalk** | Stable | Stream | Via DingTalk Open API, requires app with robot capability |
| **Windows Toast** | Stable | Local | Native system toast notification with sound |
| **WeChat** | Prone to expiry | Long polling | Via ilink Bot API, `context_token` expires and needs manual refresh |

> **Recommend QQ or Telegram as primary remote notification channels.** WeChat's `context_token` expires over time (usually hours to a day), requiring you to message the bot in WeChat to restore it. QQ and Telegram don't have this issue. Feishu and DingTalk use outbound-only connections, no public IP needed.

### Channel Setup

**Telegram:**
1. Find [@BotFather](https://t.me/BotFather) in Telegram, send `/newbot` to create a bot
2. Get the Bot Token, enter it in the Web UI to validate and save
3. Send a message to the bot, the system auto-captures Chat ID

**Feishu:**
1. Create an enterprise app at [Feishu Open Platform](https://open.feishu.cn/)
2. Enable **Robot** capability
3. Add permissions: `im:message`, `im:message.receive_v1`, `auth:user_access_token:read`
4. Event subscription → Add `im.message.receive_v1`
5. Publish the app (at least to your organization)
6. Enter App ID / App Secret in the Web UI, validate and save
7. Find the bot in Feishu and send a message, the system auto-captures Open ID

**DingTalk:**
1. Create an app at [DingTalk Open Platform](https://open.dingtalk.com/)
2. Add **Robot** capability
3. Select **Stream mode** in message receiving settings
4. Get Client ID and Client Secret (on the app credentials page)
5. Publish the app
6. Enter Client ID / Client Secret in the Web UI, validate and save
7. Find the bot in DingTalk and send a message, the system auto-captures User ID

---

## Interactive Mode

### Enabling

Web UI Dashboard → Interactive Mode toggle → Enable

Optional settings:
- **Timeout (seconds)**: `0` = infinite wait, `>0` = auto-cancel after timeout
- **Show options in terminal**: Whether to show option prompts in the Claude Code terminal window

### Supported Notification Types

| Type | Trigger | Reply Format | Hook Response |
|------|---------|-------------|---------------|
| **Approval** | File write, command execution | `1`=approve, `2`=approve all, `3`=deny | `PermissionRequest` JSON |
| **Single choice** | AskUserQuestion single-select | `1`/`2`/`3` or custom text | `Elicitation` JSON |
| **Multiple choice** | AskUserQuestion multi-select | `1,3,5` (comma-separated) | `Elicitation` JSON |

### Reply Format

**Single choice:**
```
A 1          → Select option 1
A custom text → Use custom content
1            → Omit label, reply to latest request by default
```

**Multiple choice:**
```
A 1,3        → Select options 1 and 3
A 1，3       → Chinese comma also supported
```

**Multi-question:**
```
A 1,3|2      → Q1: select 1,3; Q2: select 2 (separated by |)
A 1,3。2     → Chinese period also supported
A 1,3.2      → English period also supported
A 1,3        → Single question: defaults to Q1
a 1,3        → Case-insensitive label
```

**Approval:**
```
A 1          → Approve this time
A 2          → Approve this + auto-approve similar operations
A 3          → Deny
A yes        → Keyword matching: 是/yes/ok/approve → approve
```

### Competition Mechanism

Three channels listen simultaneously, first reply wins. After a reply:
- Other channels automatically cancel their wait
- The replying channel receives confirmation feedback (e.g., QQ reply → QQ gets "Reply received")
- Terminal input doesn't trigger feedback

### Multi-Request Handling

When multiple approvals are triggered consecutively, the terminal displays all pending requests:

```
==================================================
  Claude Code Awaiting Reply (2 requests)
==================================================
  #A Execute command: npm install axios     ← Latest
  #B Edit file: src/app.ts

  Reply format: <letter> <option>, e.g., "A 1"
  Type "1" to reply to latest request #A
  All three channels can reply, first wins.
==================================================
```

After the Claude Code session closes, leftover requests are automatically cleaned up and labels restart from A. Requests within the session are preserved and can be replied to at any time.

---

## Configuration

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
    "client_id": "",
    "client_secret": "",
    "user_id": ""
  },
  "interaction": {
    "enabled": false,
    "timeout_seconds": 0,
    "show_in_terminal": true
  }
}
```

### Hook Events

| Hook Event | Trigger | Interactive Mode |
|------------|---------|-----------------|
| `Stop` | Claude finishes output | Notification only, no interaction |
| `Elicitation` | MCP requests user input | Enter interaction |
| `PermissionRequest` | Permission popup (user approval needed) | Enter interaction |

### Smart Filtering

The system determines whether to skip notifications by priority:

1. `bypassPermissions` mode → Skip all
2. `acceptEdits` mode → Skip Edit/Write/Read
3. `auto_approved == true` → Skip
4. `Stop` event → Always notify
5. `PermissionRequest` → Notify (user attention needed)

---

## Web Management Interface

```bash
python notify.py --ui
```

| Tab | Function |
|-----|----------|
| **Dashboard** | Channel toggles, interactive mode, permission mode, system status |
| **WeChat** | QR login, login status |
| **QQ Bot** | AppID/AppSecret configuration |
| **Telegram** | Bot Token configuration |
| **Feishu** | App ID / App Secret configuration |
| **DingTalk** | Client ID / Client Secret configuration |
| **Hooks** | Install/uninstall hooks |
| **Logs** | View running logs |

---

## CLI Commands

```bash
python notify.py --install    # Install hooks
python notify.py --uninstall  # Uninstall hooks
python notify.py --test       # Test notification channels
python notify.py --ui         # Launch Web UI
```

---

## FAQ

**Q: No response in terminal after QQ/WeChat reply?**
- Check if the keepalive daemon is running (look for `keepalive.pid` file)
- Confirm `interaction.enabled` is `true`
- Check `notify.log` for `交互响应` (interaction response) logs

**Q: Notifications not appearing?**
- Run `python notify.py --test` to test each channel
- Check `notify.log` to confirm the hook is being triggered

**Q: WeChat not receiving notifications?**
- The `context_token` may have expired, re-scan QR code in Web UI to log in

**Q: What if labels run out?**
- After the Claude Code session closes, leftover requests are automatically cleaned up and labels restart from A. Requests within the session can be replied to at any time

**Q: Flask doesn't exit after closing browser?**
- Auto-exits about 2 seconds after SSE connection disconnects

---

## File Structure

```
claude-code-notify-plus/
├── notify.py                 # Main entry point
├── interaction.py            # Interaction core module
├── notify_state.py           # Cross-process state (dedup)
├── notify_hook.bat           # Windows launch script
├── weixin_keepalive.py       # WeChat keepalive + channel listeners
├── app.py                    # Flask Web backend
├── config.json               # Config file (generated at runtime)
├── pending/                  # Pending requests (runtime, auto-cleanup)
├── responses/                # User responses (runtime, auto-cleanup)
├── channels/
│   ├── base.py               # Notification channel base class
│   ├── windows_toast.py      # Windows Toast implementation
│   ├── weixin.py             # WeChat ilink Bot API
│   ├── qq.py                 # QQ Bot API
│   ├── telegram.py           # Telegram Bot API
│   ├── feishu.py             # Feishu Open API
│   └── dingtalk.py           # DingTalk Open API
└── static/
    ├── index.html            # Web UI (Tailwind + Alpine.js)
    └── vendor/               # Local JS libraries
```

---

## References

- [Claude Code Hooks Documentation](https://code.claude.com/docs/en/hooks)
- [CLI-WeChat-Bridge](https://github.com/UNLINEARITY/CLI-WeChat-Bridge)
- [QQ Bot API](https://bot.q.qq.com/wiki/develop/api/)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Feishu Open Platform](https://open.feishu.cn/)
- [DingTalk Open Platform](https://open.dingtalk.com/)
- [cc-connect](https://github.com/chenhg5/cc-connect) — Multi-channel AI Agent bridge tool (Go)
