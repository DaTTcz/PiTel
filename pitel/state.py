"""Čtení stavu: probíhající hovor, čítače provozu, služby, historie."""
import json
import os
import re
import time

from . import paths
from .util import read_env_file, read_json, run, tail_lines

# návratové kódy pppd (man pppd, EXIT STATUS)
PPPD_EXIT = {
    0: "OK – spojení ukončeno",
    1: "Fatální chyba pppd",
    2: "Chyba v konfiguraci pppd",
    3: "pppd nemá práva roota",
    4: "Jádro nepodporuje PPP",
    5: "Ukončeno signálem (zavěšeno ze serveru)",
    6: "Nelze zamknout sériový port",
    7: "Nelze otevřít sériový port",
    10: "Vyjednávání PPP selhalo",
    11: "Klient se neověřil (špatné jméno/heslo)",
    12: "Odpojeno pro nečinnost",
    13: "Vypršel časový limit spojení",
    15: "Klient přestal odpovídat",
    16: "Modem zavěsil (ztráta nosné)",
    17: "Detekována smyčka na lince",
    19: "Ověření vůči klientovi selhalo",
}

_RE_CONN = re.compile(r"conn='([^']*)'")


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, ValueError, TypeError):
        return False


def _call_env() -> dict:
    """call.env zapisuje ppp-wrapper. Pokud wrapper už neběží (např. byl zabit),
    je soubor zastaralý a ignorujeme ho."""
    call = read_env_file(paths.CALL_ENV)
    if call and call.get("PID") and not paths.DRY_RUN and not _pid_alive(call["PID"]):
        for f in (paths.CALL_ENV, paths.SESSION):
            try:
                os.unlink(f)
            except (FileNotFoundError, PermissionError):
                pass
        return {}
    return call


def call_active() -> bool:
    return bool(_call_env())


def iface_counters(iface: str) -> dict:
    if not iface or not re.match(r"^[A-Za-z0-9._-]+$", iface):
        return None
    base = os.path.join(paths.SYS_NET, iface, "statistics")
    try:
        with open(os.path.join(base, "rx_bytes")) as f:
            rx = int(f.read().strip())
        with open(os.path.join(base, "tx_bytes")) as f:
            tx = int(f.read().strip())
    except (FileNotFoundError, ValueError, PermissionError):
        return None
    return {"rx": rx, "tx": tx, "ts": time.time()}


def mgetty_log_path(device: str) -> str:
    return os.path.join(paths.MGETTY_LOG_DIR, f"mg_{device}.log")


def last_connect_from_log(device: str) -> str:
    """Poslední CONNECT řetězec z logu mgetty (záloha, když chybí v call.env)."""
    for line in reversed(tail_lines(mgetty_log_path(device), 400)):
        m = _RE_CONN.search(line)
        if m:
            return m.group(1)
        if "CONNECT" in line and "expect" not in line:
            idx = line.find("CONNECT")
            return line[idx + 7:].strip(" '\"") or None
    return None


def current(cfg: dict) -> dict:
    call = _call_env()
    sess = read_json(paths.SESSION) or {}
    if not call and not sess:
        return {"phase": "idle"}
    connect = (call.get("CONNECT") or "").strip() or last_connect_from_log(cfg["device"])
    started = int(call.get("STARTED") or sess.get("started") or time.time())
    out = {
        "phase": "connected" if sess else "negotiating",
        "connect": connect,
        "caller": (call.get("CALLER_ID") or "").strip() or None,
        "started": started,
        "duration": int(time.time()) - started,
        "user": sess.get("peer"),
        "iface": sess.get("iface"),
        "remote_ip": sess.get("remote"),
        "local_ip": sess.get("local"),
    }
    if sess:
        out["ppp_started"] = sess.get("started")
        out["counters"] = iface_counters(sess.get("iface"))
    return out


