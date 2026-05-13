# ClaudeBeep

ClaudeBeep is a Windows tray app for Claude Code notifications and approval replies. It keeps the original Python hook workflow intact, but packages it as a desktop application so users can install it once, manage sources from the system tray, and open the full Web UI only when they need detailed configuration.

Initial version: `1.0.0`

## Features

- Windows system tray controls: open dashboard, install/uninstall hooks, quit, check updates, start with Windows, and toggle notification sources.
- Notification sources: Windows Toast, WeChat iLink Bot, QQ Bot, Telegram, Feishu, and DingTalk.
- Disabled tray items for sources that have not been configured yet, so users cannot enable a broken channel accidentally.
- Full original Web UI is still available from the tray.
- Claude Code hook installation still writes to the user-level `~/.claude/settings.json`.
- Interactive replies are enabled by default, so Claude Code questions include numbered options and can be answered from terminal or remote channels.
- Single-instance protection prevents duplicated tray processes and duplicated background polling.
- Automatic cleanup trims logs and stale runtime files while checking whether files are still active.
- WeChat background polling is owned by the tray process when enabled, keeping `context_token` fresh and routing remote approval replies without temporary hook-side polling.
- WeChat `ret=-2` stale-context recovery clears the local token and retries once without `context_token`; `errcode=-14` is treated as a real login expiry.

## Install And Use

Download the latest Windows installer from GitHub Releases, run it, and choose the installation directory. The app stores runtime files such as `config.json`, `notify.log`, `pending/`, and `responses/` in that installation directory.

After launch, use the tray icon:

- `Open Dashboard`: open the full configuration UI.
- `Install All Hooks`: install Claude Code hooks.
- `Uninstall All Hooks`: remove Claude Code hooks.
- `Notification Sources`: enable or disable configured sources.
- `Start with Windows`: toggle per-user startup.
- `Check for Updates`: check the latest GitHub release and run the installer if a newer version is available.

## Development

```powershell
pip install -r requirements.txt
python tray.py
```

The original commands are still supported:

```powershell
python notify.py --ui
python notify.py --install
python notify.py --uninstall
python notify.py --test
```

## Build

```powershell
./build.ps1
```

The script creates `dist/ClaudeBeep.exe`. On GitHub, pushing a tag like `v1.0.0` runs the Windows workflow, builds the executable and Inno Setup installer, then uploads them to the release.

## WeChat Session Notes

The iLink protocol uses long-polling `getupdates`, a long-lived `bot_token`, and a short-lived conversation `context_token`. ClaudeBeep keeps WeChat polling in the tray process when WeChat is enabled, persists the latest `context_token`, updates `to_user_id` from inbound messages, and avoids duplicate hook-side WeChat polling while the tray heartbeat is alive.

If WeChat returns `ret=-2` with an empty or unknown error, ClaudeBeep treats it as a stale `context_token`, clears the cached token, and retries the send without it. If WeChat returns `errcode=-14`, the bot login has expired and the user must scan a new QR code in the dashboard.

## Privacy

Sensitive and runtime files are ignored by Git:

- `config.json`
- `notify.log`
- `notify_state.json`
- `tray_heartbeat.json`
- `pending/`
- `responses/`
- build outputs and installers

Do not commit local tokens or generated runtime state.
