"""Audit R-01: a missing, doubled, swapped or quoted block marker must never let the app delete your text in a
vault note. Send to Obsidian (favorites and video blocks), the channel note (videos list) and the full video's
transcript (text block) change a note only between one whole start marker line and one whole end marker line;
otherwise they leave the note as it is and say why.
Before the fix: "end_missing" lost the My notes, Description and transcript sections on the second Send, and a
marker quoted in your Summary lost everything below the quote on the first Send."""
from helpers import run

FS, FE = "<!-- reels-hub:favorites start -->", "<!-- reels-hub:favorites end -->"
VS, VE = "<!-- reels-hub:video start -->", "<!-- reels-hub:video end -->"
DS, DE = "<!-- reels-hub:videos start -->", "<!-- reels-hub:videos end -->"
TS, TE = "<!-- reels-hub:text start -->", "<!-- reels-hub:text end -->"

CASES = {          # name: (how the note is changed by hand, Send should work)
    "end_missing": (lambda t: t.replace(FE + "\n", "", 1), False),
    "start_missing": (lambda t: t.replace(FS + "\n", "", 1), False),
    "both_missing": (lambda t: t.replace(FS + "\n", "", 1).replace(FE + "\n", "", 1), True),
    "reversed": (lambda t: t.replace(FS, "@@S@@").replace(FE, FS).replace("@@S@@", FE), False),
    "start_quoted_above": (lambda t: t.replace("USER-SUMMARY-LINE", f"USER-SUMMARY-LINE the app writes after `{FS}`", 1), True),
    "pair_quoted_above": (lambda t: t.replace("USER-SUMMARY-LINE", f"USER-SUMMARY-LINE markers look like `{FS}` and `{FE}`", 1), True),
    "doubled_block": (lambda t: t.rstrip("\n") + f"\n\n## My copy\n{FS}\nUSER-COPY-LINE\n{FE}\nUSER-TAIL-LINE\n", False),
    "video_end_missing": (lambda t: t.replace(VE + "\n", "", 1), False),
    "fav_text_quotes_end": (lambda t: t, True),
}


