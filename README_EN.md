# ClaudeBeep — Let AI Work While You Relax

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
- **Unique labels** — Labels increase monotonically within a session (A→B→C→...→Z→AA), no duplicates
- **Cross-channel awareness** — After approval on one channel, others proactively receive "already handled" notifications; late replies also get feedback
- **Zero intrusion** — Doesn't modify Claude Code itself, integrates seamlessly via hooks

---

## Quick Start

### Install

```bash
cd ClaudeBeep
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
│  6. Start temporary listener threads (listener.py)           │
│  7. Block and wait for response file                         │
│  8. Receive response → stop listeners → format hook JSON     │
│  9. Send confirmation feedback to the replying channel       │
└──────────┬────────────────┬────────────────┬────────────────┘
           │                │                │
           ▼                ▼                ▼
       Terminal CON   WeChat/QQ/FS/DT/TG    Web UI
       Keyboard input   Temp listeners        Config management
           │            (start on demand)
           ▼                │
    ┌──────────────────────────────┐
    │    responses/{id}.json       │  ← Multi-channel compete, atomic write
    └──────────────────────────────┘
```

**Zero-daemon design:** When a hook fires, the `notify.py` process itself starts temporary listener threads for each enabled channel. All threads exit when a response is received or the timeout hits — no persistent daemon, zero idle memory footprint.

### Core Modules

| File | Responsibility |
|------|---------------|
| `notify.py` | Main entry: hook callbacks, smart filtering, interactive branching |
| `interaction.py` | Interaction core: request management, reply parsing, terminal I/O, file polling |
| `listener.py` | Temporary listeners: per-channel listener threads (Telegram/QQ/Feishu/DingTalk/WeChat), start on demand |
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
| **WeChat** | Stable | Long polling | Via ilink Bot API, session expires after inactivity, re-scan QR to restore |

> **Recommend QQ or Telegram as primary remote notification channels.** WeChat works via ilink Bot API. `context_token` expiration is handled gracefully with auto-degradation (retry without token). Feishu and DingTalk use outbound-only connections, no public IP needed.

### Channel Setup

**WeChat:**
1. Click "Get QR Code" in the Web UI WeChat tab
2. Scan the QR code with WeChat and confirm login
3. After login, find your Bot in WeChat and send a message (e.g., "hello")
4. The system auto-captures the receiver User ID (`to_user_id`), ready to use

> **Note:** WeChat session does not auto-renew. If the session expires after long inactivity (sending returns `ret=-2` or `errcode=-14`), re-scan the QR code to restore.

