"""Audit R-11: renaming or deleting a category, and Settings > Move notes to category folders, ran inside one request
that held the app's data lock from start to end, and rewrote the channel's whole note after every single moved note
(874 rewrites and a minute for 873 transcripts of one channel; about half an hour at 5,100). Meanwhile no listing and
no save was answered, and closing the window in between left the category half renamed for good.
Now each of them is a pass: one video at a time under the lock, the channel note written once at the end, the page's
request answered at once with the progress in /api/status, a second such change refused until the first is done, and
<reels>/_pass.json names the pass until it is done, so the next start finishes one that was cut off.
Before the fix: the rename below rewrote the channel note once per transcript, held up a save for its whole length,
and a rename that was cut off stayed half done."""
import json, threading, time, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from helpers import run

N = 40                                       # transcripts of one channel at home in the category


def body(env):
    rh = env.rh
    new_code = hasattr(rh, "PASS")
    cats = env.out / "_categories.json"
    cats.write_text(json.dumps({"categories": ["Health", "AI"], "tags": [], "homes": {"kitbig": "Health"}, "channels": [],
                                "channels_checked": True, "homes_v1": True}), encoding="utf-8")
    trs = [env.make_transcript("kitbig", f"KitBig{i:04d}", f"Big channel video {i}", name="Kit Big Channel") for i in range(1, N + 1)]
    for f in trs:
        rh.transcript_note(f, True)
        rh._auto_category(f)                 # its channel's home: Health
    rh.channel_note("kitbig")
    reels = [env.make_reel(f"KitPass{i:03d}", f"Pass video {i}", date=f"202610{i:02d}") for i in range(1, 4)]
    for f in reels:
        env.note_of(f)
        rh.save_state(f.name, {"category": "Health", "favorites": []})
    probe = env.make_reel("KitProbe001", "Another video", date="20261020")
    env.note_of(probe)
    rh.save_state(probe.name, {"category": "AI", "favorites": []})

    def hub(folder):
        return json.loads((folder / "hub.json").read_text(encoding="utf-8"))

    def cat_list():
        return json.loads(cats.read_text(encoding="utf-8"))["categories"]

    def all_in(cat, folder_part):
        """Every one of the N transcripts and 3 videos carries the category and has its note in that folder."""
        bad = [f.name for f in trs + reels if hub(f).get("category") != cat or f"/{folder_part}/" not in "/" + hub(f).get("note_path", "")
               or not (env.vault / hub(f)["note_path"]).is_file()]
        return not bad, f"{len(bad)} not: {bad[:3]}"

    def chan_note():
        return (env.vault / json.loads((env.out / "_transcripts" / "kitbig" / "_channel.json").read_text(encoding="utf-8"))["note_path"])

    def wait_done(secs=60):
        t0 = time.time()
        while new_code and rh.PASS["state"] == "running" and time.time() - t0 < secs:
            time.sleep(0.05)
        return not new_code or rh.PASS["state"] == "done"

    env.check("start: 40 transcripts and 3 videos are in Health, their notes in the Health folder", *all_in("Health", "Health"))

    # ---------- 1. the channel note is written once, not once per transcript
    calls = []
    real_note = rh.channel_note

    def counted(key, *a, **k):
        calls.append(key)
        return real_note(key, *a, **k)
    rh.channel_note = counted
    rh.change_cats({"action": "rename", "name": "Health", "new": "Wellbeing"})       # as a script calls it: back when it is done
    rh.channel_note = real_note
    env.check("a rename rewrites the channel's note once, not once per transcript (before: 41 times for 40)",
              calls.count("kitbig") <= 2, f"{calls.count('kitbig')} times")
    env.check("after it every video and transcript carries the new name and its note is in the new folder",
              *all_in("Wellbeing", "Wellbeing"))
    text = chan_note().read_text(encoding="utf-8")
    env.check("the channel note moved along and its list links to the notes at their new place",
              "/Wellbeing/" in "/" + chan_note().relative_to(env.vault).as_posix() and text.count("Wellbeing/Kit Big Channel/") == N
              and "Health/" not in text, f"{text.count('Wellbeing/Kit Big Channel/')} links")
    env.check("the list has the new name, the channel's home too, and no pass file is left",
              cat_list() == ["Wellbeing", "AI"] and json.loads(cats.read_text(encoding="utf-8"))["homes"] == {"kitbig": "Wellbeing"}
              and not (env.out / "_pass.json").exists(), str(cat_list()))

    # ---------- 2. the page's rename: answered at once, the app stays usable, a second change waits
    real_set = rh._tr_set_category

    def slow(folder, cat, *a, **k):          # every video takes a moment, as on a big vault in OneDrive
        time.sleep(0.06)
        return real_set(folder, cat, *a, **k)
    rh._tr_set_category = slow
    box = {}

    def rename():                            # the code from before has no background: it runs in the request
        t0 = time.time()
        try:
            rh.change_cats({"action": "rename", "name": "Wellbeing", "new": "Fitness"}, background=True)
        except TypeError:
            rh.change_cats({"action": "rename", "name": "Wellbeing", "new": "Fitness"})
        box["took"] = time.time() - t0
    th = threading.Thread(target=rename)
    th.start()
    time.sleep(1.0)                          # the pass is under way (43 videos at 0.06 s each: about 2.6 s)
    env.check("the page's request is answered within a second and the pass goes on behind it",
              "took" in box and box["took"] < 0.9 and new_code and rh.PASS["state"] == "running", f"{box.get('took', 'not back yet')}")
    t0 = time.time()
    rh.save_notes(probe.name, "typed while the category was being renamed")
    took = time.time() - t0
    env.check("a save on another video does not wait for the pass (before: until the whole rename was done)", took < 0.3, f"{took:.2f} s")
    t0 = time.time()
    n = len(rh.list_reels())
    env.check("the library is listed meanwhile", n == 4 and time.time() - t0 < 1.0, f"{n} in {time.time() - t0:.2f} s")
    if new_code:
        p = rh.pass_public()
        env.check("the progress says what runs and how far it is", p["state"] == "running" and p["kind"] == "rename" and p["old"] == "Wellbeing"
                  and p["new"] == "Fitness" and p["total"] == N + 3 and 0 < p["done"] < p["total"], str(p))
        env.check("the pass is named in _pass.json in the reels folder while it runs",
                  json.loads((env.out / "_pass.json").read_text(encoding="utf-8")).get("kind") == "rename")
    else:
        env.check("the progress says what runs and how far it is", False, "no progress in this version")
        env.check("the pass is named in _pass.json in the reels folder while it runs", False)

    def refused(fn):
        try:
            fn()
            return ""
        except ValueError as e:
            return str(e)
    if new_code and rh.PASS["state"] == "running":
        r1 = refused(lambda: rh.change_cats({"action": "rename", "name": "AI", "new": "Machines"}))
        r2 = refused(lambda: rh.change_cats({"action": "delete", "name": "AI"}))
        r3 = refused(lambda: rh.sort_notes(True))
        env.check("a second rename, a delete and Move notes are refused with a message until the first is done",
                  all("still renaming the category Wellbeing to Fitness" in r for r in (r1, r2, r3)), f"{r1} | {r2} | {r3}")
        rh.change_cats({"action": "add", "name": "Added meanwhile"})
        env.check("a new category can be added meanwhile", "Added meanwhile" in cat_list() and cat_list()[0] == "Fitness", str(cat_list()))
    else:
        env.check("a second rename, a delete and Move notes are refused with a message until the first is done", False, "nothing to refuse")
        env.check("a new category can be added meanwhile", False)
    th.join()
    env.check("the pass ends by itself", wait_done(), str(new_code and rh.pass_public()))
    rh._tr_set_category = real_set
    env.check("and then everything carries the new name, with the notes in the new folder", *all_in("Fitness", "Fitness"))
    env.check("AI was left alone", hub(probe)["category"] == "AI" and "AI" in cat_list() and not (env.out / "_pass.json").exists())

    # ---------- 3. the window closed in the middle: the next start finishes it
    seen = {"n": 0}
    real_move = rh.move_note

    def cut(folder, cat, *a, **k):
        seen["n"] += 1
        if seen["n"] == 12:                  # hub.json already has the new name, the note has not moved yet
            rh.move_note = lambda *a2, **k2: (_ for _ in ()).throw(SystemExit("the window was closed"))
        if seen["n"] > 12:
            raise SystemExit("the window was closed")
        return real_set(folder, cat, *a, **k)
    rh._tr_set_category = cut
    try:
        rh.change_cats({"action": "rename", "name": "Fitness", "new": "Training"})
    except BaseException:
        pass
    rh._tr_set_category, rh.move_note = real_set, real_move
    done = [f for f in trs + reels if hub(f).get("category") == "Training"]
    half = [f for f in done if "/Fitness/" in "/" + hub(f)["note_path"]]
    env.check("cut off after 12 of 43: the list has the new name, 12 videos carry it, one of them with its note still in the old folder",
              cat_list()[0] == "Training" and len(done) == 12 and len(half) == 1, f"{len(done)} done, {len(half)} half")
    if new_code:
        env.check("_pass.json is still there: it names what was going on", json.loads((env.out / "_pass.json").read_text(encoding="utf-8"))
                  == {**json.loads((env.out / "_pass.json").read_text(encoding="utf-8")), "kind": "rename", "old": "Fitness", "new": "Training"})
        rh.PASS.update(state="", kind="", old="", new="", done=0, total=0, error="", resumed=False, result=None)   # a new start
        rh.resume_pass()
        env.check("the next start goes on with it", wait_done() and rh.PASS["resumed"] is True, str(rh.pass_public()))
    else:
        env.check("_pass.json is still there: it names what was going on", False, "this version keeps no note of it")
        env.check("the next start goes on with it", False, "the 31 others stay in a category that no longer exists")
    env.check("after it all 43 are in Training with their notes in the Training folder, the half-done one too",
              *all_in("Training", "Training"))
    env.check("and the pass file is gone", not (env.out / "_pass.json").exists())

    if new_code:
        # cut off in the other instant: a note is already in the new folder, hub.json still names the old place
        moved = {"n": 0}
        real_file = rh._move_file

        def cut_move(src, dst):
            moved["n"] += 1
            real_file(src, dst)
            if moved["n"] == 7:
                raise SystemExit("the window was closed")
        rh._move_file = cut_move
        try:
            rh.change_cats({"action": "rename", "name": "Training", "new": "Coaching"})
        except BaseException:
            pass
        rh._move_file = real_file
        lost = [f for f in trs + reels if not (env.vault / hub(f)["note_path"]).is_file()]
        env.check("cut off right after a note was moved: one video's hub.json names a place where the note no longer is",
                  len(lost) == 1 and hub(lost[0])["category"] == "Coaching", f"{len(lost)} such")
        rh.PASS.update(state="", kind="")
        rh.resume_pass()
        ok = wait_done()
        env.check("the next start finds that note in the new folder and writes its place down; all 43 are in Coaching",
                  ok and all_in("Coaching", "Coaching")[0], all_in("Coaching", "Coaching")[1])

        # the app closed before the list was changed: the next start does that step too
        (env.out / "_pass.json").write_text(json.dumps({"kind": "delete", "old": "Coaching", "new": ""}), encoding="utf-8")
        rh.PASS.update(state="", kind="")
        rh.resume_pass()
        ok = wait_done()
        gone = [f.name for f in trs + reels if hub(f).get("category")]
        env.check("a delete that was cut off before its first step is done in full at the next start: the category is gone "
                  "from the list and its videos are Unassigned", ok and "Coaching" not in cat_list() and not gone, f"{cat_list()} {gone[:3]}")
        (env.out / "_pass.json").write_text("{ not json", encoding="utf-8")
        rh.PASS.update(state="", kind="")
        env.check("a pass file that can't be read is said and set aside, the app starts", rh.resume_pass() is None
                  and not (env.out / "_pass.json").exists() and rh.PASS["state"] == "")

    # ---------- 4. Move notes to category folders: the same, with the count in its answer
    rh.change_cats({"action": "add", "name": "Travel"})
    for f in trs[:5] + reels[:1]:            # as an older version left them: the category set, the note not moved
        h = hub(f); h["category"] = "Travel"
        (f / "hub.json").write_text(json.dumps(h), encoding="utf-8")
    dry = rh.sort_notes(True)
    env.check("Move notes counts the 6 notes that are not in their category's folder", dry["count"] == 6, str(dry))
    calls.clear()
    rh.channel_note = counted
    res = rh.sort_notes()
    rh.channel_note = real_note
    env.check("it moves them and says how many", res.get("count") == 6 and all("/Travel/" in "/" + hub(f)["note_path"]
              and (env.vault / hub(f)["note_path"]).is_file() for f in trs[:5] + reels[:1]), str(res))
    env.check("and rewrites the channel's note once (before: once per moved note)", calls.count("kitbig") == 1, f"{calls.count('kitbig')} times")
    env.check("the channel note links to the moved notes at their new place",
              chan_note().read_text(encoding="utf-8").count("Travel/") == 5, str(chan_note().read_text(encoding="utf-8").count("Travel/")))
    if new_code:
        for f in trs[10:13]:                 # three more notes out of place, and Move notes cut off after the first file move
            h = hub(f); h["category"] = "AI"
            (f / "hub.json").write_text(json.dumps(h), encoding="utf-8")
        real_file, n = rh._move_file, {"n": 0}

        def cut_once(src, dst):
            n["n"] += 1
            real_file(src, dst)
            if n["n"] == 1:
                raise SystemExit("the window was closed")
        rh._move_file = cut_once
        try:
            rh.sort_notes()
        except BaseException:
            pass
        rh._move_file = real_file
        lost = [f.name for f in trs[10:13] if not (env.vault / hub(f)["note_path"]).is_file()]
        rh.PASS.update(state="", kind="")
        rh.resume_pass()
        ok = wait_done()
        env.check("Move notes cut off right after a note was moved: the next start finishes it, and that note's place is written down",
                  len(lost) == 1 and ok and all("/AI/" in "/" + hub(f)["note_path"] and (env.vault / hub(f)["note_path"]).is_file()
                                                for f in trs[10:13]) and not (env.out / "_pass.json").exists(), f"{lost} {rh.pass_public()}")

    # ---------- 5. through the app's own request handler, as the page sends it: a small category is done at once
    srv = ThreadingHTTPServer(("127.0.0.1", 0), rh.Handler)
    rh.PORT = srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{rh.PORT}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # never through a proxy

    def call(path, data=None):
        head = {"Content-Type": "application/json", "Origin": base} if data is not None else {}
        req = urllib.request.Request(base + path, json.dumps(data).encode("utf-8") if data is not None else None, head)
        try:
            with opener.open(req, timeout=60) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))
    v = reels[0]                             # in Travel since part 4, with 5 transcripts
    rev = hub(v).get("rev", 0)
    code, ans = call("/api/categories", {"action": "rename", "name": "Travel", "new": "Trips", "background": True})
    p = ans.get("pass") or {}
    env.check("the page's rename of a small category: the answer says the pass is done and names how many videos it changed",
              code == 200 and p.get("state") == "done" and p.get("kind") == "rename" and (p.get("result") or {}).get("count") == 6
              and ans.get("categories", [None])[-1] == "Trips", f"{code} {str(ans)[:300]}")
    env.check("and it carries the new revision of each video, so the page's copies stay current (a star right after is not refused)",
              hub(v).get("rev") == rev + 1 and (ans.get("revs") or {}).get(v.name) == [rev, rev + 1], f"{hub(v).get('rev')} {ans.get('revs')}")
    code, st = call("/api/status")
    env.check("/api/status carries the pass for the page's status line", code == 200 and (st.get("pass") or {}).get("state") == "done", str(st.get("pass")))
    srv.shutdown()


run("r11", body)
