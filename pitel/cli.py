"""Příkaz `pitel` – stav, správa a průvodce nastavením."""
import argparse
import getpass
import json
import os
import re
import sys
import time

from . import __version__, auth, config, modem, paths, render, state, users
from .util import fmt_bps, fmt_bytes, fmt_duration, fmt_time, require_root, run

# ---- barvy -----------------------------------------------------------------
_TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(text, code):
    return f"\033[{code}m{text}\033[0m" if _TTY else str(text)


def ok(b):
    return c("✔", "32") if b else c("✘", "31")


PHASE = {
    "idle": ("čeká na hovor", "36"),
    "negotiating": ("VYJEDNÁVÁNÍ", "33;1"),
    "connected": ("PŘIPOJENO", "32;1"),
}


# ---- status ----------------------------------------------------------------

def _print_status(s: dict, rates=None, short=False):
    cur = s["current"]
    label, col = PHASE.get(cur["phase"], (cur["phase"], "0"))
    if short:
        line = f"PiTel: linka {c(label, col)}"
        if cur["phase"] != "idle":
            line += f"  {cur.get('connect') or ''}  {fmt_duration(cur.get('duration'))}"
            if cur.get("user"):
                line += f"  uživatel {cur['user']}"
        if not s["modem_present"]:
            line += c(f"  (modem /dev/{s['device']} nenalezen!)", "31")
        bad = [u for u, v in s["services"].items() if v != "active"]
        if bad:
            line += c("  neběží: " + ", ".join(b.replace('.service', '') for b in bad), "31")
        url = f"http://{s['host_ip']}" + ("" if s["web_port"] == 80 else f":{s['web_port']}")
        from . import update
        u = update.cached(config.load())
        if u["available"]:
            line += c(f"  ⬆ nová verze {u['latest']}", "33")
        print(line + c(f"   správa: {url}/", "2"))
        return

    sv = s["services"]
    from . import update
    u = update.cached(config.load())
    print(c(f"PiTel {u['current']}", "1") +
          (c(f"   ⬆ dostupná {u['latest']} (sudo pitel update)", "33") if u["available"] else ""))
    print(f"  Modem      /dev/{s['device']}  {ok(s['modem_present'])} "
          f"{'připojen' if s['modem_present'] else 'NENALEZEN'}"
          f"   max. rychlost: {s['max_speed'] or 'bez omezení'}")
    print("  Služby     " + "  ".join(
        f"{n} {ok(sv.get(u) == 'active')}" for n, u in
        (("mgetty", paths.UNIT_MGETTY), ("NAT", paths.UNIT_NAT), ("web", paths.UNIT_WEB))))
    print(f"  Linka      {c('● ' + label, col)}  {cur.get('connect') or ''}")
    if cur["phase"] != "idle":
        print(f"  Hovor      od {fmt_time(cur['started'])}, trvá {fmt_duration(cur['duration'])}"
              + (f", volá {cur['caller']}" if cur.get("caller") else ""))
    if cur["phase"] == "connected":
        print(f"  Uživatel   {cur.get('user') or '?'}  ({cur.get('remote_ip')} přes {cur.get('iface')})")
        cnt = cur.get("counters") or {}
        print(f"  Přeneseno  ↓ ke klientovi {fmt_bytes(cnt.get('tx'))}   "
              f"↑ od klienta {fmt_bytes(cnt.get('rx'))}")
        if rates:
            print(f"  Rychlost   ↓ {fmt_bps(rates[1])}   ↑ {fmt_bps(rates[0])}")
    lc = s.get("last_call")
    if lc:
        print(f"  Poslední   {fmt_time(lc['started'])}  {fmt_duration(lc.get('duration'))}  "
              f"{lc.get('connect') or '-'}  {lc.get('result')}")
    t = s["totals"]
    print(f"  Celkem     {t['calls']} hovorů ({t['ok']} úspěšných, dnes {t['today']}), "
          f"↓ {fmt_bytes(t['bytes_sent'])} ↑ {fmt_bytes(t['bytes_rcvd'])}")
    url = f"http://{s['host_ip']}" + ("" if s["web_port"] == 80 else f":{s['web_port']}")
    print(c(f"  Správa     {url}/", "2"))


