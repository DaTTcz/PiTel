"""Zjišťování nových verzí podle git tagů (v1.2.3) na GitHubu."""
import os
import re
import shutil
import time

from . import __version__, paths
from .util import read_json, run, write_json

CHECK_FILE = paths.VAR + "/update-check.json"
CHECK_EVERY = 6 * 3600  # automaticky nejvýš jednou za 6 hodin
_RE_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse(tag):
    m = _RE_TAG.match((tag or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def _git(*args, timeout=30):
    if not shutil.which("git") or not os.path.isdir(os.path.join(paths.APP_DIR, ".git")):
        return 1, ""
    return run(["git", "-C", paths.APP_DIR, *args], dry_ok=False, timeout=timeout)


_current_cache = None


def current(refresh: bool = False) -> dict:
    """Nainstalovaná verze (v rámci procesu se pamatuje – mění se jen aktualizací,
    která web restartuje)."""
    global _current_cache
    if _current_cache and not refresh:
        return _current_cache
    _current_cache = _current()
    return _current_cache


def _current() -> dict:
    """Nainstalovaná verze: přesný tag, jinak popis z git describe, jinak __version__."""
    # nejvyšší verze mezi tagy na aktuálním commitu
    rc, out = _git("tag", "--points-at", "HEAD")
    tags = sorted((t.strip() for t in out.splitlines() if parse(t)), key=parse, reverse=True) if rc == 0 else []
    tag = tags[0] if tags else None
    rc2, desc = _git("describe", "--tags", "--always", "--dirty", "--match", "v[0-9]*")
    rc3, out = _git("log", "-1", "--format=%h|%ci")
    commit, date = (out.strip().split("|", 1) if rc3 == 0 and "|" in out else (None, None))
    return {
        "version": tag or (desc.strip() if rc2 == 0 and desc.strip() else f"v{__version__}"),
        "tag": tag,
        "commit": commit,
        "date": date,
    }


def remote_tags(repo_url: str) -> list:
    """Seznam verzí (tagů) na GitHubu, od nejnovější."""
    rc, out = run(["git", "ls-remote", "--tags", "--refs", repo_url], dry_ok=False, timeout=30)
    if rc != 0:
        raise RuntimeError(out.strip().splitlines()[-1] if out.strip() else "git ls-remote selhal")
    tags = []
    for line in out.splitlines():
        name = line.rsplit("refs/tags/", 1)[-1].strip()
        if parse(name):
            tags.append(name)
    return sorted(tags, key=parse, reverse=True)


def check(cfg: dict, force: bool = False) -> dict:
    """Vrátí {current, latest, available, checked, error}. Výsledek se cachuje."""
    cur = current()
    cached = read_json(CHECK_FILE, {}) or {}
    fresh = time.time() - cached.get("checked", 0) < CHECK_EVERY
    if force or not fresh or cached.get("repo_url") != cfg.get("repo_url"):
        cached = {"checked": int(time.time()), "repo_url": cfg.get("repo_url"),
                  "latest": None, "error": None}
        if not cfg.get("repo_url"):
            cached["error"] = "Není nastavený repozitář."
        else:
            try:
                tags = remote_tags(cfg["repo_url"])
                cached["latest"] = tags[0] if tags else None
                if not tags:
                    cached["error"] = "Na GitHubu zatím není žádný tag ve tvaru v1.2.3."
            except RuntimeError as e:
                cached["error"] = str(e)
        try:
            write_json(CHECK_FILE, cached)
        except OSError:
            pass
    cur_v = parse(cur["tag"] or cur["version"].split("-")[0])
    lat_v = parse(cached.get("latest"))
    return {
        "current": cur,
        "latest": cached.get("latest"),
        "available": bool(lat_v and (not cur_v or lat_v > cur_v)),
        "checked": cached.get("checked"),
        "error": cached.get("error"),
    }


def cached(cfg: dict) -> dict:
    """Rychlá varianta bez síťového dotazu – jen z posledního výsledku kontroly."""
    c = read_json(CHECK_FILE, {}) or {}
    cur = current()
    cur_v = parse(cur["tag"] or cur["version"].split("-")[0])
    lat_v = parse(c.get("latest"))
    return {"current": cur["version"], "latest": c.get("latest"),
            "available": bool(lat_v and (not cur_v or lat_v > cur_v))}


def background_checker(get_cfg):
    """Vlákno pro web: kontrola hned po startu a pak každých 6 hodin."""
    import threading

    def loop():
        time.sleep(20)
        while True:
            try:
                check(get_cfg())
            except Exception:  # noqa: BLE001
                pass
            time.sleep(CHECK_EVERY)
    threading.Thread(target=loop, daemon=True, name="update-check").start()
