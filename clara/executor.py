"""Komut yürütme: terminal, dosya, uygulama, peekaboo (macOS), sistem bilgisi."""

from __future__ import annotations

import base64
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from storage import resolve_upload_dir

DANGEROUS_SHELL = re.compile(
    r"(rm\s+-rf|mkfs|dd\s+if=|:?\(\)\s*\{\s*:\|:&\s*\};?:|curl\s+\|?\s*sh|wget\s+\|?\s*sh|>\s*/dev/sd)",
    re.IGNORECASE | re.DOTALL,
)


def _err(
    code: str, message: str, details: str | None = None
) -> dict:
    out: dict = {
        "success": False,
        "error": message,
        "error_code": code,
    }
    if details:
        out["details"] = details
    return out


def _ok(**extra) -> dict:
    return {"success": True, **extra}


def _within_root(path: Path, root: Path) -> bool:
    try:
        path = path.resolve()
        root = root.resolve()
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_path(raw: str | None, file_root: Path) -> Path | dict:
    if not raw:
        return _err("INVALID_PATH", "path gerekli", "params.path eksik")
    expanded = Path(raw).expanduser()
    try:
        resolved = expanded.resolve()
    except OSError as e:
        return _err("INVALID_PATH", "Geçersiz yol", str(e))
    if not _within_root(resolved, file_root):
        return _err(
            "PATH_OUTSIDE_ROOT",
            "Yol izin verilen kök dışında",
            f"İzinli kök: {file_root}",
        )
    return resolved


def _run_terminal(params: dict | None, timeout_default: int = 30) -> dict:
    if not params:
        return _err("INVALID_PARAMS", "params gerekli")
    cmd = params.get("cmd")
    if not cmd or not isinstance(cmd, str):
        return _err("INVALID_PARAMS", "params.cmd string olmalı")
    if DANGEROUS_SHELL.search(cmd):
        return _err(
            "FORBIDDEN_COMMAND",
            "Komut güvenlik nedeniyle engellendi",
            "Tehlikeli desen algılandı.",
        )
    timeout = int(params.get("timeout", timeout_default))
    timeout = max(1, min(timeout, 300))
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(Path.home()),
        )
        elapsed = round(time.perf_counter() - t0, 3)
        return _ok(
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            exit_code=proc.returncode,
            execution_time=elapsed,
        )
    except subprocess.TimeoutExpired:
        return _err("TIMEOUT", "Komut zaman aşımına uğradı", f"timeout={timeout}s")
    except Exception as e:
        return _err("EXEC_ERROR", "Komut çalıştırılamadı", str(e))


def _file_read(params: dict | None, file_root: Path) -> dict:
    if not params:
        return _err("INVALID_PARAMS", "params gerekli")
    p = _safe_path(params.get("path"), file_root)
    if isinstance(p, dict):
        return p
    if not p.is_file():
        return _err("NOT_FOUND", "Dosya bulunamadı", str(p))
    try:
        max_bytes = int(params.get("max_bytes", 2_000_000))
        max_bytes = max(1, min(max_bytes, 10_000_000))
        data = p.read_bytes()[:max_bytes]
        text = data.decode("utf-8", errors="replace")
        return _ok(content=text, path=str(p), truncated=len(data) >= max_bytes)
    except OSError as e:
        return _err("READ_ERROR", "Okunamadı", str(e))


def _file_write(params: dict | None, file_root: Path) -> dict:
    if not params:
        return _err("INVALID_PARAMS", "params gerekli")
    p = _safe_path(params.get("path"), file_root)
    if isinstance(p, dict):
        return p
    content = params.get("content", "")
    if not isinstance(content, str):
        return _err("INVALID_PARAMS", "content string olmalı")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return _ok(path=str(p), bytes_written=len(content.encode("utf-8")))
    except OSError as e:
        return _err("WRITE_ERROR", "Yazılamadı", str(e))


def _file_list(params: dict | None, file_root: Path) -> dict:
    if not params:
        return _err("INVALID_PARAMS", "params gerekli")
    p = _safe_path(params.get("path"), file_root)
    if isinstance(p, dict):
        return p
    if not p.is_dir():
        return _err("NOT_FOUND", "Klasör bulunamadı", str(p))
    try:
        entries = sorted(p.iterdir(), key=lambda x: x.name.lower())
        items = [{"name": e.name, "is_dir": e.is_dir()} for e in entries[:500]]
        return _ok(path=str(p), items=items, count=len(items))
    except OSError as e:
        return _err("LIST_ERROR", "Listelenemedi", str(e))


def _app_launch(params: dict | None) -> dict:
    if not params:
        return _err("INVALID_PARAMS", "params gerekli")
    name = params.get("app") or params.get("name")
    if not name or not isinstance(name, str):
        return _err("INVALID_PARAMS", "params.app veya params.name gerekli")
    system = platform.system()
    t0 = time.perf_counter()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", "-a", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif system == "Windows":
            os.startfile(name)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elapsed = round(time.perf_counter() - t0, 3)
        return _ok(launched=name, execution_time=elapsed)
    except Exception as e:
        return _err("LAUNCH_ERROR", "Başlatılamadı", str(e))