def cmd_status(a):
    cfg = config.load()
    s = state.summary(cfg)
    if a.json:
        print(json.dumps(s, indent=2, ensure_ascii=False))
    else:
        _print_status(s, short=a.short)


def cmd_watch(a):
    cfg = config.load()
    prev = None
    try:
        while True:
            s = state.summary(cfg)
            cnt = (s["current"].get("counters") or None)
            rates = None
            if cnt and prev:
                dt = max(cnt["ts"] - prev["ts"], 0.1)
                rates = ((cnt["rx"] - prev["rx"]) / dt, (cnt["tx"] - prev["tx"]) / dt)
            prev = cnt
            if _TTY:
                print("\033[H\033[2J", end="")
            _print_status(s, rates=rates)
            print(c(f"\n  Obnovuje se každé {a.interval} s, konec Ctrl+C", "2"))
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print()


def cmd_calls(a):
    rows = state.calls(a.n)
    if not rows:
        print("Zatím žádné hovory.")
        return
    print(f"{'začátek':19}  {'délka':8}  {'uživatel':12}  {'spojení':18}  {'↓ ke klientovi':>14}  "
          f"{'↑ od klienta':>12}  výsledek")
    for r in rows:
        print(f"{fmt_time(r['started']):19}  {fmt_duration(r.get('duration')):8}  "
              f"{(r.get('user') or '-')[:12]:12}  {(r.get('connect') or '-')[:18]:18}  "
              f"{fmt_bytes(r.get('bytes_sent')):>14}  {fmt_bytes(r.get('bytes_rcvd')):>12}  "
              f"{r.get('result')}")


# ---- uživatelé -------------------------------------------------------------

def _ask_password(prompt="Heslo: "):
    p1 = getpass.getpass(prompt)
    p2 = getpass.getpass("Znovu: ")
    if p1 != p2:
        sys.exit("Hesla se neshodují.")
    return p1


def cmd_users(a):
    if a.action in (None, "list"):
        rows = users.list_users()
        if not rows:
            print("Žádní uživatelé. Přidej: sudo pitel users add JMÉNO")
        for u in rows:
            print(f"{u['name']:20} vytvořen {fmt_time(u['created'])}")
        return
    require_root()
    try:
        if a.action in ("add", "passwd"):
            if not a.name:
                sys.exit("Zadej jméno uživatele.")
            pw = a.password or _ask_password()
            new = users.set_user(a.name, pw)
            print(("Přidán" if new else "Změněno heslo") + f": {a.name}")
        elif a.action == "del":
            users.delete_user(a.name)
            print(f"Smazán: {a.name}")
    except users.UserError as e:
        sys.exit(str(e))


# ---- konfigurace -----------------------------------------------------------

def cmd_config(a):
    cfg = config.load()
    if a.action in (None, "show"):
        print(json.dumps(cfg, indent=2, ensure_ascii=False))
        return
    require_root()
    if a.key not in config.DEFAULTS:
        sys.exit(f"Neznámý klíč. Možné: {', '.join(config.DEFAULTS)}")
    val = a.value
    default = config.DEFAULTS[a.key]
    if isinstance(default, bool):
        val = val.lower() in ("1", "true", "ano", "yes", "y", "a")
    try:
        cfg[a.key] = val
        config.save(cfg)
    except config.ConfigError as e:
        sys.exit(str(e))
    for m in render.apply(cfg, restart_web=a.key in ("web_port", "web_bind")):
        print(m)


def cmd_apply(a):
    require_root()
    try:
        for m in render.apply(restart_web=a.restart_web):
            print(m)
    except config.ConfigError as e:
        sys.exit(f"Chyba konfigurace: {e}")


def cmd_restart(a):
    require_root()
    run(["systemctl", "restart", paths.UNIT_MGETTY], check=True)
    print("mgetty restartováno.")


def cmd_hangup(a):
    require_root()
    print(state.hangup(config.load()))


def cmd_modem(a):
    require_root()
    try:
        print(modem.query(config.load(), a.commands or None))
    except modem.ModemError as e:
        sys.exit(str(e))


def cmd_logs(a):
    print(state.logs(config.load(), a.n))


