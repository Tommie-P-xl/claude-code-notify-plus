"""Auto-update via GitHub Releases.

Update flow (from Hermes-UI-Control):
1. Try fetching latest.json from release assets for structured metadata
2. Fall back to GitHub API for version info
3. Download the new exe with proper redirect handling
4. Apply update via a batch script that retries on locked files
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


APP_NAME = "ClaudeBeep"
GITHUB_OWNER = "Tommie-P-xl"
GITHUB_REPO = "ClaudeBeep"
UPDATE_CHECK_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
LATEST_JSON_URL = (
    f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
    f"/releases/latest/download/latest.json"
)


def _log(msg: str):
    try:
        _log_file = Path(sys.executable).resolve().parent / "updater.log" if getattr(sys, "frozen", False) else Path(__file__).resolve().parent / "updater.log"
        ts = time.strftime("%H:%M:%S")
        with open(_log_file, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def parse_version(v: str) -> tuple:
    v = v.lstrip("v")
    parts = v.split(".")
    return tuple(int(p) for p in parts[:3])


def _fetch_json(url: str, timeout: int = 15) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        _log(f"Fetch failed for {url}: {e}")
        return None


def _find_exe_asset(release: dict) -> str | None:
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if name.endswith(".exe") and "setup" not in name.lower():
            url = asset.get("browser_download_url", "")
            if url:
                return url
    # Fallback: any .exe
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if name.endswith(".exe"):
            url = asset.get("browser_download_url", "")
            if url:
                return url
    return None


def check_for_update(current_version: str) -> dict | None:
    """Check for a newer release. Returns release info dict or None."""
    # Strategy 1: Try latest.json metadata endpoint
    data = _fetch_json(LATEST_JSON_URL)
    if data and "version" in data:
        remote_ver = parse_version(data["version"])
        local_ver = parse_version(current_version)
        if remote_ver > local_ver:
            exe_url = data.get("url") or data.get("download_url")
            if not exe_url:
                exe_url = _find_exe_asset(data)
            _log(f"Update available via latest.json: {data['version']}")
            return {
                "version": data["version"],
                "url": exe_url,
                "body": data.get("notes", data.get("body", "")),
            }

    # Strategy 2: Fall back to GitHub API
    data = _fetch_json(UPDATE_CHECK_URL)
    if not data:
        return None

    remote_tag = data.get("tag_name", "")
    if not remote_tag:
        return None

    remote_ver = parse_version(remote_tag)
    local_ver = parse_version(current_version)

    if remote_ver > local_ver:
        exe_url = _find_exe_asset(data)
        _log(f"Update available via API: {remote_tag}")
        return {
            "version": remote_tag,
            "url": exe_url,
            "body": data.get("body", ""),
        }

    _log(f"No update available (local={current_version}, remote={remote_tag})")
    return None


def _download_file(url: str, dest: Path, timeout: int = 300) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
        return dest.exists() and dest.stat().st_size > 0
    except Exception as e:
        _log(f"Download failed: {e}")
        return False


def perform_update(download_url: str) -> bool:
    """Download new version and replace current exe via a batch script."""
    if not download_url:
        _log("No download URL provided")
        return False
    if not getattr(sys, "frozen", False):
        _log("Not a packaged exe, cannot self-update")
        return False

    current_exe = Path(sys.executable)
    backup_exe = current_exe.with_suffix(".exe.bak")
    temp_dir = Path(tempfile.mkdtemp(prefix="claudebeep_update_"))
    new_exe = temp_dir / "ClaudeBeep_new.exe"

    try:
        _log(f"Downloading update from: {download_url}")
        if not _download_file(download_url, new_exe):
            _log("Download failed or file is empty")
            return False

        _log(f"Downloaded {new_exe.stat().st_size} bytes")

        if backup_exe.exists():
            try:
                backup_exe.unlink()
            except Exception:
                pass

        pid = os.getpid()

        bat_content = f"""@echo off
chcp 65001 >nul
echo ============================================
echo   ClaudeBeep - Auto Update
echo ============================================
echo.
echo Waiting for application to close...

set /a "count=0"
:wait_loop
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul
if %errorlevel% equ 0 (
    if %count% geq 10 (
        echo Force killing process...
        taskkill /F /PID {pid} >nul 2>&1
    ) else (
        timeout /t 1 /nobreak >nul
        set /a "count+=1"
        goto wait_loop
    )
)

echo.
echo Replacing application...

set /a "retry=0"
:replace_loop
move /Y "{new_exe}" "{current_exe}" >nul 2>&1
if %errorlevel% neq 0 (
    set /a "retry+=1"
    if %retry% geq 5 (
        echo ERROR: Failed to replace application after 5 attempts.
        echo The file may be locked by another process.
        pause
        goto cleanup
    )
    echo Retry %retry%/5...
    timeout /t 1 /nobreak >nul
    goto replace_loop
)

echo Update successful!
echo Starting application...
start "" "{current_exe}"

:cleanup
if exist "{backup_exe}" del /F "{backup_exe}" >nul 2>&1
rd /S /Q "{temp_dir}" >nul 2>&1
del /F "%~f0" >nul 2>&1
"""
        bat_path = temp_dir / "update.bat"
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write(bat_content)

        _log("Launching update script...")
        subprocess.Popen(
            ["cmd", "/c", str(bat_path)],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        )
        return True

    except Exception as e:
        _log(f"Update failed: {type(e).__name__}: {e}")
        for p in [new_exe, backup_exe]:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        try:
            temp_dir.rmdir()
        except Exception:
            pass
        return False
