"""Konfigurace aplikace: /etc/pitel/config.json."""
import ipaddress
import re

from . import paths
from .util import read_json, write_json

# Typ linky u Raspberry Pi -> doporučená maximální rychlost (0 = bez omezení)
LINE_TYPES = {
    "pbx": ("Analogová pobočka ústředny", 0),
    "ata": ("VoIP převodník (ATA)", 14400),
    "unknown": ("Zatím nevím", 14400),
}

# povolené maximální rychlosti a modulace, kterou pro ně použijeme v AT+MS
SPEEDS = {
    0: None,          # bez omezení – modem si vyjedná sám
    33600: "V34",
    31200: "V34",
    28800: "V34",
    26400: "V34",
    24000: "V34",
    21600: "V34",
    19200: "V34",
    14400: "V32B",
    12000: "V32B",
    9600: "V32B",
    7200: "V32B",
    4800: "V32",
    2400: "V22B",
}

DEFAULTS = {
    "device": "ttyACM0",
    "line_type": "unknown",
    "max_speed": 14400,
    "extra_init": [],          # další AT příkazy, např. ["ATS10=50"]
    "rings": 1,
    "server_ip": "192.168.50.1",
    "client_ip": "192.168.50.2",
    "dns": ["1.1.1.1", "8.8.8.8"],
    "wan_iface": "eth0",
    "idle_timeout": 0,         # sekundy, 0 = vypnuto
    "terminal_test": True,     # když volající nepošle PPP, dostane testovací echo
    "web_port": 80,
    "web_bind": "0.0.0.0",
    "repo_url": "https://github.com/DaTTcz/PiTel.git",
    "branch": "main",
    "update_channel": "tags",  # "tags" = jen vydané verze (v1.2.3), "branch" = poslední commit
}

_RE_DEV = re.compile(r"^tty[A-Za-z0-9]{1,16}$")
_RE_IFACE = re.compile(r"^[A-Za-z0-9._-]{1,15}$")
_RE_AT = re.compile(r"^AT[\x21-\x7e]{0,60}$", re.IGNORECASE)


class ConfigError(ValueError):
    pass


def load() -> dict:
    cfg = dict(DEFAULTS)
    data = read_json(paths.CONFIG, {}) or {}
    cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    return cfg


def save(cfg: dict) -> None:
    validate(cfg)
    write_json(paths.CONFIG, {k: cfg[k] for k in DEFAULTS}, 0o644)


def exists() -> bool:
    return read_json(paths.CONFIG) is not None


def _ip(value, name):
    try:
        return ipaddress.IPv4Address(str(value).strip())
    except ValueError:
        raise ConfigError(f"{name}: '{value}' není platná IPv4 adresa")


def validate(cfg: dict) -> dict:
    """Zkontroluje a znormalizuje hodnoty. Vyhazuje ConfigError s českou hláškou."""
    if not _RE_DEV.match(str(cfg.get("device", ""))):
        raise ConfigError("Zařízení modemu musí být ve tvaru ttyACM0 / ttyUSB0 (bez /dev/).")
    if cfg.get("line_type") not in LINE_TYPES:
        raise ConfigError("Neznámý typ linky.")
    try:
        cfg["max_speed"] = int(cfg.get("max_speed", 0))
    except (TypeError, ValueError):
        raise ConfigError("Maximální rychlost musí být číslo.")
    if cfg["max_speed"] not in SPEEDS:
        raise ConfigError("Nepodporovaná maximální rychlost. Povolené: "
                          + ", ".join(str(s) for s in sorted(SPEEDS) if s))

    extra = cfg.get("extra_init") or []
    if isinstance(extra, str):
        extra = [x.strip() for x in re.split(r"[;\n]", extra) if x.strip()]
    for cmd in extra:
        if not _RE_AT.match(cmd):
            raise ConfigError(f"Neplatný AT příkaz '{cmd}' (musí začínat AT, bez mezer a uvozovek).")
        if '"' in cmd or "\\" in cmd:
            raise ConfigError(f"AT příkaz '{cmd}' nesmí obsahovat uvozovky ani zpětné lomítko.")
    cfg["extra_init"] = extra

    try:
        cfg["rings"] = int(cfg.get("rings", 1))
    except (TypeError, ValueError):
        raise ConfigError("Počet zazvonění musí být číslo.")
    if not 1 <= cfg["rings"] <= 10:
        raise ConfigError("Počet zazvonění musí být 1–10.")

    srv = _ip(cfg.get("server_ip"), "IP serveru")
    cli = _ip(cfg.get("client_ip"), "IP klienta")
    if srv == cli:
        raise ConfigError("IP serveru a klienta nesmí být stejné.")
    cfg["server_ip"], cfg["client_ip"] = str(srv), str(cli)

    dns = cfg.get("dns") or []
    if isinstance(dns, str):
        dns = [x for x in re.split(r"[,\s]+", dns) if x]
    if not 1 <= len(dns) <= 2:
        raise ConfigError("Zadej jeden nebo dva DNS servery.")
    cfg["dns"] = [str(_ip(d, "DNS")) for d in dns]

    if not _RE_IFACE.match(str(cfg.get("wan_iface", ""))):
        raise ConfigError("Neplatný název síťového rozhraní (např. eth0).")

    try:
        cfg["idle_timeout"] = int(cfg.get("idle_timeout", 0))
        cfg["web_port"] = int(cfg.get("web_port", 80))
    except (TypeError, ValueError):
        raise ConfigError("Časový limit a port musí být čísla.")
    if cfg["idle_timeout"] < 0:
        raise ConfigError("Časový limit nečinnosti nesmí být záporný.")
    if not 1 <= cfg["web_port"] <= 65535:
        raise ConfigError("Port webu musí být 1–65535.")
    _ip(cfg.get("web_bind", "0.0.0.0"), "Adresa webu")

    cfg["terminal_test"] = bool(cfg.get("terminal_test"))
    cfg["repo_url"] = str(cfg.get("repo_url") or "").strip()
    cfg["branch"] = str(cfg.get("branch") or "main").strip()
    if cfg["repo_url"] and not re.match(r"^(https://|git@)[\w.@:/~+-]+$", cfg["repo_url"]):
        raise ConfigError("Neplatná adresa repozitáře.")
    if cfg.get("update_channel") not in ("tags", "branch"):
        raise ConfigError("Kanál aktualizací musí být 'tags' nebo 'branch'.")
    if not re.match(r"^[\w./-]{1,60}$", cfg["branch"]):
        raise ConfigError("Neplatný název větve.")
    return cfg


def init_commands(cfg: dict) -> list:
    """AT příkazy, které mgetty pošle modemu při startu."""
    cmds = ["ATZ", "AT&F", "ATS0=0V1Q0X4&C1&D2&K3"]
    mod = SPEEDS.get(int(cfg.get("max_speed") or 0))
    if mod:
        # AT+MS=<modulace>,<automode>,<min>,<max> – syntaxi ověř v manuálu MT5634ZBA
        cmds.append(f"AT+MS={mod},1,300,{int(cfg['max_speed'])}")
    cmds.extend(cfg.get("extra_init") or [])
    return cmds
