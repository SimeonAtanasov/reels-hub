"""Audit R-04: a page with an older copy of a video saved over newer favorites, their typed text and the category
(two tabs after a restart, or a change made through the API while a tab stayed open), without a word.
Now every video has a revision, "rev" in hub.json: it goes up with every save of the state, the tags or My notes, and
whenever the category, the favorites or the tags change in another way. A save made from a copy with another
revision is refused (answer 409, "stale") and changes nothing. A page (a request with an Origin header) must send
its revision; a script that sends none is taken as before, and one that sends it is checked. The number stays on
the laptop: it is not in a share and not in the online copy.
Before the fix: there was no revision, and the older copy won."""
import json, threading, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from helpers import run

FAV = {"shot": "shot_001.jpg", "text": "", "corrected": False}


def body(env):
    rh = env.rh
    (env.out / "_categories.json").write_text(json.dumps({"categories": ["Training", "AI"], "tags": [], "homes": {}, "channels": [],
                                                           "channels_checked": True, "homes_v1": True}), encoding="utf-8")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), rh.Handler)       # the app's own request handler on a free port
    rh.PORT = srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{rh.PORT}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # never through a proxy

    def call(path, data=None, page=False):
        """(status, answer). page=True: as a browser page sends it, with its Origin header."""
        head = {"Content-Type": "application/json"} if data is not None else {}
        if page:
            head["Origin"] = base
        req = urllib.request.Request(base + path, json.dumps(data).encode("utf-8") if data is not None else None, head)
        try:
            with opener.open(req, timeout=60) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def hub(folder):
        return json.loads((folder / "hub.json").read_text(encoding="utf-8"))

    def listed(folder):
        return next(r for r in call("/api/reels")[1]["reels"] if r["id"] == folder.name)

    # ---------- two pages, one video
    a = env.make_reel("KitRevA001", "Two pages")
    note = env.note_of(a)
    env.check("a video starts without a revision: nothing is written until its next save", "rev" not in hub(a))
    tab_a = listed(a)                      # the page opened first, never reloaded
    tab_b = listed(a)                      # the second page
    env.check("the listing gives each page the video's revision (0 so far)", tab_a["state"].get("rev") == 0, str(tab_a["state"].get("rev")))

    st = dict(tab_b["state"])
    st["favorites"] = [{"shot": "shot_002.jpg", "text": "B typed this by hand, 5 x 10 reps", "corrected": True},
                       {"shot": "shot_003.jpg", "text": "", "corrected": False}]
    st["category"] = "Training"
    code, ans = call("/api/state", {"id": a.name, "state": st}, page=True)
    env.check("page B saves two favorites, a text and a category", code == 200 and hub(a)["category"] == "Training"
              and len(hub(a)["favorites"]) == 2, f"{code} {ans}")
    env.check("the save raised the revision and the answer says so", hub(a).get("rev") == 1 and ans.get("state", {}).get("rev") == 1
              and ans.get("revs") == {a.name: [0, 1]}, f"{hub(a).get('rev')} {ans.get('state', {}).get('rev')} {ans.get('revs')}")
    where = [k for k, v in env.tree().items() if f"reels-hub:id={a.name} ".encode() in v]
    before = ((a / "hub.json").read_bytes(), (a / "note.md").read_bytes())

    st_a = dict(tab_a["state"])            # page A still shows no favorites and no category: one star there
    st_a["favorites"] = [{"shot": "shot_001.jpg", "text": "", "corrected": False}]
    code, ans = call("/api/state", {"id": a.name, "state": st_a}, page=True)
    env.check("page A's save from its older copy is refused with 409", code == 409 and ans.get("stale") is True, f"{code} {ans}")
    env.check("the refusal says what to do and gives the revision on the laptop",
              "make your change again" in str(ans.get("error")) and ans.get("rev") == 1, str(ans))
    env.check("B's favorites, text and category are untouched (hub.json and note.md byte for byte)",
              ((a / "hub.json").read_bytes(), (a / "note.md").read_bytes()) == before)
    env.check("the Obsidian note did not move back",
              [k for k, v in env.tree().items() if f"reels-hub:id={a.name} ".encode() in v] == where and "Training" in where[0], str(where))

    fresh = listed(a)                      # page A reloads the video and makes its change again
    st_a = dict(fresh["state"])
    st_a["favorites"] = st_a["favorites"] + [{"shot": "shot_001.jpg", "text": "", "corrected": False}]
    code, ans = call("/api/state", {"id": a.name, "state": st_a}, page=True)
    h = hub(a)
    env.check("after the reload page A's star is saved next to B's work", code == 200 and len(h["favorites"]) == 3
              and h["favorites"][0]["text"].startswith("B typed") and h["category"] == "Training" and h["rev"] == 2, f"{code} {h}")
    env.check("there is still one note for the video, in its category folder", len(where) == 1 and not note.is_file())

    # ---------- tags and My notes go the same way
    b = env.make_reel("KitRevB002", "Tags and notes")
    old = listed(b)["state"]["rev"]
    code, ans = call("/api/tags/set", {"id": b.name, "tags": ["kit/one"], "rev": old}, page=True)
    env.check("a tag save with the right revision goes through and raises it", code == 200 and ans.get("rev") == old + 1
              and hub(b).get("tags") == ["kit/one"], f"{code} {ans}")
    code, ans = call("/api/tags/set", {"id": b.name, "tags": ["kit/two"], "rev": old}, page=True)
    env.check("a tag save from the older copy is refused, the tags stay", code == 409 and ans.get("stale") is True
              and hub(b).get("tags") == ["kit/one"], f"{code} {ans} {hub(b).get('tags')}")
    env.check("the refused tag did not join the tag list",
              "kit/two" not in json.loads((env.out / "_categories.json").read_text(encoding="utf-8"))["tags"])
    rev = hub(b)["rev"]
    code, ans = call("/api/notes", {"id": b.name, "notes": "newer notes, typed in the other tab", "rev": rev}, page=True)
    env.check("My notes with the right revision are saved and raise it", code == 200 and ans.get("rev") == rev + 1
              and "newer notes" in (b / "note.md").read_text(encoding="utf-8"), f"{code} {ans}")
    code, ans = call("/api/notes", {"id": b.name, "notes": "older notes", "rev": rev}, page=True)
    text = (b / "note.md").read_text(encoding="utf-8")
    env.check("My notes from the older copy are refused, the newer text stays", code == 409 and ans.get("stale") is True
              and "newer notes" in text and "older notes" not in text, f"{code} {ans}")
    rev = hub(b)["rev"]
    st = dict(listed(b)["state"])
    st["favorites"] = [dict(FAV)]
    code, ans = call("/api/state", {"id": b.name, "state": st, "notes": "notes sent with the state"}, page=True)
    env.check("a closing page's state and My notes in one request: both saved",
              code == 200 and len(hub(b)["favorites"]) == 1 and "notes sent with the state" in (b / "note.md").read_text(encoding="utf-8")
              and hub(b)["rev"] > rev, f"{code} {ans}")

    # ---------- pages and scripts
    c = env.make_reel("KitRevC003", "Pages and scripts")
    plain = {"category": "AI", "favorites": []}
    code, ans = call("/api/state", {"id": c.name, "state": dict(plain)}, page=True)
    env.check("a page that sends no revision (one from an older version) is refused and told to reload",
              code == 409 and ans.get("stale") is True and "older version" in str(ans.get("error")) and hub(c).get("category") == "", f"{code} {ans}")
    code, ans = call("/api/notes", {"id": c.name, "notes": "x"}, page=True)
    code2, ans2 = call("/api/tags/set", {"id": c.name, "tags": ["kit/x"]}, page=True)
    env.check("the same for its My notes and its tags", code == 409 and code2 == 409 and not hub(c).get("tags"), f"{code} {code2}")
    code, ans = call("/api/state", {"id": c.name, "state": dict(plain)})
    env.check("a script that sends no revision is taken as before", code == 200 and hub(c)["category"] == "AI", f"{code} {ans}")
    code, ans = call("/api/tags/set", {"id": c.name, "tags": ["kit/script"]})
    code2, ans2 = call("/api/notes", {"id": c.name, "notes": "from a script"})
    env.check("also its tags and My notes, and each of them raises the revision", code == 200 and code2 == 200 and hub(c)["rev"] == 3,
              f"{code} {code2} {hub(c).get('rev')}")
    st = dict(listed(c)["state"])          # a script that sends back the state it read sends its revision with it
    code, ans = call("/api/state", {"id": c.name, "state": {**st, "category": "Training"}})
    code2, ans2 = call("/api/state", {"id": c.name, "state": {**st, "category": ""}})
    env.check("a script that sends a revision is checked: its first save goes through, a second from the same copy does not",
              code == 200 and code2 == 409 and hub(c)["category"] == "Training", f"{code} {code2} {hub(c).get('category')}")
    for bad in (True, "4", 4.5, -1, [4]):
        code, ans = call("/api/state", {"id": c.name, "state": dict(plain), "rev": bad})
        if code != 409:
            break
    env.check("a revision that is not the video's number is refused, whatever it is", code == 409, f"{bad!r} -> {code}")
    code, ans = call("/api/state", {"id": c.name, "state": {"category": "", "favorites": [], "rev": 999}, "rev": hub(c)["rev"]})
    env.check("the revision next to the state counts before the one inside it", code == 200, f"{code} {ans}")

    # ---------- the number belongs to the laptop and to the file
    d = env.make_reel("KitRevD004", "Where the number lives")
    env.note_of(d)
    rh.save_state(d.name, {"category": "", "favorites": [dict(FAV)]})
    r1 = hub(d)["rev"]
    rh.write_state(d, {**rh.read_state(d), "rev": 99})
    env.check("a writer cannot set the revision: it comes from the file", hub(d)["rev"] == r1, str(hub(d)["rev"]))
    st = rh.read_state(d); st["sent"] = "2026-11-15 10:00"; rh.write_state(d, st)
    env.check("a change that is not the category, favorites or tags (last send, the note's place) leaves it alone", hub(d)["rev"] == r1)
    rh.change_cats({"action": "add", "name": "Old name"})
    rh.save_state(d.name, {"category": "Old name", "favorites": [dict(FAV)]})
    r2 = hub(d)["rev"]
    code, ans = call("/api/categories", {"action": "rename", "name": "Old name", "new": "New name"}, page=True)
    env.check("renaming its category raises it, and the page that asked gets the new number",
              code == 200 and hub(d)["category"] == "New name" and hub(d)["rev"] == r2 + 1 and ans.get("revs", {}).get(d.name) == [r2, r2 + 1],
              f"{code} {hub(d)} {ans.get('revs')}")
    code, ans = call("/api/shots/delete", {"id": d.name, "shots": ["shot_001.jpg"], "force": True}, page=True)
    env.check("deleting a starred snapshot raises it too", code == 200 and hub(d)["favorites"] == [] and hub(d)["rev"] == r2 + 2
              and ans.get("revs", {}).get(d.name) == [r2 + 1, r2 + 2], f"{code} {hub(d)} {ans.get('revs')}")
    r = rh.parse_note(d)
    meta = rh.build_share(d, r, "someone")[0]
    env.check("a share does not carry the revision", "rev" not in meta and '"rev"' not in json.dumps(meta), str(sorted(meta)))
    text, entry = rh.lib_body(d.name, d, "video", "")[:2]
    env.check("the online copy does not carry it either", "rev" not in json.loads(text) and "rev" not in entry, str(sorted(json.loads(text))))

    # ---------- a transcript's tags
    tf = env.make_transcript("kitchan", "KitRevT005", "Transcript tags")
    it = next(i for i in call("/api/transcripts")[1]["items"] if i["id"] == tf.name)
    env.check("a transcript is listed with its revision", it.get("rev") == 0, str(it.get("rev")))
    code, ans = call("/api/tags/set", {"channel": "kitchan", "id": tf.name, "tags": ["kit/a"], "rev": 0}, page=True)
    code2, ans2 = call("/api/tags/set", {"channel": "kitchan", "id": tf.name, "tags": ["kit/b"], "rev": 0}, page=True)
    env.check("its tags: saved from the current copy, refused from the older one", code == 200 and code2 == 409
              and hub(tf).get("tags") == ["kit/a"] and ans.get("revs") == {f"t/kitchan/{tf.name}": [0, 1]}, f"{code} {code2} {ans.get('revs')}")

    # ---------- an unreadable hub.json: My notes are still saved, as before (R-05)
    e = env.make_reel("KitRevE006", "Unreadable")
    (e / "hub.json").write_text('{"category": "AI", "favor', encoding="utf-8")
    cut = (e / "hub.json").read_bytes()
    code, ans = call("/api/notes", {"id": e.name, "notes": "still saved"})
    env.check("My notes of a video with an unreadable hub.json are saved, the file is left alone",
              code == 200 and "still saved" in (e / "note.md").read_text(encoding="utf-8") and (e / "hub.json").read_bytes() == cut, f"{code} {ans}")
    srv.shutdown()


run("r04", body)