def cmd_update(a):
    if a.check:
        from . import update
        r = update.check(config.load(), force=True)
        print(f"Nainstalováno: {r['current']['version']}")
        print(f"Na GitHubu:    {r['latest'] or '-'}")
        if r["error"]:
            print(c(r["error"], "31"))
        elif r["available"]:
            print(c(f"Je dostupná nová verze {r['latest']} – nainstaluj: sudo pitel update", "32;1"))
        else:
            print("Máš nejnovější verzi.")
        return
    require_root()
    os.execv("/bin/bash", ["/bin/bash", os.path.join(paths.APP_DIR, "install.sh"), "--update"])


def cmd_admin_password(a):
    require_root()
    try:
        auth.set_password(_ask_password("Nové heslo do webové správy: "))
    except ValueError as e:
        sys.exit(str(e))
    print("Heslo nastaveno.")


def cmd_web(a):
    from . import web
    web.serve()


# ---- průvodce --------------------------------------------------------------

def _ask(prompt, default=None, check=None):
    while True:
        hint = f" [{default}]" if default not in (None, "") else ""
        try:
            val = input(f"{prompt}{hint}: ").strip()
        except EOFError:
            sys.exit("\nPřerušeno.")
        if not val and default is not None:
            val = str(default)
        if check:
            err = check(val)
            if err:
                print(c("  " + err, "31"))
                continue
        return val


def _detect_devices():
    try:
        return sorted(d for d in os.listdir(paths.DEV) if re.match(r"^tty(ACM|USB)\d+$", d))
    except FileNotFoundError:
        return []


def _detect_wan():
    rc, out = run(["ip", "route", "show", "default"], dry_ok=False)
    m = re.search(r"\bdev (\S+)", out or "")
    return m.group(1) if m else "eth0"


def cmd_setup(a):
    """Interaktivní průvodce – volá ho install.sh."""
    require_root()
    cfg = config.load()
    print(c("\n=== PiTel – nastavení dial-up serveru ===\n", "1"))

    devs = _detect_devices()
    if devs:
        print("Nalezené sériové porty: " + ", ".join("/dev/" + d for d in devs))
    else:
        print(c("Modem zatím nevidím (/dev/ttyACM* ani /dev/ttyUSB*). Nevadí, nastavíme ho i tak.", "33"))
    dflt = cfg["device"] if (config.exists() or not devs) else devs[0]
    cfg["device"] = _ask("Zařízení modemu", dflt).replace("/dev/", "")

    print("\nJak je modem u Raspberry Pi připojený k telefonní lince?")
    keys = list(config.LINE_TYPES)
    for i, k in enumerate(keys, 1):
        print(f"  {i}) {config.LINE_TYPES[k][0]}")
    cur = keys.index(cfg["line_type"]) + 1 if cfg["line_type"] in keys else 3
    sel = _ask("Volba", cur, lambda v: None if v in ("1", "2", "3") else "Zadej 1, 2 nebo 3.")
    new_type = keys[int(sel) - 1]
    if new_type != cfg["line_type"] or not config.exists():
        cfg["max_speed"] = config.LINE_TYPES[new_type][1]
    cfg["line_type"] = new_type

    speeds = ", ".join(str(s) for s in sorted(config.SPEEDS) if s)
    print(f"\nMaximální rychlost modemu (0 = bez omezení). Přes VoIP doporučuji 9600–14400.\n  Možnosti: {speeds}")
    cfg["max_speed"] = int(_ask("Max. rychlost", cfg["max_speed"],
                                lambda v: None if v.isdigit() and int(v) in config.SPEEDS
                                else "Neplatná hodnota."))
    cfg["rings"] = int(_ask("Zvednout po kolikátém zazvonění", cfg["rings"],
                            lambda v: None if v.isdigit() and 1 <= int(v) <= 10 else "1–10"))

    print("\nSíť pro dial-up klienta (Raspberry Pi bude NAT router):")
    wan = cfg["wan_iface"] if config.exists() else _detect_wan()
    cfg["wan_iface"] = _ask("Rozhraní do LAN/internetu", wan)
    cfg["server_ip"] = _ask("IP adresa Raspberry Pi na PPP lince", cfg["server_ip"])
    cfg["client_ip"] = _ask("IP adresa, kterou dostane volající PC", cfg["client_ip"])
    cfg["dns"] = _ask("DNS servery pro klienta (oddělit čárkou)", ", ".join(cfg["dns"]))
    cfg["idle_timeout"] = int(_ask("Odpojit při nečinnosti po N sekundách (0 = nikdy)",
                                   cfg["idle_timeout"],
                                   lambda v: None if v.isdigit() else "Zadej číslo."))
    tt = _ask("Povolit testovací echo pro terminál (a/n)", "a" if cfg["terminal_test"] else "n")
    cfg["terminal_test"] = tt.lower().startswith(("a", "y"))
    cfg["web_port"] = int(_ask("Port webové správy", cfg["web_port"],
                               lambda v: None if v.isdigit() and 0 < int(v) < 65536 else "1–65535"))
    if a.repo_url and not cfg.get("repo_url"):
        cfg["repo_url"] = a.repo_url

    try:
        config.save(cfg)
    except config.ConfigError as e:
        print(c(f"Chyba: {e}", "31"))
        print("Spusť průvodce znovu: sudo pitel setup")
        sys.exit(1)

    if not users.load():
        print("\nPřidej uživatele pro vytáčené připojení (zadá ho kamarád ve Windows):")
        while True:
            name = _ask("Jméno", "pitel")
            pw = _ask("Heslo (zobrazí se, je to heslo pro PAP/CHAP)")
            try:
                users.set_user(name, pw)
                break
            except users.UserError as e:
                print(c("  " + str(e), "31"))

    if not auth.has_password():
        print("\nHeslo do webové správy:")
        while True:
            try:
                auth.set_password(_ask_password("Heslo do správy: "))
                break
            except ValueError as e:
                print(c("  " + str(e), "31"))
    print(c("\nNastavení uloženo.", "32"))