def service_states() -> dict:
    out = {}
    for unit in paths.UNITS:
        if paths.DRY_RUN:
            out[unit] = "active"
            continue
        rc, txt = run(["systemctl", "is-active", unit], dry_ok=False)
        out[unit] = (txt.strip() or "unknown")
    return out


def modem_present(cfg: dict) -> bool:
    return os.path.exists(os.path.join(paths.DEV, cfg["device"]))


def calls(n: int = 50) -> list:
    out = []
    for line in reversed(tail_lines(paths.CALLS, n)):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def totals() -> dict:
    """Souhrn za dnešek a celkově (čte celý soubor – je malý)."""
    today = time.strftime("%Y-%m-%d")
    t = {"calls": 0, "ok": 0, "today": 0, "bytes_sent": 0, "bytes_rcvd": 0, "seconds": 0}
    try:
        with open(paths.CALLS, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t["calls"] += 1
                if r.get("connected"):
                    t["ok"] += 1
                if time.strftime("%Y-%m-%d", time.localtime(r.get("started", 0))) == today:
                    t["today"] += 1
                t["bytes_sent"] += int(r.get("bytes_sent") or 0)
                t["bytes_rcvd"] += int(r.get("bytes_rcvd") or 0)
                t["seconds"] += int(r.get("duration") or 0)
    except FileNotFoundError:
        pass
    return t


def host_ip(iface: str) -> str:
    if paths.DRY_RUN:
        return "127.0.0.1"
    rc, out = run(["ip", "-4", "-o", "addr", "show", "dev", iface], dry_ok=False)
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out or "")
    return m.group(1) if m else None


def summary(cfg: dict) -> dict:
    from . import __version__
    hist = calls(1)
    return {
        "version": __version__,
        "time": int(time.time()),
        "device": cfg["device"],
        "modem_present": modem_present(cfg),
        "services": service_states(),
        "current": current(cfg),
        "last_call": hist[0] if hist else None,
        "totals": totals(),
        "max_speed": cfg["max_speed"],
        "line_type": cfg["line_type"],
        "host_ip": host_ip(cfg["wan_iface"]),
        "web_port": cfg["web_port"],
    }


def logs(cfg: dict, n: int = 200) -> str:
    parts = []
    if paths.DRY_RUN:
        parts.append("[dry-run] journalctl se v testovacím režimu nevolá")
    else:
        rc, out = run(["journalctl", "--no-pager", "-o", "short-iso", "-n", str(n),
                       "-u", paths.UNIT_MGETTY, "-u", paths.UNIT_NAT, "-u", paths.UNIT_WEB],
                      dry_ok=False)
        parts.append("===== journalctl (mgetty, pppd, nat, web) =====\n" + (out or ""))
    mlog = tail_lines(mgetty_log_path(cfg["device"]), n)
    parts.append(f"===== {mgetty_log_path(cfg['device'])} =====\n" +
                 ("\n".join(mlog) if mlog else "(prázdný nebo neexistuje)"))
    return "\n\n".join(parts)


def pppd_pid(iface: str = None):
    for name in ([f"{iface}.pid"] if iface else []) + ["ppp0.pid"]:
        for d in ("/run", "/var/run"):
            try:
                with open(paths.p(os.path.join(d, name))) as f:
                    return int(f.readline().strip())
            except (FileNotFoundError, ValueError):
                continue
    return None


def hangup(cfg: dict) -> str:
    sess = read_json(paths.SESSION) or {}
    pid = pppd_pid(sess.get("iface"))
    if pid:
        if paths.DRY_RUN:
            return f"[dry-run] kill {pid}"
        try:
            os.kill(pid, 15)
            return "Hovor se ukončuje."
        except ProcessLookupError:
            pass
    if call_active():
        # pppd ještě neběží (vyjednávání) – restart mgetty linku shodí
        run(["systemctl", "restart", paths.UNIT_MGETTY])
        return "Linka byla resetována restartem mgetty."
    return "Žádný hovor neprobíhá."
