"""Audit R-51: three edge cases of the Tags and Category lines in a vault note.
(a) A category changed while the vault is away: the note keeps its old #Category for now, and the next change takes
    it out. Before: hub.json's cat_tag was set although the note was not written, so the old tag stayed for good.
(b) Taking a tag off removes only a token the app itself put on the Tags line. Before: also a token you had typed by
    hand, and the creator's token, when it was the same as the tag's.
(c) Only the **Category** line above the id marker is the app's. Before: the first line of that shape anywhere, so a
    line of your own further down was replaced."""
import json
from helpers import run

CATS = {"categories": ["Training", "AI", "Food"], "tags": ["ai/claude code"], "homes": {}, "channels": [],
        "channels_checked": True, "homes_v1": True}


def body(env):
    rh = env.rh
    (env.out / "_categories.json").write_text(json.dumps(CATS), encoding="utf-8")
    V = env.vault

    def hub(folder):
        return json.loads((folder / "hub.json").read_text(encoding="utf-8"))

    def line(note, start="**Tags**"):
        return next((l for l in note.read_text(encoding="utf-8").splitlines() if l.startswith(start)), "")

    def set_cat(folder, cat):
        st = rh.read_state(folder); st.pop("rev", None); st["category"] = cat
        rh.save_state(folder.name, st)

    # ---- (a) the vault is away while the category changes
    a = env.make_reel("KitCat00001", "Offline reel", hub={"category": "Training", "favorites": []})
    na = env.note_of(a)
    env.check("(set-up) the new note carries #Training", "#Training" in line(na), line(na))
    away = V.with_name("vault-away")
    env.retry(lambda: V.rename(away))
    set_cat(a, "AI")
    env.check("away: cat_tag still names what the note carries", hub(a).get("cat_tag") == "#Training" and hub(a).get("category") == "AI", str(hub(a)))
    env.retry(lambda: away.rename(V))
    set_cat(a, "Food")
    na = V / hub(a)["note_path"]
    env.check("back: no tag of an earlier category is left on the Tags line (before: #Training stayed for good)",
              "#Training" not in line(na) and "#Ai" not in line(na) and "#Food" in line(na), line(na))
    env.check("back: the Category line is right", line(na, "**Category**") == "**Category**: Food", line(na, "**Category**"))

    # ---- (b) tokens typed by hand and the creator's token stay
    b = env.make_reel("KitTag00001", "Tag reel", hub={"category": "Training", "favorites": []})
    nb = env.note_of(b)
    creator = hub(b).get("creator_tag") or ""
    env.check("(set-up) the note has the creator's token", creator.startswith("#creator/") and creator in line(nb), line(nb))
    t = nb.read_text(encoding="utf-8")
    nb.write_text(t.replace(line(nb), line(nb) + " #typed-by-me #ai/claude-code", 1), encoding="utf-8")
    rh.set_tags({"id": b.name, "tags": ["ai/claude code", "kettlebell", creator[1:]]})
    env.check("three tags set: each token is on the line once",
              all(line(nb).split().count(x) == 1 for x in ("#ai/claude-code", "#kettlebell", creator)), line(nb))
    env.check("tag_tokens holds only the token the app added", hub(b).get("tag_tokens") == ["#kettlebell"], str(hub(b).get("tag_tokens")))
    rh.set_tags({"id": b.name, "tags": ["kettlebell"]})
    env.check("a token typed by hand before the app's tag of the same name stays", "#ai/claude-code" in line(nb).split(), line(nb))
    env.check("the creator's token stays when a tag with the same token is taken off", creator in line(nb).split(), line(nb))
    env.check("another token typed by hand stays", "#typed-by-me" in line(nb).split(), line(nb))
    rh.set_tags({"id": b.name, "tags": []})
    env.check("a token the app added leaves with its tag", "#kettlebell" not in line(nb) and "tag_tokens" not in hub(b), line(nb))
    rh.set_tags({"id": b.name, "tags": ["kit/renamed soon"]})
    rh.change_tags({"action": "rename", "name": "kit/renamed soon", "new": "kit/new name"})
    env.check("a renamed tag changes its own token only",
              "#kit/new-name" in line(nb).split() and "#kit/renamed-soon" not in line(nb) and "#typed-by-me" in line(nb).split(), line(nb))
    # a video that had its tags before it had a note: the new note's tokens are the app's
    c = env.make_reel("KitTag00002", "Tags first", hub={"category": "AI", "favorites": [], "tags": ["kit/early"]})
    nc = env.note_of(c)
    rh.set_tags({"id": c.name, "tags": []})
    env.check("a tag set before the note was made leaves the new note with its tag", "#kit/early" not in line(nc), line(nc))
    set_cat(c, "Food")
    env.check("and its first category's tag leaves with the category", "#Ai" not in line(V / hub(c)["note_path"]), line(V / hub(c)["note_path"]))

    # ---- (c) a Category line of your own further down
    d = env.make_reel("KitCat00002", "Own line", hub={"category": "Training", "favorites": []})
    nd = env.note_of(d)
    mine = "**Category**: MY OWN LINE about health things"
    t = nd.read_text(encoding="utf-8")
    nd.write_text(t.replace("## My notes\n", f"## My notes\n{mine}\n", 1), encoding="utf-8")
    set_cat(d, "AI")
    nd = V / hub(d)["note_path"]
    lines = nd.read_text(encoding="utf-8").splitlines()
    env.check("the app's Category line above the id marker is changed", "**Category**: AI" in lines, str([l for l in lines if "Category" in l]))
    env.check("your own line of that shape is still there", mine in lines)
    # the app's line removed by hand: yours is the only one left, and stays
    nd.write_text(nd.read_text(encoding="utf-8").replace("**Category**: AI\n", "", 1), encoding="utf-8")
    set_cat(d, "Food")
    nd = V / hub(d)["note_path"]
    env.check("with the app's line gone, your line is not taken instead (before: it was replaced)", mine in nd.read_text(encoding="utf-8").splitlines())
    rh.send_to_obsidian(d.name)
    env.check("Send to Obsidian leaves it alone too", mine in nd.read_text(encoding="utf-8").splitlines())
    # a transcript-only note from the template has no Category line of the app's
    tf = env.make_transcript("kitchan", "KitCat00003", "Template note", name="Kit Channel")
    nt = V / rh.transcript_note(tf, True)["path"]
    nt.write_text(nt.read_text(encoding="utf-8").replace("# **Summary**:\n", f"# **Summary**:\n{mine}\n", 1), encoding="utf-8")
    rh.tr_set_category("kitchan", tf.name, "Training")
    nt = V / hub(tf)["note_path"]
    env.check("a template note: your line under Summary stays, the tag is set", mine in nt.read_text(encoding="utf-8").splitlines() and "#Training" in line(nt), line(nt))


run("r51", body)
