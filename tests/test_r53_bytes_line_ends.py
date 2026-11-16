"""Audit R-53: when the app changes its own lines in a vault note, everything else in the note stays byte for byte:
bytes that are not UTF-8 (a line typed in another editor) and the line ends each line has.
Before the fix: such a byte came back as the replacement character U+FFFD, and on Windows a rewrite turned every line
end of the note into CRLF (on other systems a note with CRLF lost them). Notes the app makes are written with LF, as
Obsidian's own notes; before, on Windows, with CRLF."""
import json
from helpers import run

CATS = {"categories": ["Training", "AI"], "tags": [], "homes": {}, "channels": [], "channels_checked": True, "homes_v1": True}
ODD = b"USER caf\xe9 and na\xefve typed in another editor"


def body(env):
    rh = env.rh
    (env.out / "_categories.json").write_text(json.dumps(CATS), encoding="utf-8")
    V = env.vault

    def hub(folder):
        return json.loads((folder / "hub.json").read_text(encoding="utf-8"))

    def set_cat(folder, cat):
        st = rh.read_state(folder); st.pop("rev", None); st["category"] = cat
        rh.save_state(folder.name, st)

    def other_lines(raw, skip=(b"**Tags**", b"**Category**")):
        return [l for l in raw.split(b"\n") if not l.startswith(skip)]

    # ---- a new note
    a = env.make_reel("KitByte0001", "Odd bytes", hub={"category": "Training", "favorites": []})
    na = env.note_of(a)
    env.check("a new note has LF line ends (before, on Windows: CRLF)", b"\r" not in na.read_bytes())

    # ---- bytes that are not UTF-8 survive a category change, tags, the creator pass and Send to Obsidian
    na.write_bytes(na.read_bytes().replace(b"## My notes\n", b"## My notes\n" + ODD + b"\n", 1))
    before = na.read_bytes()
    set_cat(a, "AI")
    na = V / hub(a)["note_path"]
    now = na.read_bytes()
    env.check("category change: the Tags line is changed", b"#Ai" in now and b"#Training" not in now)
    env.check("category change: your line with odd bytes is byte for byte the same (before: U+FFFD)", ODD in now.split(b"\n"))
    env.check("category change: every other line is byte for byte the same", other_lines(now) == other_lines(before))
    rh.set_tags({"id": a.name, "tags": ["kit/bytes"]})
    env.check("tags: the token is added and your line is the same", b"#kit/bytes" in na.read_bytes() and ODD in na.read_bytes().split(b"\n"))
    env.set_favorites(a, [{"shot": "shot_001.jpg", "text": "a favorite", "corrected": False}])
    rh.send_to_obsidian(a.name)
    sent = na.read_bytes()
    env.check("Send to Obsidian: the favorite is in the note and your line is the same", b"a favorite" in sent and ODD in sent.split(b"\n"))
    env.check("an LF note stays LF through all of it (before, on Windows: every line end became CRLF)", b"\r" not in sent)

    # ---- a note with CRLF line ends keeps them
    b = env.make_reel("KitByte0002", "Windows line ends", hub={"category": "Training", "favorites": []})
    nb = env.note_of(b)
    nb.write_bytes(nb.read_bytes().replace(b"## My notes\n", b"## My notes\n" + ODD + b"\n", 1).replace(b"\n", b"\r\n"))
    before = nb.read_bytes()
    set_cat(b, "AI")
    nb = V / hub(b)["note_path"]
    rh.set_tags({"id": b.name, "tags": ["kit/crlf"]})
    env.set_favorites(b, [{"shot": "shot_002.jpg", "text": "another favorite", "corrected": False}])
    rh.send_to_obsidian(b.name)
    now = nb.read_bytes()
    env.check("CRLF note: the app's changes are in", b"#Ai" in now and b"#kit/crlf" in now and b"another favorite" in now)
    env.check("CRLF note: every line still ends with CRLF (before: lost, or doubled work for the sync)",
              now.count(b"\r\n") == now.count(b"\n") and b"\r\r" not in now, f"{now.count(bytes([13, 10]))} of {now.count(bytes([10]))}")
    env.check("CRLF note: your line with odd bytes is the same", ODD + b"\r" in now.split(b"\n"))
    head = lambda raw: [l for l in raw.split(b"\r\n")[:4]]
    env.check("CRLF note: the lines above the Tags line are the same", head(now) == head(before))

    # ---- a note with both kinds: each untouched line keeps its own
    c = env.make_reel("KitByte0003", "Mixed line ends", hub={"category": "Training", "favorites": []})
    nc = env.note_of(c)
    pasted = b"PASTED line one\r\nPASTED line two\r\nPASTED line three\r\n"
    nc.write_bytes(nc.read_bytes().replace(b"## My notes\n", b"## My notes\n" + pasted, 1))
    before = nc.read_bytes()
    set_cat(c, "AI")
    nc = V / hub(c)["note_path"]
    rh.set_tags({"id": c.name, "tags": ["kit/mixed"]})
    now = nc.read_bytes()
    env.check("mixed note: the pasted CRLF lines keep CRLF and the rest keeps LF",
              pasted in now and other_lines(now) == other_lines(before) and now.count(b"\r\n") == 3, str(now.count(b"\r\n")))

    # ---- transcript-only notes and the channel note
    tfs = [env.make_transcript("kitchan", f"KitByte1{k:03d}", f"Transcript {k}", name="Kit Channel") for k in (1, 2)]
    nt = V / rh.transcript_note(tfs[0], True)["path"]
    env.check("a new transcript note has LF line ends (before, on Windows: CRLF)", b"\r" not in nt.read_bytes())
    cn = V / rh.channel_note("kitchan")
    env.check("a new channel note has LF line ends", b"\r" not in cn.read_bytes())
    cn.write_bytes(cn.read_bytes().replace(b"## My notes\n", b"## My notes\n" + ODD + b"\n", 1).replace(b"\n", b"\r\n"))
    rh.transcript_note(tfs[1], True)
    rh.channel_note("kitchan")
    now = cn.read_bytes()
    env.check("the channel note's list is rewritten with its CRLF and your odd bytes kept",
              b"Transcript 2" in now and now.count(b"\r\n") == now.count(b"\n") and ODD + b"\r" in now.split(b"\n"))

    # ---- the helper itself
    le = getattr(rh, "_line_ends", None)
    if env.check("line ends are put back by one helper (_line_ends)", le is not None):
        env.check("helper: LF stays LF", le("a\nb\n", "a\nX\nb\n") == "a\nX\nb\n")
        env.check("helper: CRLF everywhere stays CRLF, also on a new line", le("a\r\nb\r\n", "a\nX\nb\n") == "a\r\nX\r\nb\r\n")
        env.check("helper: no line end is added at the end of the note", le("a\r\nb", "a\nb\nc") == "a\r\nb\r\nc")
        env.check("helper: mixed, a changed line in the middle", le("a\nb\r\nc\r\nd\n", "a\nB\nc\nd\n") == "a\nB\r\nc\r\nd\n")
        env.check("helper: mixed, two changes with unchanged lines between them",
                  le("t\nk1\r\nk2\nk3\r\ncat\nrest\r\n", "T\nk1\nk2\nk3\nCAT\nrest\n") == "T\r\nk1\r\nk2\nk3\r\nCAT\r\nrest\r\n",
                  repr(le("t\nk1\r\nk2\nk3\r\ncat\nrest\r\n", "T\nk1\nk2\nk3\nCAT\nrest\n")))
        text, raw = rh.note_read(nb)
        env.check("helper: a note read and written back unchanged is the same file", rh._line_ends(raw, text).encode("utf-8", "surrogateescape") == nb.read_bytes())


run("r53", body)
