"""Audit R-12: every listing of the library read every video's files again (note.md, hub.json three times, shots.json,
every snapshot's time) and looked for every note in the vault: 10 to 17 seconds for about 870 videos on the laptop,
and the transcripts catalogue read every video.info.json again on each call.
Now the app keeps what it read of each video and each transcript in memory and reads a video's files again only when
one of them changed (time stamp and size). Whether a note is still at its place in the vault is checked at most once a
minute per video, so a note moved or deleted in Obsidian shows up to a minute later.
Before the fix: the fourth listing below opened as many files as the first."""
import builtins, io, json, os, time
from pathlib import Path
from helpers import run, FAKE_JPEG


class Opens:
    """Counts the files opened under one folder while it is active (the app opens through open() and pathlib)."""
    def __init__(self, root):
        self.root, self.paths = str(Path(root).resolve()), []

    def __enter__(self):
        self.real_io, self.real_b = io.open, builtins.open

        def counted(file, *a, **k):
            try:
                p = str(Path(os.fspath(file)).resolve()) if not isinstance(file, int) else ""
            except (TypeError, OSError, ValueError):
                p = ""
            if p.startswith(self.root):
                self.paths.append(p[len(self.root) + 1:].replace("\\", "/"))
            return self.real_io(file, *a, **k)
        io.open = builtins.open = counted
        return self

    def __exit__(self, *a):
        io.open, builtins.open = self.real_io, self.real_b


def age(root, secs=30):
    """Set every file and folder under root to a time in the past: as on a laptop where nothing was touched for a while
    (the app reads a file again that changed in the last two seconds)."""
    t = time.time() - secs
    for p in [Path(root)] + sorted(Path(root).rglob("*")):
        try:
            os.utime(p, (t, t))
        except OSError:
            pass


