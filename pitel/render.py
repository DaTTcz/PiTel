"""Generování konfiguračních souborů a jejich aplikace do systému."""
import os
import shutil

from . import config, paths, state, users
from .util import atomic_write, run

HEADER = "# Generováno aplikací PiTel – neupravuj ručně.\n# Změny dělej přes web nebo příkazem `pitel`.\n"


def _mgetty_bin() -> str:
    return shutil.which("mgetty") or "/usr/sbin/mgetty"


def _pppd_bin() -> str:
    return shutil.which("pppd") or "/usr/sbin/pppd"


def mgetty_config(cfg: dict) -> str:
    chat = ['""']
    for i, cmd in enumerate(config.init_commands(cfg)):
        chat.append(cmd)
        chat.append("OK")
    return HEADER + f"""
# globální výchozí hodnoty
debug 4

port {cfg['device']}
  speed 115200
  data-only y
  rings {cfg['rings']}
  toggle-dtr y
  modem-check-time 3600
  init-chat {' '.join(chat)}
"""


def mgetty_login(cfg: dict) -> str:
    app = paths.APP_DIR
    mode = "echo" if cfg.get("terminal_test") else "deny"
    return HEADER + f"""
# Volající poslal PPP rámec -> spustíme pppd přes náš wrapper
/AutoPPP/ -     a_ppp   {app}/libexec/ppp-wrapper auto

# Kdokoliv jiný (terminál, test linky) -> testovací echo / odmítnutí
*         nobody  -     {app}/libexec/line-test {mode} @
"""


def ppp_options(cfg: dict) -> str:
    app = paths.APP_DIR
    lines = [
        HEADER,
        f"{cfg['server_ip']}:{cfg['client_ip']}",
    ]
    lines += [f"ms-dns {d}" for d in cfg["dns"]]
    lines += [
        "auth",
        "crtscts",
        "lock",
        "modem",
        "asyncmap 0",
        "nodefaultroute",
        "noproxyarp",
        "lcp-echo-interval 30",
        "lcp-echo-failure 4",
        "hide-password",
    ]
    if cfg.get("idle_timeout"):
        lines.append(f"idle {int(cfg['idle_timeout'])}")
    lines += [
        f"ip-up-script {app}/libexec/ip-up",
        f"ip-down-script {app}/libexec/ip-down",
    ]
    return "\n".join(lines) + "\n"


def nat_nft(cfg: dict) -> str:
    return HEADER + f"""
table ip pitel
delete table ip pitel

table ip pitel {{
    chain postrouting {{
        type nat hook postrouting priority srcnat; policy accept;
        ip saddr {cfg['client_ip']} oifname "{cfg['wan_iface']}" masquerade
    }}
    chain input {{
        type filter hook input priority filter; policy accept;
        # dial-up klient nemá přístup do správy
        ip saddr {cfg['client_ip']} tcp dport {cfg['web_port']} drop
    }}
}}
"""


def sysctl_conf(cfg: dict) -> str:
    return HEADER + "net.ipv4.ip_forward = 1\n"


def unit_mgetty(cfg: dict) -> str:
    return f"""# Generováno aplikací PiTel
[Unit]
Description=pitel: mgetty na /dev/{cfg['device']}
After=network-online.target {paths.UNIT_NAT}
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
ExecStart={_mgetty_bin()} {cfg['device']}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def unit_nat(cfg: dict) -> str:
    nft = shutil.which("nft") or "/usr/sbin/nft"
    return f"""# Generováno aplikací PiTel
[Unit]
Description=pitel: NAT pro dial-up klienta
After=network-pre.target
Before=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart={nft} -f {paths.NAT_NFT}
ExecStop=-{nft} delete table ip pitel

[Install]
WantedBy=multi-user.target
"""


def unit_web(cfg: dict) -> str:
    return f"""# Generováno aplikací PiTel
[Unit]
Description=pitel: webová správa
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 {paths.APP_DIR}/bin/pitel web
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
"""


def profile_d(cfg: dict) -> str:
    return """# pitel: krátký stav linky po přihlášení přes SSH
case $- in
  *i*) command -v pitel >/dev/null 2>&1 && pitel status --short 2>/dev/null ;;
esac
"""


def files(cfg: dict) -> list:
    """Seznam (cesta, obsah, práva) všeho, co generujeme."""
    return [
        (paths.MGETTY_CONFIG, mgetty_config(cfg), 0o644),
        (paths.MGETTY_LOGIN, mgetty_login(cfg), 0o600),
        (paths.PPP_OPTIONS, ppp_options(cfg), 0o644),
        (paths.NAT_NFT, nat_nft(cfg), 0o644),
        (paths.SYSCTL, sysctl_conf(cfg), 0o644),
        (os.path.join(paths.SYSTEMD_DIR, paths.UNIT_MGETTY), unit_mgetty(cfg), 0o644),
        (os.path.join(paths.SYSTEMD_DIR, paths.UNIT_NAT), unit_nat(cfg), 0o644),
        (os.path.join(paths.SYSTEMD_DIR, paths.UNIT_WEB), unit_web(cfg), 0o644),
        (paths.PROFILE_D, profile_d(cfg), 0o644),
    ]


def _backup_once(path: str) -> None:
    """Před prvním přepsáním souboru z balíčku si uložíme originál."""
    bak = path + ".pitel.orig"
    if os.path.exists(path) and not os.path.exists(bak):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                if "pitel" in f.read():
                    return
            shutil.copy2(path, bak)
        except OSError:
            pass


def apply(cfg: dict = None, restart_web: bool = False) -> list:
    """Zapíše všechny soubory a restartuje, co je potřeba. Vrací seznam hlášek."""
    if cfg is None:
        cfg = config.load()
    config.validate(cfg)
    msgs = []
    changed = set()

    for d in (paths.ETC, paths.VAR):
        os.makedirs(d, exist_ok=True)

    for path, content, mode in files(cfg):
        if path in (paths.MGETTY_CONFIG, paths.MGETTY_LOGIN):
            _backup_once(path)
        if atomic_write(path, content, mode):
            changed.add(path)
    users.write_secrets(client_ip=cfg["client_ip"])

    if any(p.startswith(paths.SYSTEMD_DIR) for p in changed):
        run(["systemctl", "daemon-reload"])

    run(["sysctl", "-q", "-p", paths.SYSCTL])
    run(["systemctl", "enable", "--quiet", *paths.UNITS])
    run(["systemctl", "restart", paths.UNIT_NAT])

    mgetty_files = {paths.MGETTY_CONFIG, paths.MGETTY_LOGIN, paths.PPP_OPTIONS,
                    os.path.join(paths.SYSTEMD_DIR, paths.UNIT_MGETTY)}
    if state.call_active():
        if changed & mgetty_files:
            msgs.append("Právě probíhá hovor – mgetty se restartuje až po zavěšení "
                        "(nebo ho restartuj ručně).")
        run(["systemctl", "start", paths.UNIT_MGETTY])
    else:
        run(["systemctl", "restart", paths.UNIT_MGETTY])

    web_file = os.path.join(paths.SYSTEMD_DIR, paths.UNIT_WEB)
    if restart_web or web_file in changed:
        # restart s malým zpožděním, aby web stihl odpovědět
        run(["systemd-run", "--quiet", "--collect", "--on-active=2",
             "systemctl", "restart", paths.UNIT_WEB])
        msgs.append("Webová správa se za chvíli restartuje.")
    else:
        run(["systemctl", "start", paths.UNIT_WEB])

    msgs.insert(0, f"Konfigurace uložena ({len(changed)} změněných souborů).")
    return msgs