# ---- main ------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="pitel", description="PiTel – dial-up server na Raspberry Pi")
    ap.add_argument("--version", action="version", version=f"PiTel {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("status", help="stav linky (výchozí)")
    p.add_argument("--short", action="store_true", help="jeden řádek")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("watch", help="živý přehled s rychlostí přenosu")
    p.add_argument("-i", "--interval", type=float, default=1.0)
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("calls", help="historie hovorů")
    p.add_argument("-n", type=int, default=20)
    p.set_defaults(func=cmd_calls)

    p = sub.add_parser("users", help="uživatelé dial-up připojení")
    p.add_argument("action", nargs="?", choices=["list", "add", "passwd", "del"])
    p.add_argument("name", nargs="?")
    p.add_argument("password", nargs="?")
    p.set_defaults(func=cmd_users)

    p = sub.add_parser("config", help="zobrazit / změnit nastavení")
    p.add_argument("action", nargs="?", choices=["show", "set"])
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("apply", help="znovu vygenerovat konfiguraci a restartovat služby")
    p.add_argument("--restart-web", action="store_true")
    p.set_defaults(func=cmd_apply)

    sub.add_parser("restart", help="restart mgetty").set_defaults(func=cmd_restart)
    sub.add_parser("hangup", help="ukončit probíhající hovor").set_defaults(func=cmd_hangup)

    p = sub.add_parser("modem", help="poslat AT příkazy modemu (jen bez hovoru)")
    p.add_argument("commands", nargs="*")
    p.set_defaults(func=cmd_modem)

    p = sub.add_parser("logs", help="logy mgetty/pppd")
    p.add_argument("-n", type=int, default=100)
    p.set_defaults(func=cmd_logs)

    p = sub.add_parser("update", help="aktualizovat z GitHubu")
    p.add_argument("--check", action="store_true", help="jen zjistit, jestli je nová verze")
    p.set_defaults(func=cmd_update)
    sub.add_parser("admin-password", help="změnit heslo do webu").set_defaults(func=cmd_admin_password)
    sub.add_parser("web", help="spustit webovou správu (spouští systemd)").set_defaults(func=cmd_web)

    p = sub.add_parser("setup", help="interaktivní průvodce nastavením")
    p.add_argument("--repo-url", default="")
    p.set_defaults(func=cmd_setup)

    a = ap.parse_args(argv)
    if not a.cmd:
        a = ap.parse_args(["status"])
    a.func(a)
