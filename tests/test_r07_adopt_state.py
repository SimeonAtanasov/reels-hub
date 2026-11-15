"""Audit R-07: Get the video of a transcript that has a category and tags. The video takes over the transcript's note,
its category and its tags (README: it "keeps the category and tags you gave the transcript"), and the listing that
does this must hand the page that state. Before the fix the listing read hub.json first and took the note over
afterwards, so the page got the video with no category: it showed under Unassigned, and the first save from the page
(one star) put "no category" back, moved the note out of its category folder and took the category tag off its Tags
line, without a word."""
import json
from helpers import run


def body(env):
    rh = env.rh
    (env.out / "_categories.json").write_text(json.dumps({"categories": ["Training", "AI"], "tags": [], "homes": {}, "channels": [],
                                                           "channels_checked": True, "homes_v1": True}), encoding="utf-8")
    # a transcript you put into a category by hand, with a tag
    tf = env.make_transcript("kitchan", "KitGet0001", "Swing basics", name="Kit Channel", date="20261020")
    rh.transcript_note(tf, True)
    rh.tr_set_category("kitchan", tf.name, "Training")
    rh.set_tags({"channel": "kitchan", "id": tf.name, "tags": ["training/kettlebell"]})
    th = json.loads((tf / "hub.json").read_text(encoding="utf-8"))
    note = env.vault / th["note_path"]
    env.check("the transcript's note is in its category folder", note.is_file() and "Training" in note.parent.parts, th["note_path"])

    # Get the video: the pipeline leaves a new reel folder of the same name, with no category of its own
    reel = env.make_reel("KitGet0001", "Swing basics", date="20261020")
    rel = note.relative_to(env.vault).as_posix()

    def mine():                                    # the notes that carry this video's id marker
        return sorted(k for k, v in env.tree().items() if k.endswith(".md") and f"reels-hub:id={reel.name} ".encode() in v)

    r = next(x for x in rh.list_reels() if x["id"] == reel.name)            # what the page loads when the run ends
    disk = json.loads((reel / "hub.json").read_text(encoding="utf-8"))
    env.check("on disk the video has the transcript's category and tags",
              disk.get("category") == "Training" and disk.get("tags") == ["training/kettlebell"], str(disk))
    env.check("the listing gives the page that same category", r["state"]["category"] == "Training",
              f"the page gets {r['state']['category']!r}, hub.json has {disk.get('category')!r}")
    env.check("and the same tags", r["state"]["tags"] == ["training/kettlebell"], str(r["state"]["tags"]))
    env.check("the video took over the transcript's note, no second note",
              r["obsidian"].get("path") == th["note_path"] and mine() == [rel], str(mine()))

    # the first save from the page: one star, the rest of the state as the page got it
    st = dict(r["state"])
    st["favorites"] = [{"shot": r["shots"][0], "text": "", "corrected": False}]
    rh.save_state(reel.name, st)
    disk = json.loads((reel / "hub.json").read_text(encoding="utf-8"))
    env.check("after a star the category is still Training", disk.get("category") == "Training", str(disk.get("category")))
    env.check("the note is where it was, and there is no second one", mine() == [rel], str(mine()))
    tags = [l for l in note.read_text(encoding="utf-8").splitlines() if l.startswith("**Tags**")] if note.is_file() else []
    env.check("the note's Tags line keeps the category tag and the tag", bool(tags) and "#Training" in tags[0]
              and "#training/kettlebell" in tags[0], str(tags))
    env.check("the star is saved", len(disk.get("favorites") or []) == 1)

    # a second listing changes nothing
    r2 = next(x for x in rh.list_reels() if x["id"] == reel.name)
    env.check("the next listing says the same", r2["state"]["category"] == "Training" and len(r2["state"]["favorites"]) == 1
              and mine() == [rel])


run("r07", body)