**Telegram:**
1. Find [@BotFather](https://t.me/BotFather) in Telegram, send `/newbot` to create a bot
2. Get the Bot Token, enter it in the Web UI to validate and save
3. Send a message to the bot, the system auto-captures Chat ID

**Feishu:**
1. Create an enterprise app at [Feishu Open Platform](https://open.feishu.cn/)
2. Enable **Robot** capability
3. **Permissions** → Search and enable:
   - `im:message` — Send and receive messages
   - `im:message.receive_v1` — Receive message events
   - `auth:user_access_token:read` — User info
4. **Event subscription** → Connection mode: **WebSocket** → Add event `im.message.receive_v1`
5. Publish the app (at least to your organization), **permission changes require a new app version to take effect**
6. Enter App ID / App Secret in the Web UI, validate and save
7. Find the bot in Feishu and send a message, the system auto-captures Open ID

**DingTalk:**
1. Create an app at [DingTalk Open Platform](https://open.dingtalk.com/)
2. Add **Robot** capability, select **Stream mode** in message receiving settings
3. **Permissions** → Search and enable:
   - `qyapi_robot_sendmsg` — Send messages
   - `Robot.SingleChat.ReadWrite` — Read/write single chat messages (**required, otherwise user replies won't be received**)
4. Get Client ID and Client Secret (on the app credentials page)
5. Publish the app
6. Enter Client ID / Client Secret in the Web UI, validate and save
7. Find the bot in DingTalk and send a message, the system auto-captures User ID

> **Note:** DingTalk's message receiving capability is built into the "Robot" feature — no need to add events separately in "Event subscription". If messages aren't being received, check that `Robot.SingleChat.ReadWrite` permission is enabled first.

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

All channels listen simultaneously, first reply wins. After a reply:
- The replying channel receives confirmation feedback (e.g., QQ reply → QQ gets "Reply received")
- Other remote channels proactively receive "already handled" notification (e.g., terminal approves → QQ/Telegram gets "#A handled by terminal, no need to reply")
- If you reply after another channel already handled it, you'll get "#A handled by [xx], your reply has been ignored"
- Terminal input doesn't trigger terminal feedback, but other channels still receive the handled notification

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
  All channels can reply, first wins.
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

**Q: No response in terminal after QQ/WeChat/Feishu/DingTalk reply?**
- Confirm `interaction.enabled` is `true`
- Check `notify.log` for `[listener]` entries and message received records
- Verify channel credentials are valid (token not expired)
- Check platform permissions (e.g., DingTalk `Robot.SingleChat.ReadWrite`, Feishu WebSocket event subscription)

**Q: DingTalk/Feishu not receiving user messages?**
- **DingTalk**: Confirm `Robot.SingleChat.ReadWrite` permission is enabled (required for single chat)
- **Feishu**: Confirm `im.message.receive_v1` event is subscribed, connection mode is WebSocket
- Check `notify.log` for `收到消息` logs; absence means the connection isn't receiving events
- Permission changes require publishing a new app version to take effect

**Q: Notifications not appearing?**
- Run `python notify.py --test` to test each channel
- Check `notify.log` to confirm the hook is being triggered

**Q: WeChat not receiving notifications?**
- Confirm `to_user_id` is configured (send a message to the bot in WeChat to auto-capture)
- If log shows `ret=-2` (context_token expired) or `errcode=-14` (bot session expired), re-scan QR code to log in
- `context_token` expiration auto-degrades (retries without token), doesn't affect delivery
- Note: if the bot_token itself is expired, both retries return `ret=-2`; you must re-scan to get a new token

**Q: What if labels run out?**
- After the Claude Code session closes, leftover requests are automatically cleaned up and labels restart from A. Requests within the session can be replied to at any time
- Labels increase monotonically within a session (A→B→C→...→Z→AA→AB), so no label is reused even after a request is cleared

**Q: Flask doesn't exit after closing browser?**
- Auto-exits about 2 seconds after SSE connection disconnects

---

## File Structure

```
ClaudeBeep/
├── notify.py                 # Main entry point
├── interaction.py            # Interaction core module
├── listener.py               # Temporary listeners (zero-daemon core)
├── notify_state.py           # Cross-process state (dedup)
├── notify_hook.bat           # Windows launch script
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

## Changelog

### 2026-05-06 (WeChat Channel Full Fix)

**Request Format Fix (`channels/weixin.py`):**
- Fixed missing `ensure_ascii=False` in `json.dumps` causing Chinese messages to be escaped as `\uXXXX`
- Fixed JSON serialization format: compact `separators=(",",":")` matching iLink API protocol
- Added missing request headers `iLink-App-Id` and `iLink-App-ClientVersion`
- Downgraded `channel_version` from `2.3.1` to `2.2.0` to match Hermes Agent

**context_token Expiration Handling (`channels/weixin.py`):**
- `errcode=-14` (session expired) no longer retries blindly, prompts for re-login
- Other API errors auto-retry once without `context_token` (graceful degradation)
- Added detailed request logging (body + headers) for debugging

**to_user_id Auto-Capture (`listener.py`):**
- Temporary listeners auto-extract `from_user_id` as `to_user_id` from incoming messages (consistent with QQ/Telegram)
- All channel listeners support automatic user ID capture

**Zero-Daemon Refactor (`listener.py` added, `weixin_keepalive.py` removed):**
- Removed `weixin_keepalive.py` persistent daemon; replaced with on-demand temporary listeners
- New `listener.py`: temporary listener threads for all 5 channels (Telegram long-poll, QQ/Feishu WebSocket, DingTalk Stream, WeChat getupdates)
- `interaction.py` integrates `listener.start_listeners()`; all threads exit on response or timeout
- `app.py` removes all keepalive-related calls
- Zero idle memory footprint — no persistent Python process

**Web UI Improvements (`static/index.html`):**
- Auto-shows "waiting for receiver User ID" reminder after QR login (matching DingTalk/Feishu pattern)
- Polls config every 2s, auto-closes reminder when `to_user_id` is captured
- 2-minute timeout with user-friendly message

### 2026-05-03 (Interaction Experience Improvements)

**Unique Labels (`interaction.py`):**
- Introduced persistent monotonic counter (`pending/.label_seq`), labels only increase within a session (A→B→C→...→Z→AA→AB)
- Fixes label duplication confusion in multi-agent scenarios
- Counter resets on `cleanup_all()`, next session starts from A

**Late-Reply Feedback (`weixin_keepalive.py`):**
- New `_send_feedback_to_channel()` helper to send feedback to any channel
- Rewrote `_process_incoming_message()`: properly formatted commands get clear feedback when no pending requests exist, label not found, or already handled by another channel
- Plain chat messages (no label prefix) are still silently ignored

**Cross-Channel Handled Notification (`notify.py`):**
- After approval on one channel, proactively push "#X handled by [channel], no need to reply" to other remote channels
- Combined with late-reply feedback, achieves full-chain status awareness

### 2026-05-03

**Configuration Improvements:**
- DingTalk setup instructions now include `Robot.SingleChat.ReadWrite` permission (required for receiving single chat messages)
- Feishu setup instructions now include WebSocket connection mode and permission details
- Web UI configuration steps updated in sync
- Fixed `requirements.txt` `dingtalk-stream` version (`>=1.0.0` → `>=0.24.0`)

**Connection Stability Improvements (`listener.py`):**
- **Zero-daemon**: No persistent process; temporary listener threads start on hook trigger and exit cleanly
- **DingTalk heartbeat optimization**: Subclass `DingTalkStreamClient` to reduce ping interval from 60s (default) to 10s for faster disconnect detection
- **Feishu watchdog**: Forces connection close via private attribute `_Client__ws_client` when the stop event is set
- **Enhanced logging**: Full message chain logging (connection established → message received → parsed → matched → response written)

---

## References

- [Claude Code Hooks Documentation](https://code.claude.com/docs/en/hooks)
- [CLI-WeChat-Bridge](https://github.com/UNLINEARITY/CLI-WeChat-Bridge)
- [QQ Bot API](https://bot.q.qq.com/wiki/develop/api/)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Feishu Open Platform](https://open.feishu.cn/)
- [DingTalk Open Platform](https://open.dingtalk.com/)
- [cc-connect](https://github.com/chenhg5/cc-connect) — Multi-channel AI Agent bridge tool (Go)
