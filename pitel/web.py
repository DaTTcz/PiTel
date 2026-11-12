"""Webová správa – jen standardní knihovna Pythonu (http.server)."""
import json
import os
import secrets
import shlex
import shutil
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, auth, config, modem, paths, render, state, update, users
from .util import run, tail_lines

WEB_DIR = os.path.join(paths.APP_DIR, "web")
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
SESSION_TTL = 12 * 3600
COOKIE = "pitel_sid"

_sessions = {}
_lock = threading.Lock()
_modem_lock = threading.Lock()


def _new_session() -> str:
    sid = secrets.token_urlsafe(32)
    with _lock:
        now = time.time()
        for k in [k for k, v in _sessions.items() if v < now]:
            del _sessions[k]
        _sessions[sid] = now + SESSION_TTL
    return sid


def _valid(sid) -> bool:
    with _lock:
        exp = _sessions.get(sid)
        if exp and exp > time.time():
            _sessions[sid] = time.time() + SESSION_TTL
            return True
    return False


def _update_running() -> bool:
    if paths.DRY_RUN:
        return False
    rc, out = run(["systemctl", "is-active", "pitel-update.service"], dry_ok=False)
    return out.strip() in ("active", "activating")


class Handler(BaseHTTPRequestHandler):
    server_version = f"PiTel/{__version__}"

    def log_message(self, fmt, *args):  # tišší log do journalu
        if not self.path.startswith("/api/status"):
            super().log_message(fmt, *args)

    # ---- pomocné -------------------------------------------------------
    def _cookie(self):
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                if k == COOKIE:
                    return v
        return None

    def _send(self, code, body: bytes, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code=200, extra=None):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", extra)

    def _err(self, msg, code=400):
        self._json({"error": msg}, code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 64 * 1024:
            raise ValueError("Požadavek je příliš velký.")
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def _authed(self):
        return _valid(self._cookie())

    # ---- routování -----------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in STATIC:
            name, ctype = STATIC[url.path]
            try:
                with open(os.path.join(WEB_DIR, name), "rb") as f:
                    return self._send(200, f.read(), ctype)
            except FileNotFoundError:
                return self._err("Soubor nenalezen", 404)
        if not url.path.startswith("/api/"):
            return self._err("Nenalezeno", 404)
        self._api("GET", url)

    def do_POST(self):
        self._api("POST", urlparse(self.path))

    def do_DELETE(self):
        self._api("DELETE", urlparse(self.path))

    def _api(self, method, url):
        path = url.path
        qs = parse_qs(url.query)
        try:
            if method != "GET":
                # ochrana proti CSRF: vyžadujeme vlastní hlavičku (posílá ji jen naše JS)
                if self.headers.get("X-PiTel") != "1":
                    return self._err("Chybí hlavička X-PiTel.", 403)

            if path == "/api/session" and method == "GET":
                return self._json({"authenticated": self._authed(),
                                   "has_password": auth.has_password()})
            if path == "/api/login" and method == "POST":
                body = self._body()
                if auth.check_password(str(body.get("password", ""))):
                    sid = _new_session()
                    return self._json({"ok": True}, extra={
                        "Set-Cookie": f"{COOKIE}={sid}; Path=/; HttpOnly; SameSite=Strict; "
                                      f"Max-Age={SESSION_TTL}"})
                time.sleep(1.5)
                return self._err("Špatné heslo.", 401)

            if not self._authed():
                return self._err("Nepřihlášen.", 401)

            if path == "/api/logout" and method == "POST":
                with _lock:
                    _sessions.pop(self._cookie(), None)
                return self._json({"ok": True}, extra={
                    "Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0"})

            cfg = config.load()

            if path == "/api/status":
                s = state.summary(cfg)
                s["update"] = update.cached(cfg)
                return self._json(s)
            if path == "/api/calls":
                n = min(int((qs.get("n") or ["100"])[0]), 1000)
                return self._json({"calls": state.calls(n), "totals": state.totals()})

            if path == "/api/users":
                if method == "GET":
                    return self._json({"users": users.list_users()})
                if method == "POST":
                    b = self._body()
                    new = users.set_user(str(b.get("name", "")).strip(), str(b.get("password", "")))
                    return self._json({"ok": True, "message": "Uživatel přidán." if new else "Heslo změněno."})
            if path.startswith("/api/users/") and method == "DELETE":
                users.delete_user(path.rsplit("/", 1)[1])
                return self._json({"ok": True, "message": "Uživatel smazán."})

            if path == "/api/config":
                if method == "GET":
                    return self._json({
                        "config": cfg,
                        "line_types": {k: v[0] for k, v in config.LINE_TYPES.items()},
                        "line_defaults": {k: v[1] for k, v in config.LINE_TYPES.items()},
                        "speeds": sorted(config.SPEEDS),
                        "init_preview": config.init_commands(cfg),
                    })
                if method == "POST":
                    b = self._body()
                    new = dict(cfg)
                    for k in config.DEFAULTS:
                        if k in b:
                            new[k] = b[k]
                    config.validate(new)
                    restart_web = (new["web_port"], new["web_bind"]) != (cfg["web_port"], cfg["web_bind"])
                    config.save(new)
                    msgs = render.apply(new, restart_web=restart_web)
                    return self._json({"ok": True, "messages": msgs,
                                       "init_preview": config.init_commands(new)})

            if path == "/api/action" and method == "POST":
                act = self._body().get("action")
                if act == "restart-mgetty":
                    run(["systemctl", "restart", paths.UNIT_MGETTY])
                    return self._json({"ok": True, "message": "mgetty restartováno."})
                if act == "hangup":
                    return self._json({"ok": True, "message": state.hangup(cfg)})
                if act == "apply":
                    return self._json({"ok": True, "messages": render.apply(cfg)})
                return self._err("Neznámá akce.")

            if path == "/api/modem" and method == "POST":
                cmds = self._body().get("commands") or None
                if isinstance(cmds, str):
                    cmds = [x for x in cmds.replace(";", "\n").splitlines() if x.strip()]
                if not _modem_lock.acquire(blocking=False):
                    return self._err("Dotaz na modem už běží.", 409)
                try:
                    return self._json({"output": modem.query(cfg, cmds)})
                finally:
                    _modem_lock.release()

            if path == "/api/logs":
                n = min(int((qs.get("n") or ["200"])[0]), 2000)
                return self._json({"text": state.logs(cfg, n)})

            if path == "/api/update":
                if method == "GET":
                    force = (qs.get("check") or ["0"])[0] == "1"
                    info = update.check(cfg, force=force)
                    info.update({
                        "running": _update_running(),
                        "log": "\n".join(tail_lines(paths.UPDATE_LOG, 300)),
                        "repo_url": cfg.get("repo_url"),
                        "channel": cfg.get("update_channel"),
                        "branch": cfg.get("branch"),
                    })
                    return self._json(info)
                if method == "POST":
                    if _update_running():
                        return self._err("Aktualizace už běží.", 409)
                    os.makedirs(paths.VAR, exist_ok=True)
                    script = os.path.join(paths.APP_DIR, "install.sh")
                    # běží mimo cgroup webu, takže restart webu ji nezabije
                    run(["systemd-run", "--quiet", "--collect", "--unit=pitel-update",
                         "/bin/sh", "-c",
                         f"/bin/bash {shlex.quote(script)} --update > {shlex.quote(paths.UPDATE_LOG)} 2>&1"])
                    return self._json({"ok": True, "message": "Aktualizace spuštěna."})

            if path == "/api/admin-password" and method == "POST":
                b = self._body()
                if not auth.check_password(str(b.get("old", ""))):
                    return self._err("Současné heslo nesouhlasí.", 403)
                auth.set_password(str(b.get("new", "")))
                return self._json({"ok": True, "message": "Heslo změněno."})

            return self._err("Neznámý požadavek.", 404)
        except (config.ConfigError, users.UserError, modem.ModemError, ValueError) as e:
            return self._err(str(e), 400)
        except Exception as e:  # noqa: BLE001
            self.log_error("chyba: %r", e)
            return self._err(f"Vnitřní chyba: {e}", 500)


def serve():
    cfg = config.load()
    addr = (cfg.get("web_bind") or "0.0.0.0", int(cfg.get("web_port") or 80))
    httpd = ThreadingHTTPServer(addr, Handler)
    httpd.daemon_threads = True
    update.background_checker(config.load)
    print(f"PiTel web běží na http://{addr[0]}:{addr[1]}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
