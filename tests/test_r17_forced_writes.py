"""Audit R-17: every file that holds your work is forced onto the disk before it replaces the old one (flush +
fsync), as the waiting line already was: hub.json, note.md, the vault note, _categories.json, config.json,
_channel.json, an edited image, a share picked up from the inbox (before it is deleted online), and what the
pipeline writes. A write that fails leaves no temp file, and at start the app removes its own left-over temp files
(.<name>.<numbers>.tmp, older than a day) and nothing else.
Before the fix: only _queue.json was forced. After a crash or a power cut within seconds of a save any of the other
files could be empty, which then looked like "nothing here yet" (R-05, R-06, R-16, R-02)."""
import base64, json, os, time
from helpers import run, FAKE_JPEG

CALLS = []
_fsync = os.fsync


def counting(fd):
    CALLS.append(fd)
    return _fsync(fd)


def forced(env, name, call, at_least=1):
    n = len(CALLS)
    try:
        call()
    except Exception as e:
        return env.check(name, False, f"{type(e).__name__}: {e}")
    return env.check(name, len(CALLS) - n >= at_least, f"{len(CALLS) - n} forced write(s), expected at least {at_least}")


def body(env):
    rh, r2n = env.rh, env.r2n
    (env.out / "_categories.json").write_text(json.dumps({"categories": ["Training"], "tags": [], "homes": {}, "channels": [],
                                                          "channels_checked": True, "homes_v1": True}), encoding="utf-8")
    reel = env.make_reel("KitSync001", "Forced writes", hub={"category": "", "favorites": []})
    note = env.note_of(reel)
    os.fsync = counting
    try:
        st = dict(rh.parse_note(reel)["state"])
        st["favorites"] = [{"shot": "shot_001.jpg", "text": "typed by hand", "corrected": True}]
        forced(env, "a star: hub.json and note.md", lambda: rh.save_state(reel.name, st), 2)
        forced(env, "My notes: note.md", lambda: rh.save_notes(reel.name, "a line of my own"))
        st2 = dict(st, category="Training")
        forced(env, "a category: hub.json, note.md and the vault note", lambda: rh.save_state(reel.name, st2), 3)
        forced(env, "tags: the tag list, hub.json and the vault note", lambda: rh.set_tags({"id": reel.name, "tags": ["kit/tag"]}), 3)
        forced(env, "Send to Obsidian: the vault note and hub.json", lambda: rh.send_to_obsidian(reel.name), 2)
        forced(env, "the category list", lambda: rh.change_cats({"action": "add", "name": "AI"}))
        forced(env, "an edited image", lambda: rh.save_edit(reel.name, "shot_002.jpg",
                                                            "data:image/jpeg;base64," + base64.b64encode(FAKE_JPEG).decode()))
        forced(env, "Settings: config.json", lambda: rh.save_config({"name": "Tester", "out": str(env.out), "vault": str(env.vault)}))
        tf = env.make_transcript("kitchan", "KitTr0002", "Transcript")
        forced(env, "a channel's _channel.json", lambda: rh._save_channel_meta(tf.parent, dict(rh._channel_meta(tf.parent), note_path="x.md")))
        forced(env, "a transcript's vault note and hub.json", lambda: rh.transcript_note(tf, True), 2)
        info = json.loads((reel / "video.info.json").read_text(encoding="utf-8"))
        forced(env, "the pipeline's note.md", lambda: r2n.write_note(reel, info["webpage_url"], info, [], ["[00:00] Again."], "en", None, "tester"))

        # a share from the inbox is on the disk before it is deleted online
        seen = {}

        def cloud(method, path, body=None, c=None, raw=False, timeout=30, text=None):
            if method == "GET" and path == "/api/inbox":
                return {"shares": [{"id": "kitshare-0001", "url": "https://www.youtube.com/watch?v=KitShare01", "from": "other",
                                    "from_name": "Other", "title": "Shared", "platform": "YouTube",
                                    "favorites": [{"t": 1.0, "text": "hers", "corrected": False, "image": 0}]}]}
            if method == "GET" and "/image/" in path:
                return base64.b64encode(FAKE_JPEG).decode()
            if method == "DELETE":
                d = env.out / "_inbox" / "kitshare-0001"
                seen.update(share=(d / "share.json").is_file(), image=(d / "img_000.jpg").is_file(),
                            listed="kitshare-0001" in rh._seen(), forced=len(CALLS) - n0)
                return {}
            raise AssertionError(f"unexpected call {method} {path}")
        real, rh.cloud_call, n0 = rh.cloud_call, cloud, len(CALLS)
        try:
            rh.pickup()
        finally:
            rh.cloud_call = real
        env.check("a share is on the disk, image and list of picked-up shares included, before it is deleted online",
                  seen.get("share") and seen.get("image") and seen.get("listed") and seen.get("forced", 0) >= 3, str(seen))

        # a write that fails leaves the old file and no temp file
        target = reel / "hub.json"
        before = target.read_bytes()

        def failing(fd):
            raise OSError(28, "No space left on device")
        os.fsync = failing
        try:
            rh.atomic_write(target, "{}")
            env.check("a write that can't be forced onto the disk is refused", False, "it went through")
        except OSError:
            env.check("a write that can't be forced onto the disk is refused", True)
        env.check("the old file is as it was, and no temp file is left", target.read_bytes() == before
                  and not [p.name for p in reel.iterdir() if p.name.endswith(".tmp")], str(sorted(p.name for p in reel.iterdir())))
    finally:
        os.fsync = _fsync

    # ---- left-over temp files at start
    note = env.vault / json.loads((reel / "hub.json").read_text(encoding="utf-8"))["note_path"]   # it followed the category
    old = time.time() - 3 * 86400
    share = env.out / "_inbox" / "kitshare-0002"
    share.mkdir(parents=True)
    (env.out / "_trash").mkdir(exist_ok=True)
    (env.vault / "YouTube" / "Training" / "Kit Channel").mkdir(parents=True, exist_ok=True)
    cfgdir = rh.config_path().parent
    cfgdir.mkdir(parents=True, exist_ok=True)
    go = [env.out / "._categories.json.4242.1234.tmp", reel / ".hub.json.4242.1234.tmp", reel / "shots" / ".shot_001.jpg.4242.1234.tmp",
          tf / ".note.md.4242.1234.tmp", tf.parent / "._channel.json.4242.1234.tmp", share / ".share.json.4242.1234.tmp",
          env.out / "._stats.json.4242.tmp", cfgdir / ".config.json.4242.1234.tmp",
          env.vault / "YouTube" / "Training" / "Kit Channel" / ".'A note (v2).md.4242.1234.tmp", note.parent / ".x.md.1.2.tmp"]
    stay = [reel / ".hub.json.4242.9999.tmp",                    # from today: a write may be going on
            reel / ".note.md.tmp", reel / "note.tmp", reel / "hub.json.4242.1234.tmp", reel / ".hub.json.abc.def.tmp",
            env.out / "_trash" / ".hub.json.4242.1234.tmp",      # the bin is never tidied
            env.vault / ".note.md.4242.1234.tmp",                # the vault outside the app's platform folders
            env.vault / "My own folder" / ".note.md.4242.1234.tmp"]
    for x in go + stay:
        x.parent.mkdir(parents=True, exist_ok=True)
        x.write_text("", encoding="utf-8")
        if x is not stay[0]:
            os.utime(x, (old, old))
    folder_like = reel / ".folder.4242.1234.tmp"
    folder_like.mkdir()
    os.utime(folder_like, (old, old))
    keep = {p: p.read_bytes() for p in (reel / "hub.json", reel / "note.md", note, env.out / "_categories.json")}
    rh.CONFIG.update(name="Tester")
    gone = getattr(rh, "clean_tmp", lambda: [])()
    env.check("left-over temp files older than a day are removed, in every folder the app writes to",
              not [x for x in go if x.exists()], str([str(x.relative_to(env.root)) for x in go if x.exists()]))
    env.check("everything else stays: a temp file from today, other names, the bin, the rest of the vault, a folder",
              all(x.exists() for x in stay) and folder_like.is_dir(), str([str(x.relative_to(env.root)) for x in stay if not x.exists()]))
    env.check("it removed exactly those files and no other", len(gone) == len(go) and all(p.read_bytes() == b for p, b in keep.items()),
              f"{len(gone)} removed, {len(go)} expected")


run("r17", body)
