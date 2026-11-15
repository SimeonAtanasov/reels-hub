"""Audit R-14: a note of yours that quotes a video's id marker inside a sentence is not that video's note. Only a
marker that stands as a whole line counts, both when the app looks for a moved note (the vault index) and when it
checks a note before changing, moving or binning it.
Before the fix: with the video's own note gone, a category change rewrote your note's Category line and moved it."""
import json
from helpers import run


def body(env):
    rh = env.rh
    chan = getattr(rh, "has_channel_marker", lambda t, k: f"<!-- reels-hub:channel={k} -->" in t)   # (older app: none)
    (env.out / "_categories.json").write_text(json.dumps({"categories": ["Training"], "tags": [], "homes": {}, "channels": [],
                                                          "channels_checked": True, "homes_v1": True}), encoding="utf-8")
    rid = "20261101_KitQuote01"
    mark = f"<!-- reels-hub:id={rid} -->"
    env.check("a whole marker line counts", rh.has_marker(f"x\n{mark}\ny\n", rid))
    env.check("a whole marker line with spaces around counts", rh.has_marker(f"x\n  {mark} \ny\n", rid))
    env.check("a marker quoted in a sentence does not count", not rh.has_marker(f"Notes carry `{mark}` lines.\n", rid))
    env.check("a channel marker quoted in a sentence does not count",
              not chan("see `<!-- reels-hub:channel=kitchan -->` here\n", "kitchan"))
    env.check("a whole channel marker line counts", chan("a\n<!-- reels-hub:channel=kitchan -->\n", "kitchan"))

    folder = env.make_reel("KitQuote01", "Quote case")
    note = env.note_of(folder)
    env.check("the app's own note is found by its marker", rh.has_marker(note.read_text(encoding="utf-8"), rid))
    note.unlink()                                       # the video's own note is gone
    mine = env.vault / "YouTube" / "'How Reels Hub marks notes.md"
    mine.write_text("2026-11-01\n**Title:** my own documentation\n**Tags**: #YouTube #Mine\n**Category**: my own line\n\n"
                    f"Each note carries a line like `{mark}` under the header.\nUSER-DOC-LINE hand written\n", encoding="utf-8")
    before = mine.read_bytes()
    rh.VIDX["at"] = 0                                   # a fresh vault walk
    idx = rh._vault_index()
    env.check("the vault index does not take your note for the video's", rid not in idx, str(idx.get(rid)))
    rh.MISS.clear()
    r = next(x for x in rh.list_reels() if x["id"] == rid)
    st = r["state"]
    st["category"] = "Training"
    try:
        rh.save_state(rid, st)
    except Exception as e:
        print("  (save_state:", e, ")")
    env.check("a category change leaves your note as it was", mine.is_file() and mine.read_bytes() == before)
    env.check("the video's state does not point to your note",
              json.loads((folder / "hub.json").read_text(encoding="utf-8")).get("note_path") != "YouTube/'How Reels Hub marks notes.md")


run("r14", body)
