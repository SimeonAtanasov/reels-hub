"""Audit R-13: a note you move or rename in Obsidian is found again at the next look, also right after another
search, and also for transcript-only notes. A file is taken as a video's note only when it carries the video's id.
Before the fix: a note moved within two minutes of a vault walk was "not found (deleted?)" for 30 minutes and Send to
Obsidian then made a second note; a transcript-only note moved by hand was never found and Get the video made a second
one; a note of your own with the same file name in the platform folder was taken as the video's note."""
import json, time
from helpers import run

CATS = {"categories": ["Training", "AI"], "tags": [], "homes": {}, "channels": [], "channels_checked": True, "homes_v1": True}


def body(env):
    rh = env.rh
    (env.out / "_categories.json").write_text(json.dumps(CATS), encoding="utf-8")
    V = env.vault
    (V / "My filing").mkdir()

    def listed(folder):
        getattr(rh, "forget_all", lambda: None)()                 # no answer from the listing's memory
        return next(x for x in rh.list_reels() if x["id"] == folder.name)["obsidian"]

    def with_id(rid):
        return sorted(p.relative_to(V).as_posix() for p in V.rglob("*.md")
                      if ".trash" not in p.parts and rh.has_marker(p.read_text(encoding="utf-8"), rid))

    def hub(folder):
        return json.loads((folder / "hub.json").read_text(encoding="utf-8"))

    # ---- 1. two notes moved one after the other: the second right after the vault walk for the first
    a, b = env.make_reel("KitMove0001", "Moved first"), env.make_reel("KitMove0002", "Moved second")
    pa, pb = env.note_of(a), env.note_of(b)
    env.retry(lambda: pa.rename(V / "My filing" / "first renamed.md"))
    env.check("a moved and renamed note is found again", listed(a).get("path") == "My filing/first renamed.md")
    env.retry(lambda: pb.rename(V / "My filing" / "second moved.md"))
    o = listed(b)
    env.check("a note moved right after a vault walk is found (before: called deleted for 30 minutes)",
              o.get("path") == "My filing/second moved.md", str(o))
    env.check("hub.json has the note's new place", hub(b).get("note_path") == "My filing/second moved.md")

    # ---- 2. Send to Obsidian looks afresh before it makes a new note
    c = env.make_reel("KitMove0003", "Sent after a move")
    pc = env.note_of(c)
    listed(c)
    env.retry(lambda: pc.rename(V / "My filing" / "sent moved.md"))
    rh.MISS[c.name] = time.time()                                 # as if it was not found a moment ago
    try:
        rh.send_to_obsidian(c.name)
    except Exception as e:
        print("  (send:", e, ")")
    env.check("Send to Obsidian takes the moved note instead of making a second one", with_id(c.name) == ["My filing/sent moved.md"],
              str(with_id(c.name)))

    # ---- 3. a deleted note: said so, nothing made by the listing; looked for again after a minute
    d = env.make_reel("KitMove0004", "Deleted then back")
    pd = env.note_of(d)
    keep = pd.read_bytes()
    env.retry(pd.unlink)
    o = listed(d)
    env.check("a deleted note is reported as missing and the listing makes no new one", o.get("missing") is True and not with_id(d.name), str(o))
    at = rh.VIDX["at"]
    listed(d)
    env.check("within a minute the vault is not walked again for it", rh.VIDX["at"] == at)
    (V / "My filing" / "came back.md").write_bytes(keep)          # e.g. restored from Obsidian's trash into another folder
    rh.MISS[d.name] = time.time() - 61
    o = listed(d)
    env.check("after a minute it is looked for again and found (before: after 30 minutes)", o.get("path") == "My filing/came back.md", str(o))

    # ---- 4. the walk opens only notes that are new, moved or changed
    for k in range(30):
        (V / "My filing" / f"own note {k:02d}.md").write_text(f"my own note {k}\n", encoding="utf-8")
    opened = []
    real = getattr(rh, "_note_ids", None)
    if not env.check("the vault walk reads a note's ids in one place (_note_ids)", real is not None):
        return
    rh._note_ids = lambda p: (opened.append(str(p)), real(p))[1]
    old = time.time() - 60
    import os
    for p in V.rglob("*.md"):                                     # not just written: a note younger than 2 s is always read
        os.utime(p, (old, old))
    rh._vault_index(time.time())
    n1 = len(opened); opened.clear()
    rh._vault_index(time.time())
    env.check("a second walk with nothing changed opens no note", n1 >= 30 and opened == [], f"first {n1}, second {len(opened)}")
    env.retry(lambda: (V / "My filing" / "own note 07.md").rename(V / "own note 07 moved.md"))
    rh._vault_index(time.time())
    env.check("after one note was moved the walk opens exactly that one", len(opened) == 1 and opened[0].endswith("own note 07 moved.md"), str(opened))
    opened.clear()
    (V / "My filing" / "own note 08.md").write_text("changed, and longer\n", encoding="utf-8")
    os.utime(V / "My filing" / "own note 08.md", (old + 5, old + 5))
    rh._vault_index(time.time())
    env.check("after one note was changed the walk opens exactly that one", len(opened) == 1 and opened[0].endswith("own note 08.md"), str(opened))
    rh._note_ids = real

    # ---- 5. a note of your own with the same file name in the platform folder is not the video's
    e = env.make_reel("KitMove0005", "My favourite talk", hub={"category": "Training", "favorites": []})
    pe = env.note_of(e)
    env.check("(set-up) the video's note is in its category folder", pe.parent.name == "Training", str(pe))
    own = V / "YouTube" / pe.name
    own.write_text("2025-03-01\n**Title:** My favourite talk\n**Tags**: #YouTube #Mine\n\nMY OWN OLDER NOTE, written by hand\n", encoding="utf-8")
    mine = own.read_bytes()
    (V / "Filed by me").mkdir()
    env.retry(lambda: pe.rename(V / "Filed by me" / pe.name))
    o = listed(e)
    env.check("the moved note is found, not your own note of the same name", o.get("path") == "Filed by me/" + pe.name, str(o))
    try:
        rh.send_to_obsidian(e.name)
    except Exception as ex:
        print("  (send:", ex, ")")
    env.check("your own note is byte for byte the same after Send to Obsidian", own.read_bytes() == mine)

    # ---- 6. transcript-only notes are looked for too
    t1, t2 = env.make_transcript("kitchan", "KitTr000001", "Transcript one", name="Kit Channel"), \
        env.make_transcript("kitchan", "KitTr000002", "Transcript two", name="Kit Channel")
    n1, n2 = (V / rh.transcript_note(t, True)["path"] for t in (t1, t2))
    rh.list_transcripts()
    env.retry(lambda: n1.rename(V / "My filing" / "transcript renamed by me.md"))
    rh.TR_NOTE.clear(); rh.TR_CACHE.clear()
    it = next(x for x in rh.list_transcripts()["items"] if x["id"] == t1.name)
    env.check("the catalogue finds a moved transcript-only note again (before: never)",
              bool(it["note"]) and it["note"]["path"] == "My filing/transcript renamed by me.md", str(it["note"]))
    env.check("its hub.json has the new place", hub(t1).get("note_path") == "My filing/transcript renamed by me.md")
    rh.tr_set_category("kitchan", t1.name, "Training")
    env.check("a category change reaches the moved note", "#Training" in (V / "My filing" / "transcript renamed by me.md").read_text(encoding="utf-8"))
    full = env.make_reel("KitTr000001", "Transcript one", date="20261020")
    listed(full)
    env.check("Get the video keeps the moved note: one note per video", with_id(t1.name) == ["My filing/transcript renamed by me.md"], str(with_id(t1.name)))
    # the same without the catalogue in between: the note is moved and the video comes in at once
    env.retry(lambda: n2.rename(V / "My filing" / "second transcript moved.md"))
    full2 = env.make_reel("KitTr000002", "Transcript two", date="20261020")
    listed(full2)
    env.check("the same when the video comes in before the catalogue was opened", with_id(t2.name) == ["My filing/second transcript moved.md"], str(with_id(t2.name)))

    # ---- 7. two notes with one id: the app does nothing, and Send does not add a third
    f = env.make_reel("KitMove0007", "Copied note")
    pf = env.note_of(f)
    (V / "My filing" / "copy of it.md").write_bytes(pf.read_bytes())
    env.retry(lambda: pf.rename(V / "My filing" / "the original.md"))
    before = env.tree()
    o = listed(f)
    env.check("two notes with one id: the listing changes nothing and takes neither", env.tree() == before and not o.get("path"), str(o))
    try:
        rh.send_to_obsidian(f.name)
        said = ""
    except Exception as ex:
        said = str(ex)
    env.check("Send to Obsidian then refuses instead of making a third note", len(with_id(f.name)) == 2 and "More than one note" in said,
              f"{with_id(f.name)} | {said}")


run("r13", body)