def body(env):
    rh = env.rh
    if not hasattr(rh, "REEL_CACHE"):
        print("  (this reels_hub.py has no memory for the listings: the checks below show what that costs)")
    (env.out / "_categories.json").write_text(json.dumps({"categories": ["Training", "AI"], "tags": [], "homes": {}, "channels": [],
                                                           "channels_checked": True, "homes_v1": True}), encoding="utf-8")
    reels = [env.make_reel(f"KitFast{i:03d}", f"Fast listing {i}", date=f"202610{i:02d}", nshots=4) for i in range(1, 7)]
    trs = [env.make_transcript("kitfast", f"KitFastT{i:03d}", f"Catalogue item {i}", name="Kit Fast Channel") for i in range(1, 6)]
    for f in trs:
        rh.transcript_note(f, True)

    def listing():
        return {r["id"]: r for r in rh.list_reels()}

    def catalogue():
        return {(i["ch"], i["id"]): i for i in rh.list_transcripts()["items"]}

    first = listing()                                   # makes the vault notes and writes each note's place into hub.json
    env.check("the first listing shows every video with its note in the vault", len(first) == 6
              and all(r["obsidian"].get("path") and (env.vault / r["obsidian"]["path"]).is_file() for r in first.values()),
              str({k: r.get("obsidian") for k, r in first.items()})[:300])
    catalogue()
    age(env.root)
    listing(); catalogue()                              # everything is read once more (its times changed) and kept
    with Opens(env.root) as o:
        warm = listing()
    opened = [p for p in o.paths if p != "reels/_categories.json"]       # the lists are one small file, read as before
    env.check("a listing with nothing changed opens no file of any video and no note in the vault", not opened,
              f"{len(opened)} opened: {opened[:6]}")
    with Opens(env.root) as o:
        cat = catalogue()
    opened = [p for p in o.paths if p != "reels/_categories.json"]
    env.check("the transcripts catalogue with nothing changed opens no file of any video or transcript", not opened,
              f"{len(opened)} opened: {opened[:6]}")
    env.check("the catalogue lists the 5 transcripts and the 6 videos", len(cat) == 11 and sum(1 for i in cat.values() if i.get("reel")) == 6,
              str(sorted(cat))[:300])

    def same(a, b):
        return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)
    fresh = {f.name: rh.parse_note(f) for f in reels}
    env.check("what comes from memory is what the files say (every field of every video)",
              all(same({k: v for k, v in warm[n].items() if k not in ("obsidian", "ch", "chname")}, fresh[n]) for n in fresh),
              next((n for n in fresh if not same({k: v for k, v in warm[n].items() if k not in ("obsidian", "ch", "chname")}, fresh[n])), ""))

    # ---------- a change made by the app shows at once
    a = reels[0]
    st = dict(warm[a.name]["state"]); st["favorites"] = [{"shot": "shot_002.jpg", "text": "typed by hand", "corrected": True}]
    st["category"] = "Training"
    rh.save_state(a.name, st)
    with Opens(env.root) as o:
        now = listing()
    env.check("a star and a category saved in the app show in the next listing", now[a.name]["state"]["category"] == "Training"
              and [f["shot"] for f in now[a.name]["state"]["favorites"]] == ["shot_002.jpg"]
              and now[a.name]["state"]["rev"] == json.loads((a / "hub.json").read_text(encoding="utf-8")).get("rev"),
              str(now[a.name]["state"])[:300])
    env.check("and its note is shown at its new place (the Training folder)", "/Training/" in (now[a.name]["obsidian"].get("path") or ""),
              str(now[a.name]["obsidian"]))
    names = {f.name for f in reels[1:]}
    others = [p for p in o.paths if p.startswith("reels/") and p.split("/")[1] in names]
    env.check("only the changed video's files were read again", not others, str(others[:6]))

    # ---------- a change made by another program (or by hand) shows too
    b = reels[1]
    hub = json.loads((b / "hub.json").read_text(encoding="utf-8"))
    hub["category"] = "AI"
    (b / "hub.json").write_text(json.dumps(hub), encoding="utf-8")
    env.check("hub.json changed by another program: the listing shows it", listing()[b.name]["state"]["category"] == "AI")
    note = (b / "note.md").read_text(encoding="utf-8")
    (b / "note.md").write_text(note.replace("Hello there.", "Hello there, changed by hand."), encoding="utf-8")
    env.check("note.md changed by hand: the listing shows the new transcript line",
              any("changed by hand" in x["text"] for x in listing()[b.name]["transcript"]))
    (b / "shots" / "shot_009.jpg").write_bytes(FAKE_JPEG)
    env.check("a snapshot file that was added is listed", "shot_009.jpg" in listing()[b.name]["shots"])
    (b / "shots" / "shot_009.jpg").unlink()
    env.check("and gone again when it is deleted", "shot_009.jpg" not in listing()[b.name]["shots"])

    # ---------- an image saved from Paint: the file changes, its folder's time does not
    c = reels[2]
    shot = c / "shots" / "shot_001.jpg"
    rh._backup(shot)                                     # what Open in Paint does first
    age(c)
    listing(); before = listing()[c.name]
    with open(shot, "r+b") as fh:                        # Paint saves into the same file
        fh.write(FAKE_JPEG + b"\x00" * 9)
    t = time.time() + 5
    os.utime(shot, (t, t))
    after = listing()[c.name]
    env.check("an image saved from Paint (the file changed in place) shows with its new time in the next listing",
              after["mt"]["shot_001.jpg"] != before["mt"]["shot_001.jpg"] and "shot_001.jpg" in after["edited"],
              f"{before['mt'].get('shot_001.jpg')} {after['mt'].get('shot_001.jpg')}")

    # ---------- the note's place in the vault: looked at once a minute per video, not at every listing
    d = reels[3]
    age(env.root)
    listing(); listing()
    path = env.vault / listing()[d.name]["obsidian"]["path"]
    kept = path.read_bytes()
    path.unlink()                                        # deleted in Obsidian
    soon = listing()[d.name]["obsidian"]
    if hasattr(rh, "NOTE_CHECK"):
        env.check("a note deleted in Obsidian: within the same minute the listing still shows it (the vault is not asked again)",
                  soon.get("path") == path.relative_to(env.vault).as_posix(), str(soon))
        rh.NOTE_CHECK = 0                                # as if a minute had passed
    else:
        env.check("a note deleted in Obsidian: within the same minute the listing still shows it (the vault is not asked again)",
                  False, "this version asks the vault for every video at every listing")
    later = listing()[d.name]["obsidian"]
    env.check("after the minute it shows as not found, with the hint that Send to Obsidian makes a new one",
              later.get("missing") is True and "Send to Obsidian" in later.get("error", ""), str(later))
    path.write_bytes(kept)                               # put back (undo in Obsidian)
    env.check("and as found again once it is back", listing()[d.name]["obsidian"].get("path") == path.relative_to(env.vault).as_posix())
    if hasattr(rh, "NOTE_CHECK"):
        rh.NOTE_CHECK = 60

    # ---------- the catalogue follows changes as well
    t0 = trs[0]
    rh.tr_set_category("kitfast", t0.name, "AI")
    env.check("a transcript's new category shows in the next catalogue", catalogue()[("kitfast", t0.name)]["category"] == "AI")
    env.check("and the video whose hub.json another program changed shows its category there too",
              next(i for i in catalogue().values() if i.get("reel") == b.name)["category"] == "AI")

    # ---------- nobody gets the memory's own objects
    one = listing()
    one[a.name]["transcript"].append({"t": 999, "text": "added by the caller"})
    one[a.name]["state"]["favorites"].append({"shot": "x"})
    one[a.name]["title"] = "changed by the caller"
    two = listing()
    env.check("changing a listing's answer does not change the next listing", two[a.name]["title"] == "Fast listing 1"
              and len(two[a.name]["transcript"]) == 2 and len(two[a.name]["state"]["favorites"]) == 1, str(two[a.name]["title"]))

    # ---------- a deleted video leaves the list and the memory
    e = reels[5]
    rh.delete_reels([e.name], False)
    gone = listing()
    env.check("a deleted video is no longer listed", e.name not in gone and len(gone) == 5, str(sorted(gone)))
    if hasattr(rh, "REEL_CACHE"):
        env.check("and nothing of it is kept in memory", not any(e.name in k for k in rh.REEL_CACHE), str(list(rh.REEL_CACHE)[:3]))


run("r12", body)
