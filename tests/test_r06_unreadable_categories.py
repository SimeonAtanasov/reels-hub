"""Audit R-06: a _categories.json that is there but can't be read (cut off, empty, a list, a typo) is not "no
categories yet". It is kept as _categories.broken-<time>.json and never written over. With the copy from one change
before (_categories.prev.json) the lists come back from that; without one the file stays where it is, the lists
show empty and every change that rests on them is refused until the file is in order.
Before the fix: one start or one page load wrote {"categories": [], "tags": [], "homes": {}} over such a file: the
category list and its order, the tag list and every channel's home were gone, with no copy."""
import json
from helpers import run

FULL = {"categories": ["Training", "AI", "Health and safety"], "channels": [], "tags": ["ai/claude", "training/kettlebell"],
        "homes": {"kitchan": "Training"}, "channels_checked": True, "homes_v1": True}


def refused(env, name, call, word="_categories.json"):
    try:
        call()
    except ValueError as e:
        return env.check(name, word in str(e), str(e))
    except Exception as e:
        return env.check(name, False, f"{type(e).__name__}: {e}")
    return env.check(name, False, "it went through")


def body(env):
    rh = env.rh
    f, prev = env.out / "_categories.json", env.out / "_categories.prev.json"
    notes = getattr(rh, "FILE_NOTES", {})
    kept = lambda: sorted(env.out.glob("_categories.broken-*.json"))
    f.write_text(json.dumps(FULL, indent=1), encoding="utf-8")
    first = f.read_bytes()

    # ---- every change keeps the file as it was as the copy from before
    rh.write_tags(FULL["tags"] + ["health/sleep"])
    env.check("a change keeps the file from before as _categories.prev.json", prev.is_file() and prev.read_bytes() == first)
    good = f.read_bytes()
    rh.change_cats({"action": "add", "name": "Learning"})
    env.check("the copy is always one change old", prev.read_bytes() == good and "Learning" in rh.read_cats())
    good = f.read_bytes()
    want = json.loads(prev.read_text(encoding="utf-8"))

    # ---- unreadable, with a copy from before: set aside, the copy takes its place
    for kind, data in (("cut off", good[:len(good) - 25]), ("empty", b""), ("a list", json.dumps(FULL["categories"]).encode()),
                       ("not JSON at all", b"\x00\x00 not json {{{")):
        n = len(kept())
        f.write_bytes(data)
        cats = rh.read_cats()
        env.check(f"{kind}: the lists come from the copy of one change before",
                  cats == want["categories"] and rh.read_tags() == want["tags"] and rh.read_homes() == want["homes"], str(cats))
        k = kept()
        env.check(f"{kind}: the unreadable file is kept as _categories.broken-<time>.json, byte for byte",
                  len(k) == n + 1 and any(p.read_bytes() == data for p in k), str([p.name for p in k]))
        env.check(f"{kind}: _categories.json reads fine again and holds the homes",
                  json.loads(f.read_text(encoding="utf-8")).get("homes") == want["homes"])
        env.check(f"{kind}: the page is told, as a notice", "cats" in notes and not notes["cats"]["bad"], str(notes.get("cats")))
        notes.pop("cats", None)
    f.write_bytes(b"\xef\xbb\xbf" + good)                       # saved by Notepad or PowerShell with a BOM in front
    n = len(kept())
    env.check("a BOM in front is no damage: the file is read as it is", "Learning" in rh.read_cats() and len(kept()) == n
              and "cats" not in notes)

    # ---- unreadable and no copy from before: the file stays, nothing writes over it, changes are refused
    f.write_bytes(good)
    rh.read_cats()
    reel = env.make_reel("KitCat001", "Category case", hub={"category": "Training", "favorites": []})
    note = env.note_of(reel)
    hub_before = (reel / "hub.json").read_bytes()
    prev.unlink()
    bad = good[:len(good) // 2]
    f.write_bytes(bad)
    n = len(kept())
    env.check("no copy from before: the lists show empty", rh.read_cats() == [] and rh.read_tags() == [] and rh.read_homes() == {})
    env.check("no copy from before: the file is left exactly as it is", f.read_bytes() == bad)
    k = kept()
    env.check("no copy from before: a copy of it is kept all the same", len(k) == n + 1 and any(p.read_bytes() == bad for p in k))
    env.check("no copy from before: the page is told, and the note stays", notes.get("cats", {}).get("bad") is True, str(notes.get("cats")))
    refused(env, "adding a category is refused", lambda: rh.change_cats({"action": "add", "name": "Training"}))
    refused(env, "adding a tag is refused", lambda: rh.change_tags({"action": "add", "name": "x/y"}))
    refused(env, "a tag on a video is refused", lambda: rh.set_tags({"id": reel.name, "tags": ["x/y"]}))
    refused(env, "a channel's home is refused", lambda: rh.set_channel_home("kitchannel", ""))
    refused(env, "Move notes to category folders is refused (every note would leave its folder)", lambda: rh.sort_notes(True))
    st = dict(rh.parse_note(reel)["state"])
    st["category"] = ""
    refused(env, "a video's category change is refused", lambda: rh.save_state(reel.name, st))
    refused(env, "no writer gets past it: write_cats itself refuses", lambda: rh.write_cats(["Only this"]))
    rh.HOMES.update(state="", done=0, total=0, error="")
    rh.start_homes()
    rh.channel_cats()
    rh.list_reels()
    env.check("after all of it: the file is still exactly as it was", f.read_bytes() == bad and len(kept()) == n + 1,
              repr(f.read_bytes()[:80]))
    env.check("the video kept its category, and its note its place",
              (reel / "hub.json").read_bytes() == hub_before and note.is_file())

    # ---- in order again: by hand, or no file at all (a new library)
    f.write_bytes(good)
    env.check("repaired by hand: the lists are back and the note on the page goes",
              "Learning" in rh.read_cats() and "cats" not in notes)
    rh.change_cats({"action": "add", "name": "Travel"})
    env.check("repaired by hand: changes work again", "Travel" in json.loads(f.read_text(encoding="utf-8"))["categories"])
    f.unlink()
    prev.unlink()
    env.check("no file at all is a new library: empty lists, no note", rh.read_cats() == [] and "cats" not in notes)
    rh.change_cats({"action": "add", "name": "First"})
    env.check("a new library: the first category makes the file", json.loads(f.read_text(encoding="utf-8"))["categories"] == ["First"])


run("r06", body)