def body(env):
    rh = env.rh
    # ---------- Send to Obsidian ----------
    for i, (case, (edit, works)) in enumerate(CASES.items()):
        folder = env.make_reel(f"KitMark{i:04d}", f"Marker case {case}")
        note = env.note_of(folder)
        t = note.read_text(encoding="utf-8")
        t = t.replace("## Summary\n\n", "## Summary\n\nUSER-SUMMARY-LINE my own summary\n\n", 1)
        t = t.replace("## My notes\n\n", "## My notes\n\nUSER-MYNOTES-LINE written in Obsidian\n", 1)
        t = t.replace("# **Original text:**", "USER-CONTENT-LINE my comment\n\n# **Original text:**", 1)
        note.write_text(edit(t), encoding="utf-8")
        text = f"my text mentions {FE} here" if case == "fav_text_quotes_end" else "fav text"
        env.set_favorites(folder, [{"shot": "shot_002.jpg", "text": text, "corrected": False}])
        before = note.read_text(encoding="utf-8")
        mine = [l for l in before.splitlines() if "USER-" in l]
        att = env.vault / rh.ATTACH_DIR / folder.name
        sizes, errors = [], []
        for n in range(3):
            try:
                rh.send_to_obsidian(folder.name)
                errors.append("")
            except ValueError as e:
                errors.append(str(e))
            sizes.append(len(note.read_text(encoding="utf-8")))
        now = note.read_text(encoding="utf-8")
        lost = [l for l in mine if l not in now.splitlines()]
        env.check(f"send, {case}: none of your lines lost", not lost, f"lost {lost}")
        env.check(f"send, {case}: the note does not grow with each Send", sizes[1] == sizes[2], str(sizes))
        if works:
            env.check(f"send, {case}: Send works", not any(errors), errors[0][:120])
            env.check(f"send, {case}: one start and one end marker line", now.count("\n" + FS + "\n") == 1 and now.count("\n" + FE + "\n") == 1)
        else:
            env.check(f"send, {case}: refused with a message that names the damage", all("damaged" in e for e in errors), errors[0][:120])
            env.check(f"send, {case}: the note is byte for byte as it was", now == before)
            env.check(f"send, {case}: no image copied into the vault", not att.exists() or not any(att.iterdir()))

    # ---------- channel note: the videos list ----------
    key = "kitmarkers"
    folders = [env.make_transcript(key, f"KitChMk{k:04d}", f"Channel video {k}", name="Kit Markers") for k in (1, 2)]
    for f in folders:
        rh.transcript_note(f, True)
    rel = rh.channel_note(key)
    cnote = env.vault / rel
    good = cnote.read_text(encoding="utf-8").replace("## My notes\n", "## My notes\nUSER-CHANNEL-LINE mine\n", 1)
    cnote.write_text(good.replace(DE + "\n", "", 1), encoding="utf-8")
    before = cnote.read_text(encoding="utf-8")
    rh.channel_note(key)
    rh.channel_note(key)
    env.check("channel note, end marker missing: left as it was after two refreshes", cnote.read_text(encoding="utf-8") == before)
    try:
        rh.channel_note(key, strict=True)
        env.check("channel note, end marker missing: the run's log gets the reason", False, "no error raised")
    except rh.MarkersDamaged as e:
        env.check("channel note, end marker missing: the run's log gets the reason", "damaged" in str(e))
    cnote.write_text(good, encoding="utf-8")           # markers put back by hand
    folders.append(env.make_transcript(key, "KitChMk0003", "Channel video 3", name="Kit Markers"))
    rh.transcript_note(folders[-1], True)
    rh.channel_note(key)
    after = cnote.read_text(encoding="utf-8")
    env.check("channel note, markers put back: the list is updated and your line kept",
              "USER-CHANNEL-LINE mine" in after and after.count(DS) == 1 and after.count(DE) == 1 and "Channel video 3" in after)

    # ---------- the full video's transcript into a transcript-only note: the text block ----------
    for case in ("damaged", "intact"):
        vid = f"KitText{case[:3]}1"
        tf = env.make_transcript("kittext", vid, f"Text block {case}", name="Kit Text")
        info = rh.transcript_note(tf, True)
        tnote = env.vault / info["path"]
        t = tnote.read_text(encoding="utf-8") + "\nUSER-AFTER-TEXT-LINE mine\n"
        if case == "damaged":
            t = t.replace(TE + "\n", "", 1)
        tnote.write_text(t, encoding="utf-8")
        before = tnote.read_text(encoding="utf-8")
        full = env.make_reel(vid, f"Text block {case}", date="20261020",
                             transcript=("[00:00] Whisper words here.", "[00:04] More Whisper words."))
        rh._adopt_transcript_note(full, rh.parse_note(full))
        now = tnote.read_text(encoding="utf-8")
        if case == "damaged":
            env.check("text block, end marker missing: the note is left as it was", now == before)
        else:
            env.check("text block, markers whole: the text is replaced and your line kept",
                      "Whisper words here." in now and "USER-AFTER-TEXT-LINE mine" in now and "Second line." not in now)
            env.set_favorites(full, [{"shot": "shot_001.jpg", "text": "fav after adoption", "corrected": False}])
            for _ in range(2):
                rh.send_to_obsidian(full.name)    # a template note has no Favorites or Video block yet: both added once
            now = tnote.read_text(encoding="utf-8")
            env.check("text block: Send on the adopted note adds one Favorites and one Video block",
                      now.count(FS) == 1 and now.count(VS) == 1 and "fav after adoption" in now
                      and "USER-AFTER-TEXT-LINE mine" in now, f"{now.count(FS)} {now.count(VS)}")

    # ---------- the marker helpers themselves ----------
    env.check("a marker quoted inside a line is not a block", rh._block_span(f"a `{FS}` b\nc `{FE}` d\n", FS, FE, "x") is None)
    try:
        rh._block_span(f"{FS}\nx\n", FS, FE, "x")
        env.check("a lone start marker line is damaged", False)
    except rh.MarkersDamaged:
        env.check("a lone start marker line is damaged", True)
    env.check("marker text inside a block's content is written as &lt;", "&lt;!-- reels-hub:" in rh._defuse_block(FE))


run("r01", body)
