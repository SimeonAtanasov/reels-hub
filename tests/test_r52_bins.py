"""Audit R-52: nothing the app deletes is gone for good.
Two notes of the same file name deleted together, while Obsidian's .trash already holds that name: all three are kept.
Before: both got the same time-stamped name and one replaced the other.
Snapshots you delete (with the originals of edited ones) and the edited picture on Reset to original go to the reels
folder's _trash. Before: they were removed for good, the only deletes of the app that went to no bin."""
import base64, json
from helpers import run, FAKE_JPEG

CATS = {"categories": ["Training", "AI"], "tags": [], "homes": {}, "channels": [], "channels_checked": True, "homes_v1": True}


def body(env):
    rh = env.rh
    (env.out / "_categories.json").write_text(json.dumps(CATS), encoding="utf-8")
    V = env.vault

    # ---- two notes of one name into .trash, which has that name already
    a = env.make_reel("KitSame0001", "Same title", hub={"category": "Training", "favorites": []})
    b = env.make_reel("KitSame0002", "Same title", hub={"category": "AI", "favorites": []})
    na, nb = env.note_of(a), env.note_of(b)
    env.check("(set-up) two notes with one file name in two folders", na.name == nb.name and na != nb, f"{na} {nb}")
    na.write_text(na.read_text(encoding="utf-8") + "\nUSER-TEXT-IN-A\n", encoding="utf-8")
    nb.write_text(nb.read_text(encoding="utf-8") + "\nUSER-TEXT-IN-B\n", encoding="utf-8")
    (V / ".trash").mkdir()
    (V / ".trash" / na.name).write_text("OLDER-TRASHED-NOTE of the same name\n", encoding="utf-8")
    res = rh.delete_reels([a.name, b.name], True)
    alltext = "\n".join(p.read_text(encoding="utf-8") for p in (V / ".trash").rglob("*.md"))
    env.check("both deleted notes are in .trash with their text (before: one replaced the other)",
              "USER-TEXT-IN-A" in alltext and "USER-TEXT-IN-B" in alltext, str(res))
    env.check("the older note of that name in .trash is still there", "OLDER-TRASHED-NOTE" in alltext)
    env.check("both reel folders are in _trash whole", (env.out / "_trash" / a.name / "note.md").is_file()
              and (env.out / "_trash" / b.name / "note.md").is_file())
    d = env.root / "names"; d.mkdir()
    got = []
    for _ in range(4):
        p = rh._unique(d / "note.md"); p.write_text("x", encoding="utf-8"); got.append(p.name)
    env.check("four files of one name in one second get four names", len(set(got)) == 4, str(got))

    # ---- deleted snapshots go to _trash
    c = env.make_reel("KitShot0001", "Snapshots", nshots=4)
    shots = c / "shots"
    for k in range(1, 5):                                           # four different pictures
        (shots / f"shot_{k:03d}.jpg").write_bytes(FAKE_JPEG + bytes([k]) * 8)
    first = {p.name: p.read_bytes() for p in shots.glob("*.jpg")}
    edited = b"\xff\xd8\xff\xe0EDITED-BY-ME" + b"\x00" * 40 + b"\xff\xd9"
    url = "data:image/jpeg;base64," + base64.b64encode(edited).decode()
    rh.save_edit(c.name, "shot_002.jpg", url)                       # an edited snapshot: its original is in shots/_orig
    rh.save_edit(c.name, "shot_003.jpg", url)
    before = env.tree(env.out)
    res = rh.delete_shots(c.name, ["shot_001.jpg", "shot_002.jpg"])
    bin_ = env.out / "_trash" / f"{c.name}-shots-deleted"
    env.check("the two snapshots are deleted from the video", res.get("deleted") == ["shot_001.jpg", "shot_002.jpg"]
              and not (shots / "shot_001.jpg").exists() and not (shots / "shot_002.jpg").exists(), str(res))
    env.check("they are in the reels folder's _trash, byte for byte (before: gone for good)",
              bin_.joinpath("shot_001.jpg").is_file() and bin_.joinpath("shot_001.jpg").read_bytes() == first["shot_001.jpg"]
              and bin_.joinpath("shot_002.jpg").is_file() and bin_.joinpath("shot_002.jpg").read_bytes() == edited)
    env.check("the original of the edited one is there too", bin_.joinpath("_orig", "shot_002.jpg").is_file()
              and bin_.joinpath("_orig", "shot_002.jpg").read_bytes() == first["shot_002.jpg"])
    after = env.tree(env.out)
    lost = [k for k, v in before.items() if v not in after.values() and not k.endswith((".json", ".md"))]
    env.check("no picture of the reels folder is lost by the delete", not lost, str(lost))
    env.check("shots.json no longer lists them", set(json.loads((c / "shots.json").read_text(encoding="utf-8"))) == {"shot_003.jpg", "shot_004.jpg"})
    (shots / "shot_001.jpg").write_bytes(b"\xff\xd8SECOND-OF-THAT-NAME\xff\xd9")      # e.g. after a Re-process
    rh.delete_shots(c.name, ["shot_001.jpg"])
    env.check("a second deleted snapshot of the same name is kept beside the first", len(list(bin_.glob("shot_001*.jpg"))) == 2)

    # ---- Reset to original keeps the edited picture
    rh.reset_edit(c.name, "shot_003.jpg")
    env.check("Reset to original puts the original back", (shots / "shot_003.jpg").read_bytes() == first["shot_003.jpg"]
              and not (shots / "_orig" / "shot_003.jpg").exists())
    env.check("the edited picture is in _trash (before: gone for good)", bin_.joinpath("edited", "shot_003.jpg").is_file()
              and bin_.joinpath("edited", "shot_003.jpg").read_bytes() == edited)
    listed = [x["id"] for x in rh.list_reels()]
    env.check("the bin folder is not listed as a video", listed == [c.name], str(listed))

    # ---- a favorite is still never deleted without force
    st = rh.read_state(c); st.pop("rev", None); st["favorites"] = [{"shot": "shot_004.jpg", "text": "kept", "corrected": False}]
    rh.save_state(c.name, st)
    res = rh.delete_shots(c.name, ["shot_004.jpg"])
    env.check("a favorite is not deleted without force", res.get("deleted") == [] and (shots / "shot_004.jpg").is_file(), str(res))


run("r52", body)
