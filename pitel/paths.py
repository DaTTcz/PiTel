"""Všechny cesty na jednom místě.

Pro testování mimo Raspberry Pi lze nastavit proměnnou PITEL_PREFIX
(např. /tmp/fakeroot) – všechny systémové cesty se pak berou relativně
k ní a příkazy systemctl/nft/sysctl se jen vypíšou (dry-run).
"""
import os

PREFIX = os.environ.get("PITEL_PREFIX", "").rstrip("/")
DRY_RUN = bool(PREFIX)

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def p(path: str) -> str:
    return PREFIX + path


# vlastní konfigurace
ETC = p("/etc/pitel")
CONFIG = ETC + "/config.json"
USERS = ETC + "/users.json"
ADMIN = ETC + "/admin.json"
PPP_OPTIONS = ETC + "/ppp-options"
NAT_NFT = ETC + "/nat.nft"

# běhový stav (tmpfs) – zapisují ho ppp-wrapper, ip-up, ip-down
RUN = p("/run/pitel")
CALL_ENV = RUN + "/call.env"
SESSION = RUN + "/session.json"
LAST_SESSION = RUN + "/last-session.json"

# trvalá data
VAR = p("/var/lib/pitel")
CALLS = VAR + "/calls.jsonl"
UPDATE_LOG = VAR + "/update.log"

# systémové soubory, které generujeme
MGETTY_CONFIG = p("/etc/mgetty/mgetty.config")
MGETTY_LOGIN = p("/etc/mgetty/login.config")
MGETTY_LOG_DIR = p("/var/log/mgetty")
PAP_SECRETS = p("/etc/ppp/pap-secrets")
CHAP_SECRETS = p("/etc/ppp/chap-secrets")
SYSCTL = p("/etc/sysctl.d/90-pitel.conf")
SYSTEMD_DIR = p("/etc/systemd/system")
PROFILE_D = p("/etc/profile.d/pitel.sh")

SYS_NET = p("/sys/class/net")
DEV = p("/dev")

UNIT_MGETTY = "pitel-mgetty.service"
UNIT_NAT = "pitel-nat.service"
UNIT_WEB = "pitel-web.service"
UNITS = (UNIT_MGETTY, UNIT_NAT, UNIT_WEB)
