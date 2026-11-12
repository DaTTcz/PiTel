"""Uživatelé pro vytáčené připojení (PAP/CHAP)."""
import re
import time

from . import paths
from .util import atomic_write, read_json, write_json

_RE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_RE_PASS = re.compile(r"^[\x21-\x7e]{4,64}$")

BEGIN = "# BEGIN pitel (generováno – neupravuj ručně)"
END = "# END pitel"


class UserError(ValueError):
    pass


def load() -> dict:
    data = read_json(paths.USERS, {}) or {}
    return data.get("users", {})


def _save(users: dict) -> None:
    write_json(paths.USERS, {"users": users}, 0o600)


def list_users() -> list:
    return [{"name": n, "created": u.get("created"), "changed": u.get("changed")}
            for n, u in sorted(load().items())]


def validate(name: str, password: str = None):
    if not _RE_NAME.match(name or ""):
        raise UserError("Jméno: 1–32 znaků, jen písmena bez diakritiky, číslice, . _ -")
    if password is not None:
        if not _RE_PASS.match(password):
            raise UserError("Heslo: 4–64 znaků, bez mezer a diakritiky.")
        if any(c in password for c in '"\\#'):
            raise UserError("Heslo nesmí obsahovat \" \\ ani #.")


def set_user(name: str, password: str) -> bool:
    """Přidá nebo změní uživatele. Vrací True, pokud byl nový."""
    validate(name, password)
    users = load()
    now = int(time.time())
    new = name not in users
    u = users.get(name, {"created": now})
    u["password"] = password
    u["changed"] = now
    users[name] = u
    _save(users)
    write_secrets(users)
    return new


def delete_user(name: str) -> None:
    users = load()
    if name not in users:
        raise UserError(f"Uživatel '{name}' neexistuje.")
    del users[name]
    _save(users)
    write_secrets(users)


def _merge_block(path: str, block: str) -> str:
    """Nahradí náš blok v souboru *-secrets, zbytek souboru zachová."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        text = ""
    if BEGIN in text and END in text:
        pre = text.split(BEGIN, 1)[0]
        post = text.split(END, 1)[1]
        return pre + block + post
    if text and not text.endswith("\n"):
        text += "\n"
    return text + block + "\n"


def write_secrets(users: dict = None, client_ip: str = None) -> None:
    from . import config as _config
    if users is None:
        users = load()
    if client_ip is None:
        client_ip = _config.load()["client_ip"]
    lines = [BEGIN, "# klient\tserver\theslo\tIP"]
    for name, u in sorted(users.items()):
        lines.append(f'{name}\t*\t"{u["password"]}"\t{client_ip}')
    lines.append(END)
    block = "\n".join(lines)
    for path in (paths.PAP_SECRETS, paths.CHAP_SECRETS):
        atomic_write(path, _merge_block(path, block), 0o600)
