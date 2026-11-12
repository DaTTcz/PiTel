import json
import os
import subprocess
import sys
import tempfile
import time

from . import paths


def atomic_write(path: str, content: str, mode: int = 0o644) -> bool:
    """Zapíše soubor atomicky. Vrací True, pokud se obsah změnil."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            if f.read() == content:
                os.chmod(path, mode)
                return False
    except FileNotFoundError:
        pass
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return True


def read_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        return default


def write_json(path: str, data, mode: int = 0o644) -> bool:
    return atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", mode)


def append_jsonl(path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def tail_lines(path: str, n: int) -> list:
    """Posledních n řádků souboru (bez načtení celého souboru)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = 8192
            data = b""
            while size > 0 and data.count(b"\n") <= n:
                step = min(block, size)
                size -= step
                f.seek(size)
                data = f.read(step) + data
        lines = data.decode("utf-8", "replace").splitlines()
        return lines[-n:] if n else lines
    except (FileNotFoundError, PermissionError):
        return []


def read_env_file(path: str) -> dict:
    out = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v
    except (FileNotFoundError, PermissionError):
        return {}
    return out


def run(cmd, check=False, timeout=60, dry_ok=True):
    """Spustí příkaz, vrátí (returncode, výstup). V dry-run režimu jen vypíše."""
    if paths.DRY_RUN and dry_ok:
        print("[dry-run] " + " ".join(cmd), file=sys.stderr)
        return 0, ""
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=timeout)
    except FileNotFoundError:
        if check:
            raise
        return 127, f"{cmd[0]}: příkaz nenalezen"
    except subprocess.TimeoutExpired:
        return 124, "časový limit vypršel"
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} selhal ({r.returncode}): {r.stdout.strip()}")
    return r.returncode, r.stdout


def is_root() -> bool:
    return paths.DRY_RUN or os.geteuid() == 0


def require_root():
    if not is_root():
        sys.exit("Tento příkaz je potřeba spustit přes sudo.")


# ---- formátování -----------------------------------------------------------

def fmt_bytes(n) -> str:
    n = float(n or 0)
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def fmt_bps(bytes_per_sec) -> str:
    bits = float(bytes_per_sec or 0) * 8
    if bits < 1000:
        return f"{bits:.0f} b/s"
    return f"{bits / 1000:.1f} kb/s"


def fmt_duration(sec) -> str:
    sec = int(sec or 0)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_time(ts) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
