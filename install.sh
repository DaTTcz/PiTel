#!/usr/bin/env bash
# PiTel – instalace a aktualizace dial-up serveru na Raspberry Pi OS (Lite).
#
#   sudo ./install.sh               první instalace (spustí průvodce)
#   sudo ./install.sh --reconfigure znovu spustit průvodce nastavením
#   sudo ./install.sh --update      stáhnout novou verzi z GitHubu a použít ji
#
# Jde spustit i přímo z GitHubu:
#   curl -fsSL https://raw.githubusercontent.com/DaTTcz/PiTel/main/install.sh | sudo bash
set -euo pipefail

REPO_URL_DEFAULT="https://github.com/DaTTcz/PiTel.git"
BRANCH_DEFAULT="main"
APP_DIR="/opt/pitel"
PACKAGES=(mgetty ppp nftables git python3)

MODE="install"
POST_UPDATE=0
for arg in "$@"; do
  case "$arg" in
    --update) MODE="update" ;;
    --reconfigure) MODE="reconfigure" ;;
    --post-update) POST_UPDATE=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Neznámý parametr: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*"; }
die()  { printf '\033[1;31mChyba:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Spusť přes sudo:  sudo $0 $*"
command -v apt-get >/dev/null || die "Tento instalátor je pro Raspberry Pi OS / Debian."

# odkud běžíme? (při curl | bash je BASH_SOURCE prázdné)
SRC_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

cfg_get() {  # přečte hodnotu z /etc/pitel/config.json
  python3 - "$1" <<'PY' 2>/dev/null || true
import json, sys
try:
    print(json.load(open("/etc/pitel/config.json")).get(sys.argv[1], "") or "")
except Exception:
    pass
PY
}

REPO_URL="$(cfg_get repo_url)"; REPO_URL="${REPO_URL:-${PITEL_REPO:-$REPO_URL_DEFAULT}}"
BRANCH="$(cfg_get branch)";     BRANCH="${BRANCH:-$BRANCH_DEFAULT}"
CHANNEL="$(cfg_get update_channel)"; CHANNEL="${CHANNEL:-tags}"

current_version() {  # nejvyšší tag na HEAD, jinak git describe
  local t
  t="$(git -C "$APP_DIR" tag --points-at HEAD -l 'v[0-9]*' --sort=-v:refname | head -1)"
  [[ -n "$t" ]] && echo "$t" || git -C "$APP_DIR" describe --tags --always --match "v[0-9]*"
}

latest_tag() {  # nejvyšší tag ve tvaru v1.2.3
  git -C "$APP_DIR" tag -l 'v[0-9]*' --sort=-v:refname | grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$' | head -1
}

# ---------------------------------------------------------------------------
# 1) balíčky
# ---------------------------------------------------------------------------
missing=()
for p in "${PACKAGES[@]}"; do
  dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p")
done
if ((${#missing[@]})); then
  say "Instaluji balíčky: ${missing[*]}"
  apt-get update -q
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${missing[@]}"
else
  say "Balíčky jsou nainstalované (${PACKAGES[*]})"
fi

# ---------------------------------------------------------------------------
# 2) kód aplikace do /opt/pitel
# ---------------------------------------------------------------------------
if [[ "$MODE" == "update" && $POST_UPDATE -eq 0 ]]; then
  [[ -d "$APP_DIR/.git" ]] || die "$APP_DIR není git repozitář – aktualizace z GitHubu nejde. Nainstaluj znovu z klonu."
  git -C "$APP_DIR" remote set-url origin "$REPO_URL"
  old="$(current_version)"
  if [[ "$CHANNEL" == "tags" ]]; then
    say "Hledám nejnovější vydanou verzi v $REPO_URL"
    git -C "$APP_DIR" fetch --quiet --force --tags origin
    target="$(latest_tag)"
    [[ -n "$target" ]] || die "Na GitHubu zatím není žádný tag ve tvaru v1.2.3."
    git -C "$APP_DIR" -c advice.detachedHead=false checkout --quiet --force "$target"
  else
    say "Stahuji poslední commit z $REPO_URL ($BRANCH)"
    git -C "$APP_DIR" fetch --quiet --tags origin "$BRANCH"
    git -C "$APP_DIR" checkout --quiet --force -B "$BRANCH" "origin/$BRANCH"
  fi
  new="$(current_version)"
  if [[ "$old" == "$new" ]]; then
    say "Už máš nejnovější verzi ($new)."
  else
    say "Aktualizováno $old -> $new"
  fi
  # zbytek dokončí NOVÁ verze instalátoru
  exec bash "$APP_DIR/install.sh" --update --post-update
fi

if [[ -n "$SRC_DIR" && -f "$SRC_DIR/pitel/__init__.py" && "$SRC_DIR" != "$APP_DIR" ]]; then
  say "Kopíruji aplikaci z $SRC_DIR do $APP_DIR"
  mkdir -p "$APP_DIR"
  cp -a "$SRC_DIR/." "$APP_DIR/"
  chown -R root:root "$APP_DIR"
elif [[ ! -f "$APP_DIR/pitel/__init__.py" ]]; then
  say "Stahuji aplikaci z $REPO_URL"
  git clone --quiet -b "$BRANCH" "$REPO_URL" "$APP_DIR"
  if [[ "$CHANNEL" == "tags" && -n "$(latest_tag)" ]]; then
    git -C "$APP_DIR" -c advice.detachedHead=false checkout --quiet "$(latest_tag)"
  fi
fi
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

chmod 755 "$APP_DIR/bin/pitel" "$APP_DIR/libexec/"*
ln -sf "$APP_DIR/bin/pitel" /usr/local/bin/pitel
mkdir -p /etc/pitel /var/lib/pitel
chmod 755 /etc/pitel

# ---------------------------------------------------------------------------
# 3) kolize s jinými programy
# ---------------------------------------------------------------------------
if systemctl list-unit-files ModemManager.service >/dev/null 2>&1 \
   && systemctl list-unit-files ModemManager.service | grep -q ModemManager; then
  if systemctl is-enabled ModemManager.service >/dev/null 2>&1; then
    warn "ModemManager by modem zabíral – vypínám ho."
    systemctl disable --now ModemManager.service || true
  fi
fi

# ---------------------------------------------------------------------------
# 4) průvodce nastavením
# ---------------------------------------------------------------------------
run_setup=0
if [[ ! -f /etc/pitel/config.json ]]; then
  run_setup=1
elif [[ "$MODE" == "reconfigure" ]]; then
  run_setup=1
elif [[ "$MODE" == "install" && -r /dev/tty ]]; then
  read -r -p "Konfigurace už existuje. Spustit průvodce znovu? [a/N] " ans </dev/tty || ans=""
  [[ "$ans" =~ ^[aAyY] ]] && run_setup=1
fi

if ((run_setup)); then
  [[ -r /dev/tty ]] || die "Průvodce potřebuje terminál. Spusť: sudo pitel setup"
  python3 "$APP_DIR/bin/pitel" setup --repo-url "$REPO_URL" </dev/tty
fi

# ---------------------------------------------------------------------------
# 5) vygenerovat konfiguraci a spustit služby
# ---------------------------------------------------------------------------
say "Generuji konfiguraci a spouštím služby"
python3 "$APP_DIR/bin/pitel" apply --restart-web

echo
python3 "$APP_DIR/bin/pitel" status || true
echo
say "Hotovo. Webová správa: viz řádek „Správa“ výše."
say "Užitečné příkazy: pitel status | pitel watch | pitel calls | pitel users | pitel --help"
