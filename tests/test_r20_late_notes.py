"""Audit R-20: transcripts made while the vault folder is missing (a drive not there, the folder renamed) get their
Obsidian notes once the vault is back: the Transcripts list sees the ones that never had a note and makes them in the
background, with the channel note. A channel run also says in its log that the vault was not found.
Before the fix: nothing ever made those notes (a second run skips what is here), and a channel run said nothing."""
import json, time
from helpers import run

CATS = {"categories": ["Training"], "tags": ["kit/tag"], "homes": {}, "channels": [], "channels_checked": True, "homes_v1": True}


def body(env):
    rh = env.rh
    (env.out / "_categories.json").write_text(json.dumps(CATS), encoding="utf-8")
    V = env.vault

    def hub(folder):
        p = folder / "hub.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}

    def wait():
        late = getattr(rh, "LATE", None)
        t0 = time.time()
        while late and late["running"] and time.time() - t0 < 20:
            time.sleep(0.05)

    c1 = env.make_transcript("kitlate", "KitLate0001", "Late one", name="Kit Late")
    c2 = env.make_transcript("kitlate", "KitLate0002", "Late two", name="Kit Late")
    single = env.make_transcript("kitsolo", "KitSolo0001", "A single link", name="Kit Solo")
    away = V.with_name("vault-away")
    env.retry(lambda: V.rename(away))                            # the vault is not there during the run

    job = {"log": [], "vault_warned": False}
    chans = set()
    rh._transcript_done({"folder": f"_transcripts/kitlate/{c1.name}", "from": "channel"}, chans, job)
    rh._transcript_done({"folder": f"_transcripts/kitlate/{c2.name}", "from": "channel"}, chans, job)
    said = [l for l in job["log"] if "Vault not found" in l]
    env.check("a channel run says once in its log that the vault was not found (before: nothing)", len(said) == 1, str(job["log"]))
    rh._transcript_done({"folder": f"_transcripts/kitsolo/{single.name}", "from": "link"}, set(), {"log": [], "vault_warned": False})
    env.check("where each transcript came from is kept for the later note",
              hub(c1).get("from") == "channel" and hub(single).get("from") == "link", f"{hub(c1)} {hub(single)}")
    env.check("without the vault the list makes nothing and starts no pass",
              not (rh.list_transcripts().get("late") or {}).get("running") and not away.joinpath("YouTube").exists())

    # meanwhile: a category and a tag for one of them, and one that has a note of its own already
    rh.tr_set_category("kitlate", c2.name, "Training")
    rh.set_tags({"channel": "kitlate", "id": c2.name, "tags": ["kit/tag"]})
    env.retry(lambda: away.rename(V))                            # the vault is back
    had = env.make_transcript("kitlate", "KitLate0003", "Had a note", name="Kit Late")
    gone = V / rh.transcript_note(had, True)["path"]
    env.retry(gone.unlink)                                       # deleted by hand: never made again
    while_run = dict(rh.TRJOB)
    rh.TRJOB["running"] = True
    j = rh.list_transcripts()
    wait()
    env.check("while a transcripts run goes no late pass starts", not (j.get("late") or {}).get("running") and not hub(c1).get("note_path"))
    rh.TRJOB.clear(); rh.TRJOB.update(while_run)

    j = rh.list_transcripts()
    env.check("the Transcripts list starts the pass and says so", (j.get("late") or {}).get("running") is True, str(j.get("late")))
    wait()
    p1, p2, ps = (hub(x).get("note_path") or "" for x in (c1, c2, single))
    env.check("both transcripts of the channel run have their notes now (before: never)",
              bool(p1) and bool(p2) and (V / p1).is_file() and (V / p2).is_file(), f"{p1!r} {p2!r}")
    env.check("the note of a channel run is in the channel's folder", p1.startswith("YouTube/Channels/Kit Late/"), p1)
    env.check("the single link's note is in the platform folder", bool(ps) and ps.rsplit("/", 1)[0] == "YouTube" and (V / ps).is_file(), ps)
    t2 = (V / p2).read_text(encoding="utf-8") if p2 else ""
    tags = next((l for l in t2.splitlines() if l.startswith("**Tags**")), "")
    env.check("a category and a tag given meanwhile are on the late note, in the category's folder",
              "#Training" in tags and "#kit/tag" in tags and p2.startswith("YouTube/Training/"), f"{p2} | {tags}")
    meta = json.loads((env.out / "_transcripts" / "kitlate" / "_channel.json").read_text(encoding="utf-8"))
    cn = meta.get("note_path") or ""
    env.check("the channel note is made and lists the late notes",
              bool(cn) and (V / cn).is_file() and p1[:-3] in (V / cn).read_text(encoding="utf-8"), cn)
    env.check("a note that was made and then deleted by hand is not made again", not gone.exists() and hub(had).get("note_path") == gone.relative_to(V).as_posix())
    items = {i["id"]: i for i in rh.list_transcripts()["items"]}
    env.check("the list shows the notes", all(items[x.name]["note"] for x in (c1, c2, single)) and not items[had.name]["note"])
    before = env.tree()
    rh.list_transcripts(); wait()
    env.check("another look at the list changes nothing in the vault", env.tree() == before)


run("r20", body)
