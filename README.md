# ClaudeBeep

ClaudeBeep is a Windows system tray application that brings multi-channel notifications and interactive approval replies to [Claude Code](https://claude.ai/code). It packages the original Python hook workflow as a single installable desktop application — install once, manage everything from the system tray, and open the full Web UI only when detailed configuration is needed.

**Version:** `1.0.0`

## Features

### System Tray

- **Open Dashboard** — launches the Web UI for detailed channel configuration, QR login, and log viewing.
- **Notification Sources** — expandable submenu. Configured sources show a checkmark when enabled; unconfigured sources are greyed out and cannot be toggled.
- **Install / Uninstall All Hooks** — registers or removes Claude Code hook entries in `~/.claude/settings.json`.
- **Start with Windows** — toggles per-user auto-start via the Windows registry (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`).
- **Check for Updates** — queries GitHub Releases for the latest version; if newer, downloads and runs the installer in-place (no uninstall required).
- **Quit** — stops all background services and exits.

### Notification Channels

| Channel | Protocol | Keepalive | Reply Listening |
|---------|----------|-----------|-----------------|
| Windows Toast | WinRT / `winotify` | None (fire-and-forget) | N/A |
| WeChat | iLink Bot API | Tray-managed `getupdates` long-poll | Direct dispatch in keepalive loop |
| QQ Bot | QQ Open API (OAuth2 + c2c/group) | None (token cached) | WebSocket via `listener.py` |
| Telegram | Telegram Bot API | None | Long-polling via `listener.py` |
| Feishu/Lark | Feishu Open API (OAuth2) | None (token cached) | WebSocket via `lark_oapi` |
| DingTalk | DingTalk Open API (OAuth2) | None (token cached) | Stream via `dingtalk_stream` |

### Interactive Replies

When Claude Code asks a question (PermissionRequest / Elicitation), ClaudeBeep sends a formatted notification with numbered options to all enabled channels. The user can reply from:
- The terminal (direct keyboard input)
- Any remote channel (WeChat, QQ, Telegram, Feishu, DingTalk)

The first reply wins. Responses are written atomically via temp-file rename to prevent race conditions.

### Safety & Reliability

- **Multi-instance protection** — a Windows global mutex (`Global\ClaudeBeepTray`) prevents duplicate tray processes.
- **Automatic cleanup** — a background loop runs every 12 hours (configurable) to trim logs, remove stale pending/response files, and clean up queue artifacts. Files are checked for active handles before deletion.
- **Heartbeat monitoring** — `tray_heartbeat.json` is written every 15 seconds with PID and channel status, enabling cross-process coordination.
- **Graceful degradation** — if the keepalive process is not running, WeChat falls back to direct HTTP sending; if a channel fails, other channels still deliver.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Claude Code                        │
│  hooks → notify.py --type stop|ask --from-stdin      │
└──────────────────────┬──────────────────────────────┘
                       │ (subprocess)
                       ▼
┌─────────────────────────────────────────────────────┐
│              notify.py (hook entry point)             │
│  • Reads stdin context                               │
│  • Filters auto-approved events                      │
│  • Creates pending request (interaction.py)          │
│  • Sends notification to all enabled channels        │
│  • Waits for response (terminal + remote listeners)  │
│  • Outputs hook response JSON to stdout              │
└──────────────────────┬──────────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
   ┌──────────┐ ┌──────────┐ ┌──────────┐
   │ WeChat   │ │ QQ/      │ │ Windows  │
   │ (queue)  │ │ TG/...   │ │ Toast    │
   └────┬─────┘ └──────────┘ └──────────┘
        │ IPC (file queue)
        ▼
┌─────────────────────────────────────────────────────┐
│              tray.py (system tray process)            │
│  • WeChat keepalive loop (getupdates long-poll)      │
│  • Send queue processor (same-process HTTP)          │
│  • Heartbeat writer                                  │
│  • Periodic cleanup                                  │
│  • Flask Web UI launcher                             │
└─────────────────────────────────────────────────────┘
```

### WeChat iLink Protocol — Deep Dive

The iLink Bot API uses a **dual-layer token architecture**:

| Layer | Token | Scope | Lifetime | Transport |
|-------|-------|-------|----------|-----------|
| Identity | `bot_token` | Global device-level auth | Long-lived (until QR re-scan) | HTTP Header |
| Routing | `context_token` | Per-conversation message routing | Short-lived (expires on inactivity) | HTTP Body |

**Key protocol behaviors:**

1. **Session binding** — the iLink server binds `bot_token` to the TCP connection that maintains `getupdates`. Send requests from a different process/connection are silently rejected with `ret=-2`.

2. **`ret=-2` ambiguity** — this error code is overloaded: it can mean stale `context_token`, parameter error, OR cross-process session mismatch. The `errmsg` field is unreliable (sometimes `"unknown error"`, sometimes empty).

3. **Tokenless fallback** — when `context_token` has expired, stripping it from the request body and retrying can succeed. This is a protocol-level "degraded send" mechanism.

4. **`errcode=-14`** — the only true session expiry signal. Requires re-scanning the QR code.

**ClaudeBeep's WeChat strategy:**

- The tray process owns the `getupdates` long-poll loop, maintaining the active TCP session.
- When `send()` is called from the hook process, the message is enqueued to `send_queue/` as a JSON file.
- The keepalive loop drains the queue and sends messages through its own HTTP connection (same process, same session binding).
- On `ret=-2`: clears cached `context_token`, retries without it (tokenless fallback).
- On `errcode=-14`: disables the channel, marks session expired, prompts for re-login.
- `context_token` and `to_user_id` are dynamically updated from inbound messages — no static config dependency.

## Installation

Download the latest `ClaudeBeep-Setup-x.x.x.exe` from [GitHub Releases](https://github.com/Tommie-P-xl/ClaudeBeep/releases) and run it. Choose the installation directory — all runtime files (`config.json`, `notify.log`, `pending/`, `responses/`, `send_queue/`) are stored there.

The installer:
- Registers the application in Add/Remove Programs
- Creates Start Menu and optional Desktop shortcuts
- Detects a running instance via mutex and warns before overwriting
- Supports silent install: `ClaudeBeep-Setup.exe /SILENT /DIR="C:\MyPath"`

## Development

```powershell
# Install dependencies
pip install -r requirements.txt

# Run the tray application
python tray.py

# Or run individual commands
python notify.py --ui          # Web UI only
python notify.py --install     # Install hooks only
python notify.py --uninstall   # Uninstall hooks only
python notify.py --test        # Test all enabled channels
```

## Build

```powershell
# Build the standalone executable
./build.ps1
```

This creates `dist/ClaudeBeep.exe` (single-file, windowed, UPX-compressed).

### CI/CD

Pushing a version tag triggers the GitHub Actions workflow:

```
git tag v1.0.0
git push origin v1.0.0
```

The workflow:
1. Sets up Python 3.11
2. Runs `build.ps1` to produce the EXE
3. Installs Inno Setup and builds the installer
4. Uploads both as GitHub Release assets

## Configuration

`config.json` is created automatically on first run. All fields have sensible defaults:

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

Sensitive fields (`bot_token`, `app_secret`, etc.) are masked in the API responses.

## Privacy

The following files contain sensitive or runtime data and are excluded from version control:

- `config.json` — channel credentials and tokens
- `notify.log` — operational log
- `notify_state.json` — cross-process dedup state
- `tray_heartbeat.json` — process heartbeat
- `send_queue/` — transient message queue
- `pending/` / `responses/` — interactive reply lifecycle files
- `dist/` / `build/` — build artifacts

Never commit local tokens or generated runtime state.

## License

MIT
