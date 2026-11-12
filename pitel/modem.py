"""Přímá komunikace s modemem (AT příkazy).

Modem drží mgetty, proto ho na dobu dotazu zastavíme a pak zase spustíme.
Během hovoru to nejde – port patří pppd.
"""
import os
import select
import termios
import time
import tty

from . import paths, state
from .util import run

DEFAULT_COMMANDS = ["ATI0", "ATI1", "ATI3", "ATI4", "AT&V"]

_FINAL = ("OK", "ERROR", "NO CARRIER", "NO DIALTONE", "BUSY", "NO ANSWER")


class ModemError(RuntimeError):
    pass


def _open(device: str) -> int:
    path = os.path.join(paths.DEV, device)
    try:
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError as e:
        raise ModemError(f"Nelze otevřít {path}: {e.strerror}")
    try:
        tty.setraw(fd)
        attrs = termios.tcgetattr(fd)
        attrs[2] |= termios.CLOCAL | termios.CREAD | getattr(termios, "CRTSCTS", 0)
        attrs[4] = attrs[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)
    except termios.error:
        pass  # např. v testovacím režimu, kde zařízení není skutečný tty
    return fd


def _command(fd: int, cmd: str, timeout: float = 4.0) -> str:
    os.write(fd, (cmd + "\r").encode("ascii"))
    buf = b""
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                chunk = os.read(fd, 1024)
            except BlockingIOError:
                continue
            if not chunk:
                break
            buf += chunk
            lines = [l.strip() for l in buf.decode("latin-1").splitlines() if l.strip()]
            if lines and lines[-1] in _FINAL:
                break
    text = buf.decode("latin-1").replace("\r", "")
    # odstraníme echo příkazu
    lines = [l for l in text.split("\n") if l.strip() and l.strip() != cmd]
    return "\n".join(lines) if lines else "(žádná odpověď)"


def query(cfg: dict, commands=None) -> str:
    commands = [c.strip() for c in (commands or DEFAULT_COMMANDS) if c.strip()]
    for c in commands:
        if not c.upper().startswith("AT") or len(c) > 80:
            raise ModemError(f"'{c}' není AT příkaz.")
        if c.upper().startswith(("ATD", "ATA")):
            raise ModemError("Vytáčení a zvedání (ATD/ATA) tady není povolené.")
    if state.call_active():
        raise ModemError("Právě probíhá hovor – modem je obsazený pppd.")

    run(["systemctl", "stop", paths.UNIT_MGETTY])
    time.sleep(0.5)
    out = []
    try:
        fd = _open(cfg["device"])
        try:
            _command(fd, "AT", 1.5)  # synchronizace
            for c in commands:
                out.append(f"### {c}\n{_command(fd, c)}")
        finally:
            os.close(fd)
    finally:
        run(["systemctl", "start", paths.UNIT_MGETTY])
    return "\n\n".join(out)