def _app_kill(params: dict | None) -> dict:
    if not params:
        return _err("INVALID_PARAMS", "params gerekli")
    name = params.get("app") or params.get("name")
    if not name or not isinstance(name, str):
        return _err("INVALID_PARAMS", "params.app veya params.name gerekli")
    t0 = time.perf_counter()
    try:
        if platform.system() == "Darwin":
            subprocess.run(["killall", name], capture_output=True, text=True, timeout=30)
        else:
            subprocess.run(["pkill", "-f", name], capture_output=True, text=True, timeout=30)
        elapsed = round(time.perf_counter() - t0, 3)
        return _ok(killed=name, execution_time=elapsed)
    except Exception as e:
        return _err("KILL_ERROR", "Durdurulamadı", str(e))


def _peekaboo_dir() -> Path:
    base = resolve_upload_dir() / "peekaboo"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _peekaboo_screenshot(_params: dict | None) -> dict:
    out_dir = _peekaboo_dir()
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"screen_{ts}.png"
    t0 = time.perf_counter()
    try:
        if platform.system() == "Darwin":
            r = subprocess.run(
                ["screencapture", "-x", str(path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode != 0:
                return _err("SCREENSHOT_FAILED", "screencapture başarısız", r.stderr)
        else:
            if shutil.which("gnome-screenshot"):
                subprocess.run(
                    ["gnome-screenshot", "-f", str(path)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            elif shutil.which("import"):
                subprocess.run(
                    ["import", "-window", "root", str(path)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            else:
                return _err(
                    "UNSUPPORTED_PLATFORM",
                    "Ekran görüntüsü bu ortamda desteklenmiyor",
                    "macOS: screencapture; Linux: gnome-screenshot veya ImageMagick import gerekir.",
                )
        if not path.exists():
            return _err("SCREENSHOT_FAILED", "Dosya oluşmadı", str(path))
        elapsed = round(time.perf_counter() - t0, 3)
        rel = f"peekaboo/{path.name}"
        return _ok(
            path=str(path),
            url=f"/files/{rel}",
            execution_time=elapsed,
        )
    except Exception as e:
        return _err("SCREENSHOT_ERROR", str(e), type(e).__name__)


def _peekaboo_click(params: dict | None) -> dict:
    if not params:
        return _err("INVALID_PARAMS", "params gerekli")
    x = params.get("x")
    y = params.get("y")
    try:
        x, y = int(x), int(y)
    except (TypeError, ValueError):
        return _err("INVALID_PARAMS", "x ve y sayı olmalı")
    t0 = time.perf_counter()
    try:
        if platform.system() == "Darwin":
            script = f'tell application "System Events" to click at {{{x}, {y}}}'
            r = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=30,
            )
            elapsed = round(time.perf_counter() - t0, 3)
            if r.returncode != 0:
                return _err("CLICK_FAILED", r.stderr or "osascript hatası", r.stdout)
            return _ok(x=x, y=y, execution_time=elapsed)
        return _err(
            "UNSUPPORTED_PLATFORM",
            "Tıklama yalnızca macOS'ta destekleniyor",
            "Linux için ayrı araç gerekir.",
        )
    except Exception as e:
        return _err("CLICK_ERROR", str(e), type(e).__name__)


def _peekaboo_type(params: dict | None) -> dict:
    if not params:
        return _err("INVALID_PARAMS", "params gerekli")
    text = params.get("text", "")
    if not isinstance(text, str):
        return _err("INVALID_PARAMS", "text string olmalı")
    t0 = time.perf_counter()
    try:
        if platform.system() == "Darwin":
            safe = text.replace("\\", "\\\\").replace('"', '\\"')
            script = f'tell application "System Events" to keystroke "{safe}"'
            r = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=60,
            )
            elapsed = round(time.perf_counter() - t0, 3)
            if r.returncode != 0:
                return _err("TYPE_FAILED", r.stderr or "osascript hatası", r.stdout)
            return _ok(typed_len=len(text), execution_time=elapsed)
        return _err(
            "UNSUPPORTED_PLATFORM",
            "Klavye yazımı yalnızca macOS'ta destekleniyor",
            None,
        )
    except Exception as e:
        return _err("TYPE_ERROR", str(e), type(e).__name__)


def _system_info(_params: dict | None) -> dict:
    return _ok(
        platform=platform.platform(),
        system=platform.system(),
        release=platform.release(),
        machine=platform.machine(),
        python=sys.version,
    )


def execute(
    command: str,
    action: str,
    params: dict | None,
    *,
    file_root: Path,
) -> dict:
    key = f"{command.strip().lower()}.{action.strip().lower()}"
    if key == "terminal.exec":
        return _run_terminal(params)
    if key == "file.read":
        return _file_read(params, file_root)
    if key == "file.write":
        return _file_write(params, file_root)
    if key == "file.list":
        return _file_list(params, file_root)
    if key == "app.launch":
        return _app_launch(params)
    if key == "app.kill":
        return _app_kill(params)
    if key == "peekaboo.screenshot":
        return _peekaboo_screenshot(params)
    if key == "peekaboo.click":
        return _peekaboo_click(params)
    if key == "peekaboo.type":
        return _peekaboo_type(params)
    if key == "system.info":
        return _system_info(params)
    return _err("UNKNOWN_COMMAND", f"Bilinmeyen komut: {key}")
