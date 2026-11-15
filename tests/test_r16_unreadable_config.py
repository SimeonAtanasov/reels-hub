"""Audit R-16: a config.json that is there but can't be used (cut off, empty, no name in it) is not a first start.
It is kept as config.broken-<time>.json and never written over. With the copy from one change before
(config.prev.json) the settings, the Cloudflare token included, come back from that; without one the Welcome box
says what happened instead of suggesting new folders. config.json is written only when something changed.
Before the fix: the app showed Welcome as on a new laptop, and its Save wrote a new file without the Cloudflare
address, the Client ID and the Client Secret (which Cloudflare shows only once). No copy of the old file was kept."""
import json
from helpers import run


def body(env):
    rh = env.rh
    p = rh.config_path()
    prev = p.with_name("config.prev.json")
    kept = lambda: sorted(p.parent.glob("config.broken-*.json"))
    notes = getattr(rh, "FILE_NOTES", {})
    env.check("the test's config.json is inside its temp folder", str(p.resolve()).startswith(str(env.root.resolve())))
    cloud = {"url": "https://kit-inbox.example", "id": "kit-client-id.access", "secret": "KIT-SECRET-not-a-real-one",
             "person": "tester", "name": "Tester", "people": [{"person": "other", "name": "Other"}], "checked": "2026-11-01 10:00"}
    cfg = {"name": "Tester", "person": "tester", "out": str(env.out), "vault": str(env.vault), "sub_lang": "en",
           "saved": "2026-11-01 10:00", "can_delete": True, "cloud": cloud}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    first = p.read_bytes()
    env.check("a good config.json is read", rh.load_config().get("cloud", {}).get("secret") == cloud["secret"])

    # ---- Settings saved: the file from before is kept as config.prev.json
    rh.CONFIG.clear(); rh.CONFIG.update(cfg)
    rh.save_config({"name": "Tester", "out": str(env.out), "vault": str(env.vault), "sub_lang": "es",
                    "cloud_url": cloud["url"], "cloud_id": cloud["id"], "cloud_secret": ""})
    env.check("Save keeps the file from before as config.prev.json", prev.is_file() and prev.read_bytes() == first)
    saved = json.loads(p.read_text(encoding="utf-8"))
    env.check("Save kept the token and took the change", saved.get("cloud", {}).get("secret") == cloud["secret"] and saved["sub_lang"] == "es")

    # ---- the inbox's answer: written only when it changed
    info = {"person": "tester", "name": "Tester", "people": cloud["people"], "can_delete": True,
            "token": cloud["id"], "inbox": cloud["url"], "library": False}
    before = (p.read_bytes(), p.stat().st_mtime_ns)
    rh.remember_cloud(info)
    env.check("the same answer from the inbox does not write config.json again", (p.read_bytes(), p.stat().st_mtime_ns) == before)
    env.check("the time of the check is still known to the running app", rh.CONFIG["cloud"]["checked"] != cloud["checked"])
    rh.remember_cloud({**info, "people": []})
    now = json.loads(p.read_text(encoding="utf-8"))
    env.check("a changed answer is written, with the file from before kept", now["cloud"]["people"] == [] and prev.read_bytes() == before[0])
    good = p.read_bytes()
    want = json.loads(prev.read_text(encoding="utf-8"))

    # ---- unreadable, with a copy from before
    for kind, data in (("cut to 60 bytes", good[:60]), ("empty", b""), ("no name in it", json.dumps({"cloud": cloud}).encode())):
        n = len(kept())
        p.write_bytes(data)
        got = rh.load_config()
        env.check(f"{kind}: the settings come from the copy of one change before, the token included",
                  got == want and got["cloud"]["secret"] == cloud["secret"], str({k: got.get(k) for k in ("name", "out")}))
        k = kept()
        env.check(f"{kind}: the unreadable file is kept as config.broken-<time>.json, byte for byte",
                  len(k) == n + 1 and any(x.read_bytes() == data for x in k), str([x.name for x in k]))
        env.check(f"{kind}: config.json is good again", json.loads(p.read_text(encoding="utf-8")) == want)
        env.check(f"{kind}: the page is told, as a notice", "config" in notes and not notes["config"]["bad"], str(notes.get("config")))
        notes.pop("config", None)

    # ---- unreadable and no copy from before: not a first start
    prev.unlink()
    n = len(kept())
    cut = good[:60]
    p.write_bytes(cut)
    got = rh.load_config()
    k = kept()
    env.check("no copy from before: nothing is made up", got == {})
    env.check("no copy from before: the file is kept as config.broken-<time>.json", len(k) == n + 1 and any(x.read_bytes() == cut for x in k))
    rh.CONFIG.clear()
    lost = getattr(rh, "config_lost", lambda: "")()
    env.check("the Welcome box is told that the settings were lost, not that this is a new laptop",
              bool(lost) and lost in [x.name for x in k] and rh.config_info().get("lost") == lost, str(lost))
    env.check("the black window and the page say so", notes.get("config", {}).get("bad") is True, str(notes.get("config")))
    rh.CONFIG.update(cfg)
    rh.save_config({"name": "Tester", "out": str(env.out), "vault": str(env.vault)})
    env.check("settings entered again: saved, and the kept files are still there",
              json.loads(p.read_text(encoding="utf-8"))["name"] == "Tester" and len(kept()) == n + 1 and "config" not in notes)
    env.check("after that the Welcome box has nothing to say", rh.config_lost() == "")

    # ---- a real first start
    for x in list(kept()) + [p] + ([prev] if prev.exists() else []):
        x.unlink()
    rh.CONFIG.clear()
    env.check("no config.json and nothing kept: a first start, as before", rh.load_config() == {} and rh.config_lost() == ""
              and "config" not in notes)
    rh.CONFIG.update(cfg)


run("r16", body)
