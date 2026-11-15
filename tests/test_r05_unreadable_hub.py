"""Audit R-05: a hub.json that is there but can't be read (cut off, a typo from a hand edit, empty) is not "no
favorites yet". Nothing writes over it: a star, a category, tags, deleting snapshots and Send to Obsidian are refused
with a message, the file stays byte for byte as it was, and a copy is kept next to it as hub.broken-<time>.json.
Before the fix: one click on a star answered "saved", replaced the file with a fresh one (category, tags and the
note's place gone) and the next listing made a second vault note for the same video."""
import json
from helpers import run

GOOD = {"category": "Training", "favorites": [{"shot": "shot_001.jpg", "text": "MY TEXT typed by hand", "corrected": True}],
        "tags": ["kit/tag"], "tag_tokens": ["#kit/tag"], "cat_tag": "#Training"}


def refused(env, name, call, word="hub.json"):
    try:
        call()
    except ValueError as e:
        return env.check(name, word in str(e), str(e))
    except Exception as e:                           # the old code may fail in other ways
        return env.check(name, False, f"{type(e).__name__}: {e}")
    return env.check(name, False, "it went through")


def body(env):
    rh = env.rh
    cats = env.out / "_categories.json"
    cats.write_text(json.dumps({"categories": ["Training", "AI"], "tags": ["kit/tag"], "homes": {}, "channels": [],
                                "channels_checked": True, "homes_v1": True}), encoding="utf-8")
    reels = {k: env.make_reel(f"KitHub{k}", f"Hub case {k}", hub=dict(GOOD)) for k in ("Typo", "Cut", "Empty", "Bom", "Moved")}
    notes = {k: env.note_of(f) for k, f in reels.items()}
    full = {k: (f / "hub.json").read_text(encoding="utf-8") for k, f in reels.items()}      # with note_path now
    hub = {k: f / "hub.json" for k, f in reels.items()}
    hub["Typo"].write_text(full["Typo"].replace('"category"', 'category'), encoding="utf-8")   # one quote lost
    hub["Cut"].write_text(full["Cut"][:len(full["Cut"]) // 2], encoding="utf-8")               # cut in half
    hub["Empty"].write_text("", encoding="utf-8")                                              # what a crash can leave
    hub["Bom"].write_bytes(b"\xef\xbb\xbf" + full["Bom"].encode("utf-8"))                      # saved by Notepad with a BOM
    bad = ("Typo", "Cut", "Empty")
    before = {k: hub[k].read_bytes() for k in bad}
    vault = env.tree()

    by = {r["id"]: r for r in rh.list_reels()}
    for k in bad:
        r = by[reels[k].name]
        env.check(f"{k}: the listing marks the video and says where the file is",
                  r["state"].get("_bad") is True and (r.get("bad_hub") or {}).get("folder") == str(reels[k]), str(r.get("bad_hub")))
    r = by[reels["Bom"].name]
    env.check("a hub.json with a BOM in front is read as it is", not r["state"].get("_bad") and r["state"]["category"] == "Training"
              and len(r["state"]["favorites"]) == 1, str(r["state"]))
    env.check("the listing leaves the unreadable files and the vault alone",
              all(hub[k].read_bytes() == before[k] for k in bad) and env.tree() == vault)

    # what the page did: it sent the state it showed (no favorites, no category) back with one new star
    st = dict(by[reels["Typo"].name]["state"])
    st["favorites"] = [{"shot": "shot_002.jpg", "text": "", "corrected": False}]
    refused(env, "a star on the video is refused", lambda: rh.save_state(reels["Typo"].name, st))
    env.check("the unreadable hub.json is byte for byte as it was", hub["Typo"].read_bytes() == before["Typo"])
    rh.list_reels()
    env.check("the next listing makes no second vault note", env.tree() == vault,
              str(sorted(set(env.tree()) - set(vault))))

    refused(env, "tags are refused", lambda: rh.set_tags({"id": reels["Typo"].name, "tags": ["new/tag"]}))
    env.check("the refused tag did not join the tag list", "new/tag" not in json.loads(cats.read_text(encoding="utf-8"))["tags"])
    refused(env, "a category change from the app's own functions is refused, also on an empty file",
            lambda: rh._tr_set_category(reels["Empty"], "AI"))
    refused(env, "deleting snapshots is refused (which ones are favorites is not known)",
            lambda: rh.delete_shots(reels["Empty"].name, ["shot_001.jpg"]))
    refused(env, "deleting snapshots with their stars is refused too",
            lambda: rh.delete_shots(reels["Empty"].name, ["shot_001.jpg"], True))
    env.check("the snapshot that was a favorite is still there", (reels["Empty"] / "shots" / "shot_001.jpg").is_file())
    refused(env, "Send to Obsidian is refused", lambda: rh.send_to_obsidian(reels["Cut"].name))
    refused(env, "no writer gets past it: write_state itself refuses",
            lambda: rh.write_state(reels["Cut"], {"category": "", "favorites": []}))
    env.check("after all of it: the three files and the vault are as they were",
              all(hub[k].read_bytes() == before[k] for k in bad) and env.tree() == vault)
    for k in bad:
        kept = sorted(reels[k].glob("hub.broken-*.json"))
        env.check(f"{k}: one copy is kept next to it, with the same bytes",
                  len(kept) == 1 and kept[0].read_bytes() == before[k], str([p.name for p in kept]))

    # a transcript-only video: no second note either
    tf = env.make_transcript("kitchan", "KitTr0001", "Transcript case")
    info = rh.transcript_note(tf, True)
    tnote = env.vault / info["path"]
    (tf / "hub.json").write_text((tf / "hub.json").read_text(encoding="utf-8")[:20], encoding="utf-8")
    tv = env.tree()
    again = rh.transcript_note(tf, True)
    env.check("a transcript's unreadable hub.json: no second note is made", "error" in again and env.tree() == tv and tnote.is_file(),
              str(again))
    refused(env, "a transcript's tags are refused", lambda: rh.set_tags({"channel": "kitchan", "id": tf.name, "tags": ["new/tag"]}))

    # the way out by hand: repair the file, or move it away
    hub["Cut"].write_text(full["Cut"], encoding="utf-8")
    st = dict(next(r for r in rh.list_reels() if r["id"] == reels["Cut"].name)["state"])
    env.check("repaired by hand: the video is read again with everything in it",
              not st.get("_bad") and st["category"] == "Training" and st["favorites"][0]["text"] == "MY TEXT typed by hand")
    st["favorites"] = st["favorites"] + [{"shot": "shot_002.jpg", "text": "", "corrected": False}]
    rh.save_state(reels["Cut"].name, st)
    env.check("repaired by hand: a star is saved again",
              len(json.loads(hub["Cut"].read_text(encoding="utf-8"))["favorites"]) == 2)
    files = sorted(env.tree())
    hub["Typo"].rename(env.root / "moved-away-hub.json")
    rh.VIDX["at"] = 0
    rh.MISS.clear()
    r = next(x for x in rh.list_reels() if x["id"] == reels["Typo"].name)
    rel = notes["Typo"].relative_to(env.vault).as_posix()
    env.check("moved away by hand: the video takes its own note again by the id marker, no second note",
              r["obsidian"].get("path") == rel and sorted(env.tree()) == files, str(r["obsidian"]))


run("r05", body)
