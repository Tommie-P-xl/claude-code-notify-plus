# Multi-Channel Expansion: Feishu, DingTalk, Telegram

> Date: 2026-05-02
> Status: Approved
> Approach: Minimal changes — follow existing channel pattern

## Goal

Add three new notification + interaction channels (Feishu, DingTalk, Telegram) to the existing claude-code-notify system. All three support bidirectional interaction (send notifications + receive user replies). No public IP required.

## Constraints

- Keep existing architecture unchanged (no plugin system, no registry)
- Follow the current `NotificationChannel` base class pattern
- Extend `weixin_keepalive.py` with new listener threads (like QQ WebSocket)
- Full Web UI config pages for each channel
- New dependencies: `lark-oapi` (Feishu), `dingtalk-stream` (DingTalk), none for Telegram

## Channel Specifications

### Feishu (飞书)

- **Connection**: WebSocket long connection via Feishu Open Platform
- **Credentials**: `app_id` + `app_secret`
- **Config key**: `feishu`
- **SDK**: `lark-oapi` (official Python SDK with WebSocket support)
- **Setup**: Create enterprise app at open.feishu.cn, enable bot capability

### DingTalk (钉钉)

- **Connection**: Stream (WebSocket-based) via DingTalk Open Platform
- **Credentials**: `app_key` + `app_secret`
- **Config key**: `dingtalk`
- **SDK**: `dingtalk-stream` (official Stream SDK)
- **Setup**: Create app at open.dingtalk.com, add robot capability

### Telegram

- **Connection**: HTTP long polling via Telegram Bot API
- **Credentials**: `bot_token`
- **Config key**: `telegram`
- **SDK**: None (urllib, same as WeChat)
- **Setup**: Create bot via @BotFather, get bot token

## File Changes

### New Files

| File | Purpose |
|------|---------|
| `channels/feishu.py` | FeishuChannel: send() + WebSocket message listener + credential validation |
| `channels/dingtalk.py` | DingTalkChannel: send() + Stream message listener + credential validation |
| `channels/telegram.py` | TelegramChannel: send() + long polling message listener + credential validation |

### Modified Files

| File | Changes |
|------|---------|
| `channels/__init__.py` | Add imports and exports for three new channel classes |
| `weixin_keepalive.py` | Add `feishu_websocket_loop()`, `dingtalk_stream_loop()`, `telegram_poll_loop()` as daemon threads |
| `notify.py` | Add imports, `collect_channels()`, `DEFAULT_CONFIG`, keepalive startup condition |
| `app.py` | Add API routes (validate/status/logout per channel), toggle validation, `DEFAULT_CONFIG` |
| `static/index.html` | Add three tab pages + dashboard toggle cards |
| `requirements.txt` | Add `lark-oapi>=1.0.0`, `dingtalk-stream>=1.0.0` |
| `config.json` | Runtime: add three new config sections |

## Config Structure

```json
{
  "feishu": {
    "enabled": false,
    "app_id": "",
    "app_secret": ""
  },
  "dingtalk": {
    "enabled": false,
    "app_key": "",
    "app_secret": ""
  },
  "telegram": {
    "enabled": false,
    "bot_token": ""
  }
}
```

## Keepalive Daemon Architecture

```
weixin_keepalive.py
├── weixin_keepalive_loop()     # Existing: WeChat long polling
├── qq_websocket_loop()         # Existing: QQ WebSocket listener
├── feishu_websocket_loop()     # New: Feishu WebSocket listener
├── dingtalk_stream_loop()      # New: DingTalk Stream listener
├── telegram_poll_loop()        # New: Telegram long polling
└── main()
    ├── Start QQ thread (existing)
    ├── Start Feishu thread (new)
    ├── Start DingTalk thread (new)
    ├── Start Telegram thread (new)
    └── Main thread runs WeChat keepalive (existing)
```

Each thread runs independently. Message reply processing reuses existing `_process_incoming_message()`.

**Thread isolation**: Each listener thread is a daemon thread. If one channel's listener crashes, it logs the error and does not affect other channels. The main thread (WeChat keepalive) continues running regardless.

**Target ID auto-discovery**: Like QQ's `user_openid` auto-discovery via WebSocket events:
- **Feishu**: Auto-discover `open_id` from incoming message events, save to config
- **DingTalk**: Auto-discover `staff_id` or `conversation_id` from Stream events, save to config
- **Telegram**: Auto-discover `chat_id` from incoming message updates, save to config

Each channel saves its target to `config.json` on first received message, same pattern as QQ.

Keepalive startup condition in `notify.py` expands from `wx or qq` to `wx or qq or feishu or dingtalk or telegram`.

## Message Flow (All Channels)

```
Channel receives message
  → _extract_reply_parts(text)     # Extract label and option
  → _process_incoming_message()    # Match pending request, write response
  → Channel sends confirmation     # "Reply received"
```

Identical to existing QQ/WeChat flow, only the underlying connection differs.

## Web UI

Three new tabs added (same structure as existing QQ Bot tab):

- **Feishu**: App ID / App Secret inputs, validate button, connection status, toggle
- **DingTalk**: App Key / App Secret inputs, validate button, connection status, toggle
- **Telegram**: Bot Token input, validate button, connection status, toggle

Dashboard channel cards expanded to include three new channels.

## API Routes (app.py)

| Route | Channel | Function |
|-------|---------|----------|
| `POST /api/feishu/validate` | Feishu | Validate and save credentials |
| `GET /api/feishu/status` | Feishu | Get connection status |
| `POST /api/feishu/logout` | Feishu | Clear credentials |
| `POST /api/dingtalk/validate` | DingTalk | Validate and save credentials |
| `GET /api/dingtalk/status` | DingTalk | Get connection status |
| `POST /api/dingtalk/logout` | DingTalk | Clear credentials |
| `POST /api/telegram/validate` | Telegram | Validate bot token |
| `GET /api/telegram/status` | Telegram | Get connection status |
| `POST /api/telegram/logout` | Telegram | Clear credentials |

## What Does NOT Change

- `interaction.py` — interaction core is channel-agnostic, no changes needed
- `channels/base.py` — base class stays the same
- `notify_state.py` — dedup logic unchanged
- Existing WeChat/QQ behavior — fully preserved
