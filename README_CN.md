# ClaudeBeep

ClaudeBeep 是一个面向 Claude Code 的 Windows 托盘通知应用。它保留原项目的 hook、通知通道和 Web UI，同时把安装、启动、通道开关、更新检查、开机自启等常用操作收进系统托盘。

初始版本：`1.0.0`

## 功能

- 系统托盘菜单：打开主界面、安装/卸载所有 hooks、退出、检查更新、开机自启动、通知源管理。
- 通知源：Windows Toast、微信 iLink Bot、QQ Bot、Telegram、飞书、钉钉。
- 未配置登录信息的通知源会在托盘菜单中置灰，不能误启用。
- 原始 Web UI 仍可从托盘打开，用于详细配置。
- hook 仍安装到用户级 `~/.claude/settings.json`。
- 交互回复默认开启，Claude Code 的询问会展示编号选项，并可从终端或远程渠道回复。
- 多实例防护，避免重复启动托盘进程和后台轮询。
- 定时清理日志与中间文件，清理前会尽量跳过仍在使用的文件。
- 微信启用后由托盘进程统一进行后台 `getupdates` 轮询，持续刷新 `context_token`，并直接接收远程审批回复。
- 微信发送遇到疑似 `context_token` 过期的 `ret=-2` 时，会清空本地 token 并执行一次不带 `context_token` 的降级重试；`errcode=-14` 会视为真正登录过期，需要重新扫码。

## 使用

从 GitHub Releases 下载 Windows 安装包，运行后选择安装目录。`config.json`、`notify.log`、`pending/`、`responses/` 等运行时文件会保存在安装目录中。

托盘菜单说明：

- `Open Dashboard`：打开完整配置界面。
- `Install All Hooks`：安装 Claude Code hooks。
- `Uninstall All Hooks`：卸载 Claude Code hooks。
- `Notification Sources`：启用或关闭已配置的通知源。
- `Start with Windows`：设置是否开机自启。
- `Check for Updates`：检查 GitHub 最新版本，如有新版则运行安装包覆盖更新。

## 开发运行

```powershell
pip install -r requirements.txt
python tray.py
```

原命令仍然保留：

```powershell
python notify.py --ui
python notify.py --install
python notify.py --uninstall
python notify.py --test
```

## 构建

```powershell
./build.ps1
```

脚本会生成 `dist/ClaudeBeep.exe`。推送 `v1.0.0` 这样的 tag 到 GitHub 后，工作流会构建 Windows EXE 和 Inno Setup 安装包，并上传到 Release。
