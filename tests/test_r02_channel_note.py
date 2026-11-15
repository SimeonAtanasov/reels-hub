"""Audit R-02: a channel's note must never be written again from scratch. Remove the channel (notes kept, as the
dialog opens), run the same channel again: your lines in the channel note stay, and the videos' notes are taken
again instead of made a second time as "... (2).md". An empty _channel.json (left by a crash) is made again
without losing the note; one that is there but cannot be read is not written over by the pipeline.
Before the fix: the channel note was replaced by a new one and every video got a twin "(2)" note."""
import json
from helpers import run


def body(env):
    rh, r2n = env.rh, env.r2n
    key, name = "kitchan", "Kit Chan"
    ids = ["KitChan0001", "KitChan0002", "KitChan0003"]

    def run_channel():                                 # what a transcripts run leaves, then the end of the run
        folders = [env.make_transcript(key, v, f"Kit video {v}", name=name) for v in ids]
        notes = [rh.transcript_note(f, True)["path"] for f in folders]
        return folders, notes, rh.channel_note(key)

    folders, notes, rel = run_channel()
    cnote = env.vault / rel
    cnote.write_text(cnote.read_text(encoding="utf-8").replace("## My notes\n", "## My notes\nUSER-CHANNEL-LINE mine\n", 1),
                     encoding="utf-8")
    vnote = env.vault / notes[0]
    vnote.write_text(vnote.read_text(encoding="utf-8") + "\nUSER-VIDEO-LINE mine\n", encoding="utf-8")

    res = rh.remove_transcripts({"channel": key, "all": True, "obsidian": False})
    env.check("remove: the whole channel is gone from the catalogue", res.get("gone") and not res.get("errors"), str(res))
    env.check("remove: the notes stay in the vault", cnote.is_file() and vnote.is_file())

    folders, notes2, rel2 = run_channel()              # Transcripts on the same channel again
    text = cnote.read_text(encoding="utf-8")
    env.check("run again: the same channel note is used", rel2 == rel, f"{rel} -> {rel2}")
    env.check("run again: your line in the channel note is kept", "USER-CHANNEL-LINE mine" in text)
    env.check("run again: the channel note lists the videos", all(f"Kit video {v}" in text for v in ids))
    env.check("run again: the videos' notes are taken again, no twin notes", notes2 == notes, f"{notes} -> {notes2}")
    env.check("run again: your line in a video note is kept", "USER-VIDEO-LINE mine" in vnote.read_text(encoding="utf-8"))
    twins = [p.name for p in cnote.parent.iterdir() if "(2)" in p.name]
    env.check("run again: no '(2)' file in the channel's folder", not twins, str(twins))

    # _channel.json emptied by a crash: the pipeline makes it again, the note is found again by its marker
    cdir = env.out / "_transcripts" / key
    cj = cdir / "_channel.json"
    cj.write_text("", encoding="utf-8")
    rec = r2n.RunRecord.__new__(r2n.RunRecord)
    rec.cdir, rec.data = cdir, {"channel": name, "uploader": name, "uploader_id": "@" + key, "channel_id": "UC" + key}
    rec.rec = {"url": f"https://www.youtube.com/@{key}/videos", "title": name, "state": "running"}
    rec.save()
    meta = json.loads(cj.read_text(encoding="utf-8"))
    env.check("empty _channel.json: made again by the pipeline", meta.get("key") == key and "run" in meta, str(meta)[:120])
    rh.CH_META.clear()
    before = cnote.read_text(encoding="utf-8")
    rel3 = rh.channel_note(key)
    env.check("empty _channel.json: the channel note is found again, your line kept",
              rel3 == rel and "USER-CHANNEL-LINE mine" in cnote.read_text(encoding="utf-8"), f"{rel3}")
    env.check("empty _channel.json: nothing else in the note changed", cnote.read_text(encoding="utf-8") == before)

    # _channel.json there but unreadable: the pipeline leaves it alone
    broken = '{"key": "kitchan", "note_path": "YouTube/Channels/Kit Chan/'
    cj.write_text(broken, encoding="utf-8")
    rec.save()
    rec.save()
    env.check("unreadable _channel.json: the pipeline does not write over it", cj.read_text(encoding="utf-8") == broken)

    # a file of yours with the channel note's name (no channel marker): never written over
    other = "kitother"
    of = env.make_transcript(other, "KitOther001", "Other video", name="Kit Other")
    rh.transcript_note(of, True)
    folder = env.vault / "YouTube" / "Channels" / "Kit Other"
    folder.mkdir(parents=True, exist_ok=True)
    mine1 = folder / "'Kit Other (channel).md"
    mine2 = folder / "'Kit Other (channel, Reels Hub).md"
    mine1.write_text("USER-OWN-NOTE one\n", encoding="utf-8")
    mine2.write_text("USER-OWN-NOTE two\n", encoding="utf-8")
    relo = rh.channel_note(other)
    env.check("your files with that name are not changed",
              mine1.read_text(encoding="utf-8") == "USER-OWN-NOTE one\n" and mine2.read_text(encoding="utf-8") == "USER-OWN-NOTE two\n")
    env.check("the channel note gets a free name", relo and relo.endswith("(channel, Reels Hub 2).md"), str(relo))


run("r02", body)
