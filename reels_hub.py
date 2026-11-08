#!/usr/bin/env python3
"""
reels_hub.py - local web app for reels2notes.py

Keep this file next to reels2notes.py (e.g. in GitHub/reels-hub) and run:
    python reels_hub.py
Downloaded reels are stored in Downloads/reels (change with --out).
It opens http://127.0.0.1:8765 in your browser.

- Paste Instagram, TikTok, YouTube (incl. Shorts) or X links, one per line, and press Run: reels2notes.py
  processes them, with a live log. Press Run again while one is running: the new links wait in line
  (Waiting list, x to take one out) and start by themselves, one run at a time
- If a site needs login (often X), put a cookies.txt next to this file: Run uses it automatically
- Categories in the sidebar: create your own (e.g. Training), assign reels by dropdown or drag;
  reels without a category stay under Unassigned at the bottom
- Per reel: video, caption, transcript (click a line to jump there), on-screen text, snapshots, notes
- Star snapshots as favorites, drag to reorder, edit the text under each one (marked "Corrected text"),
  and print them on A4 left to right, top to bottom
- Prev / Next buttons or the arrow keys move between reels; the search box filters the list

Data: favorites and category live in reels/<reel>/hub.json, categories in reels/_categories.json,
and favorites are also written into each note.md (## Favorites) for Obsidian.

Obsidian: each reel gets a note in <vault>/Instagram, /TikTok, /YouTube or /X named after the reel
(caption, transcript); "Send to Obsidian" copies the video and the starred images to 910_Attachments/ReelsHub and
writes them with their text; the video plays at the top of the note. Notes can be moved or renamed in the vault; the app finds them by their id line.

Sharing (optional): "Share with <name>" sends the link, your favorites (times, text, the images you
edited), My notes and the category to a private Cloudflare inbox (the cloud/ folder). The other laptop
picks it up, shows it in its Inbox, and Add downloads and processes the video there. Settings holds the
inbox address and this laptop's service token (in the ReelsHub folder in %APPDATA%, never here).

Options:  --port 8765   --out PATH   --vault PATH   --no-browser
Uses only the Python standard library. Listens on 127.0.0.1 only (not reachable from other devices).
"""
import argparse, base64, itertools, json, mimetypes, os, re, shutil, subprocess, sys, tempfile, threading, time, webbrowser
import urllib.error, urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, unquote

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "reels2notes.py"
OUT = Path.home() / "Downloads" / "reels"   # downloaded videos, snapshots and notes
VAULT = Path.home() / "OneDrive" / "Documents" / "Obsidian Vault"   # Obsidian vault
CONFIG = {}                                   # this laptop's settings (who you are, folders); see config_path()
OVERRIDES = {}                                # --out / --vault given on the command line win over the settings


# ---------------------------------------------------------------- settings (one file per laptop/user)
def config_path():
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "ReelsHub" / "config.json"


def default_config():
    vault = Path.home() / "OneDrive" / "Documents" / "Obsidian Vault"
    if not vault.is_dir():
        vault = Path.home() / "Documents" / "Obsidian Vault"
    return {"name": os.environ.get("USERNAME", "").title() if os.environ.get("USERNAME") else "",
            "out": str(Path.home() / "Downloads" / "reels"),
            "vault": str(vault) if vault.is_dir() else ""}


def person_id(name):
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:40] or "me"


def load_config():
    try:
        c = json.loads(config_path().read_text(encoding="utf-8"))
        return c if isinstance(c, dict) and c.get("name") and c.get("out") else {}
    except (OSError, ValueError):
        return {}


def apply_config(cfg):
    """Use the settings (command-line --out / --vault still win)."""
    global OUT, VAULT, CONFIG
    CONFIG = dict(cfg)
    OUT = Path(OVERRIDES.get("out") or cfg.get("out") or OUT).expanduser().resolve()
    v = OVERRIDES.get("vault") if "vault" in OVERRIDES else cfg.get("vault", "")
    VAULT = Path(v).expanduser() if v else Path("/nonexistent-vault-disabled")
    OUT.mkdir(parents=True, exist_ok=True)


def save_config(body):
    """Validate and store the settings from the setup screen."""
    if JOB["running"] or RUNQ:
        raise ValueError("Wait for the current run (and the ones waiting) to finish")
    name = str(body.get("name") or "").strip()[:60]
    if not name:
        raise ValueError("Please enter your name")
    out = str(body.get("out") or "").strip().strip('"')
    if not out:
        raise ValueError("Please enter a folder for your reels")
    out_p = Path(out).expanduser()
    if not out_p.is_absolute():
        raise ValueError("The reels folder must be a full path, e.g. C:\\Users\\you\\Downloads\\reels")
    try:
        out_p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError(f"Can't create the reels folder: {e}")
    vault = str(body.get("vault") or "").strip().strip('"')
    warn = ""
    if vault:
        vp = Path(vault).expanduser()
        if not vp.is_dir():
            raise ValueError("That Obsidian vault folder doesn't exist - check the path, or leave it empty")
        if not (vp / ".obsidian").is_dir():
            warn = "Note: that folder has no .obsidian folder - make sure it's the vault itself"
    with CONFIG_LOCK:
        old = load_config() or {}
        cloud, changed = cloud_from_form(body, old.get("cloud") or {})
        sub_lang = str(body.get("sub_lang", old.get("sub_lang", "en")) or "").strip().lower()
        if sub_lang and not re.fullmatch(r"[a-z]{2,3}(-[a-z0-9]{2,8})?", sub_lang):
            raise ValueError("Transcript language: a language code like en, es or bg, or empty")
        cfg = {"name": name, "person": person_id(name), "out": str(out_p.resolve()), "vault": vault,
               "sub_lang": sub_lang, "saved": time.strftime("%Y-%m-%d %H:%M")}
        if "can_delete" in old:
            cfg["can_delete"] = old["can_delete"]        # set by the Cloudflare inbox (PEOPLE), not by this form
        if cloud:
            cfg["cloud"] = cloud
        p = config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(p, json.dumps(cfg, indent=1, ensure_ascii=False))
        apply_config(cfg)
    MISS.clear()
    test = None
    if cloud and (changed or not cloud.get("person")):  # new address or token: ask the inbox who this laptop is
        try:
            remember_cloud(cloud_whoami())
            test = {"ok": True}
        except CloudError as e:
            test = {"ok": False, "error": str(e)}
    with INBOX_LOCK:
        INBOX.update(error="", whoami_at=time.time() if test and test["ok"] else 0, force=bool(cloud))
    INBOX_WAKE.set()
    return {"config": public_config(), "warning": warn, "cloud_test": test}


def cloud_from_form(body, old):
    """Sharing settings from the Settings form: inbox address, Client ID, Client Secret.
    An empty secret keeps the saved one; all three empty switches sharing off."""
    url = str(body.get("cloud_url") or "").strip().strip('"')
    cid = str(body.get("cloud_id") or "").strip().strip('"')
    sec = str(body.get("cloud_secret") or "").strip().strip('"')
    pasted = f"{cid}\n{sec}"                      # the dashboard's "CF-Access-Client-Id: ..." lines work too
    m = re.search(r"(?im)cf-access-client-id\s*:\s*(\S+)", pasted)
    cid = m.group(1) if m else cid
    m = re.search(r"(?im)cf-access-client-secret\s*:\s*(\S+)", pasted)
    sec = m.group(1) if m else sec
    if not (url or cid or sec):
        return None, bool(old)
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    u = urlparse(url)
    if not cloud_url_ok(url) or "@" in u.netloc:
        raise ValueError("The Cloudflare address must start with https://, for example https://name.pages.dev")
    url = f"{u.scheme}://{u.netloc}"
    if not cid:
        raise ValueError("Please enter this laptop's Client ID (from the service token in Cloudflare)")
    if not sec and cid == old.get("id"):
        sec = old.get("secret", "")
    if not sec:
        raise ValueError("Please enter this laptop's Client Secret (from the service token in Cloudflare)")
    if len(cid) > 200 or len(sec) > 500 or re.search(r"\s", cid + sec):
        raise ValueError("The Client ID or Client Secret contains spaces: copy it again from Cloudflare")
    new = {"url": url, "id": cid, "secret": sec}
    changed = (url, cid, sec) != (old.get("url"), old.get("id"), old.get("secret"))
    if not changed:                               # keep what the inbox said last time
        new.update({k: old[k] for k in ("person", "name", "people", "checked") if k in old})
    return new, changed


def public_config():
    """The settings for the page: everything except the Client Secret."""
    c = {k: v for k, v in CONFIG.items() if k != "cloud"}
    cl = CONFIG.get("cloud")
    if isinstance(cl, dict):
        c["cloud"] = {**{k: v for k, v in cl.items() if k != "secret"}, "has_secret": bool(cl.get("secret"))}
    return c


def config_info():
    return {"configured": bool(CONFIG.get("name")), "config": public_config(), "defaults": default_config(),
            "path": str(config_path()), "effective": {"out": str(OUT), "vault": str(VAULT) if vault_ok() else ""},
            "overrides": sorted(OVERRIDES)}


def can_delete():
    """Deleting videos is off for a person whose PEOPLE entry in the Cloudflare inbox says "delete": false
    (remembered in config.json, so it stays off while offline or without a token)."""
    return CONFIG.get("can_delete") is not False
PLATFORM_DIRS = ("Instagram", "TikTok", "YouTube", "X")             # one vault folder per platform
FAV_START, FAV_END = "<!-- reels-hub:favorites start -->", "<!-- reels-hub:favorites end -->"
VID_START, VID_END = "<!-- reels-hub:video start -->", "<!-- reels-hub:video end -->"
TXT_START, TXT_END = "<!-- reels-hub:text start -->", "<!-- reels-hub:text end -->"   # a transcript-only note's text
VIDS_START, VIDS_END = "<!-- reels-hub:videos start -->", "<!-- reels-hub:videos end -->"   # a channel note's list
TR_DIR = "_transcripts"                  # transcript-only videos, one folder per channel (see reels2notes --transcripts)
TR_VIDEO_LINE = "_Video not downloaded: paste the link in Reels Hub and press Run to add it._"
def tr_source_label(source):
    """'youtube captions (automatic)' -> 'YouTube captions (automatic)', 'tiktok captions' -> 'TikTok captions',
    'whisper' -> 'Whisper'."""
    src = str(source or "").strip().lower()
    if not src:
        return "captions"
    if src == "whisper":
        return "Whisper"
    names = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram", "x": "X"}
    site = src.split(" ")[0]
    rest = src[len(site):].strip()
    return f"{names.get(site, site.title())} {rest}".strip() if rest else names.get(site, site.title())
TR_CACHE = {}                            # transcript folder -> (note mtime, hub mtime, item) for the catalogue
TR_CONTROL = "_control.json"             # in _transcripts: {"pause", "stop"} for the transcripts run (reels2notes reads it)
TEMPLATE_FILES = {"YouTube": "'YouTube Template.md", "TikTok": "`TikTok Template.md"}   # in the vault's Templates folder
TEMPLATE_FALLBACK = {                    # copies of those templates (2026-11-05), used when the vault has none
    "YouTube": "{{date}}\n{{time}}\n**Title:** \n**Source**:\n**Tags**: #YouTube \n"
               "**Related**: [[900_Meta/MOCs/YouTube_MOC|YouTube_MOC]] [[900_Meta/MOCs/YouTube_MOC.canvas|YouTube_MOC]]\n\n"
               "Formula to remove timestamp: =RIGHT(A1,LEN(A1)-13)\n\n# **Summary**:\n\n"
               "# **What was the most memorable thing / Main Takeaways?**\n\n# **Key Concepts and Themes:**\n\n"
               "# **Reflective Questions:**\n\n# **Actionable Insights:**\n\n# **Compare and Contrast:**\n\n"
               "# **Content**:\n\n# **Original text:**\n",
    "TikTok": "{{date}}\n{{time}}\n**Source**:\n**Tags**: #TikTok\n**Related**: [[TikTok_MOC]]\n**Creator:** \n**Description:**\n\n"
              "Formula to remove timestamp: =RIGHT(A1,LEN(A1)-13)\n\n# **Summary**:\n\n"
              "# **What was the most memorable thing / Main Takeaways?**\n\n# **Key Concepts and Themes:**\n\n"
              "# **Reflective Questions:**\n\n# **Actionable Insights:**\n\n# **Compare and Contrast:**\n\n"
              "# **Content**:\n\n# **Original text:**\n"}

JOB = {"running": False, "log": [], "started": None, "finished": None, "urls": [], "progress": None, "busy": set(),
       "share": None, "mark": 0, "done": 0}   # share: id of the inbox item being added; mark: where this run's lines
                                         # start in log (it runs on across queued runs); done: links finished in this run
JOB_LOCK = threading.Lock()              # the run slot: one run at a time (taken after INBOX_LOCK, never before it)
RUNQ = []                                # Runs pressed while another run was going wait here (guarded by INBOX_LOCK)
QSEQ = itertools.count(1)                # one order for all that waits (Runs and inbox Adds): first come, first served
LOG_KEEP = 1500                          # lines of the earlier runs kept when the next run in line starts
DONE_LINE = re.compile(r"\s*done( in [^>]*)? ->")   # reels2notes' line for a finished link
DATA_LOCK = threading.RLock()            # re-entrant: helpers that already hold it can call each other
PORT = 8765
NOTES_S, NOTES_E = "<!-- reels-hub:notes start -->", "<!-- reels-hub:notes end -->"
LFAV_S, LFAV_E = "<!-- reels-hub:local-favorites start -->", "<!-- reels-hub:local-favorites end -->"
VIDEO_EXT = (".mp4", ".webm", ".mkv", ".mov", ".m4v")
CONFIG_LOCK = threading.RLock()          # config.json is written by Settings and by the inbox worker

# Sharing through the Cloudflare inbox (see the "sharing" section)
SHARE_IMG_MAX = 1_300_000                # bytes per edited image sent (the inbox takes about 1.35 MB)
SHARE_FAVS_MAX, SHARE_IMGS_MAX = 200, 40  # per video: favorites, edited images (the inbox's limits)
PLATFORMS = ("Instagram", "TikTok", "YouTube", "X", "Web")
PICKUP_EVERY = 90                        # seconds between inbox checks while the app runs
WHOAMI_EVERY = 1800                      # how often to ask the inbox again who this laptop is
SHARE_ID_RE = re.compile(r"[A-Za-z0-9-]{8,64}")
INBOX = {"checked": "", "error": "", "busy": False, "queue": [], "adding": None, "errors": {}, "force": True,
         "whoami_at": 0.0, "last": None, "partial": {}}   # partial: share id -> reel downloaded but not filled in
INBOX_LOCK = threading.RLock()           # also guards RUNQ: both lines of waiting work change only under it
INBOX_WAKE = threading.Event()
CHANGED = {"seq": 0, "list": []}         # reels an inbox Add filled in [{seq, folder}]: the page reloads them
BOOT = os.urandom(6).hex()               # a new count each start: an open page notices the restart


def mark_changed(name):
    with INBOX_LOCK:
        CHANGED["seq"] += 1
        CHANGED["list"] = CHANGED["list"][-49:] + [{"seq": CHANGED["seq"], "folder": name}]


def busy_check(rid):
    """Refuse changes to a reel while a run is replacing its transcript and snapshots (or filling in a share)."""
    if JOB["running"] and rid in JOB.get("busy", ()):
        raise ValueError("This video is being processed - wait until the run finishes")


def atomic_write(path, text):
    """Write a file so readers never see it half-written (temp file + rename, retried while Windows has it open)."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for i in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)
    try:
        tmp.unlink()
    except OSError:
        pass
    raise PermissionError(f"could not write {path.name} (open in another program?)")


def read_text(path):
    return Path(path).read_text(encoding="utf-8", errors="replace")


def reel_dir(rid):
    """A reel folder from an id sent by the page: a plain folder name directly inside the reels folder."""
    if not isinstance(rid, str) or not re.fullmatch(r"[^/\\:*?\"<>|\x00-\x1f]{1,200}", rid) \
            or rid.startswith((".", "_")) or rid.strip() != rid:
        raise PermissionError("bad reel id")
    folder = OUT / rid
    if folder.parent.resolve() != OUT.resolve() or not folder.is_dir():
        raise ValueError("unknown reel")
    return folder


# ---------------------------------------------------------------- note.md parsing
def parse_note(folder):
    note = folder / "note.md"
    if not note.exists():
        return None
    text = read_text(note)
    notes_block = None
    if NOTES_S in text and NOTES_E in text and text.find(NOTES_S) < text.find(NOTES_E):
        a, b = text.find(NOTES_S), text.find(NOTES_E)
        notes_block = text[a + len(NOTES_S):b].strip("\n")
        text = text[:a] + text[b + len(NOTES_E):]           # user text never breaks the section parsing
    if LFAV_S in text and LFAV_E in text and text.find(LFAV_S) < text.find(LFAV_E):
        text = text[:text.find(LFAV_S)] + text[text.find(LFAV_E) + len(LFAV_E):]
    meta, body = {}, text
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = m.group(2)

    title, sections, cur = "", {}, None
    for line in body.splitlines():
        if line.startswith("# ") and not title:
            title = line[2:].strip(); continue
        if line.startswith("## "):
            cur = line[3:].strip(); sections[cur] = []; continue
        if cur is not None:
            sections[cur].append(line)

    def sec(prefix):
        for k, v in sections.items():
            if k.startswith(prefix):
                return k, "\n".join(v).strip()
        return None, ""

    _, caption = sec("Caption")
    tkey, tr = sec("Transcript")
    lang = re.search(r"\((.*?)\)", tkey or "")
    transcript = []
    for line in tr.splitlines():
        mm = re.match(r"\[(\d+):(\d+)\]\s*(.*)", line)
        if mm:
            transcript.append({"t": int(mm.group(1)) * 60 + int(mm.group(2)), "text": mm.group(3)})
    _, ocr_txt = sec("On-screen text")
    ocr = []
    for line in ocr_txt.splitlines():
        mm = re.match(r"-\s*`(.*?)`:\s*(.*)", line)
        if mm and mm.group(2) != "_none_":
            ocr.append({"shot": mm.group(1), "text": mm.group(2)})
    _, notes = sec("My notes")
    if notes_block is not None:
        notes = notes_block
    if notes.strip() == "-":
        notes = ""

    shots = sorted(p.name for p in (folder / "shots").glob("*.jpg")) if (folder / "shots").exists() else []
    videos = [p.name for p in folder.glob("video.*") if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov", ".m4v")]
    url = meta.get("source", "")
    platform = meta.get("platform") or platform_of(url)
    if caption == "_none_":
        caption = ""
    # Instagram titles are "Video by ...", X titles are "Name - truncated post": use the caption instead
    generic = (not title or title.startswith("Video by") or title.startswith("Post by")
               or title in folder.name or platform == "X")
    label = ((caption.splitlines()[0] if caption else title) if generic else title)[:80]
    times = shot_times(folder, shots)
    shots = sorted(shots, key=lambda x: (times[x], x))       # time order (manual snapshots slot in between)
    return {
        "shots": shots,
        "id": folder.name, "title": title, "label": label or folder.name, "platform": platform,
        "version": int(re.search(r"_v(\d+)$", folder.name).group(1)) if re.search(r"_v(\d+)$", folder.name) else 1,
        "url": meta.get("source", ""), "author": meta.get("author", ""),
        "date": meta.get("date", ""), "duration": meta.get("duration_s", ""),
        "caption": caption, "lang": lang.group(1) if lang else "",
        "tsource": meta.get("transcript", ""),              # transcript-only videos: youtube captions / whisper
        "transcript": transcript, "ocr": ocr, "notes": notes,
        "times": times,
        "manual": manual_shots(folder),
        "mt": {s: int((folder / "shots" / s).stat().st_mtime) for s in shots},
        "edited": sorted(p.name for p in (folder / "shots" / "_orig").glob("*.jpg")) if (folder / "shots" / "_orig").exists() else [],
        "video": videos[0] if videos else None,
        "slides": read_slides(folder),
        "added": _added(folder),
        "owner": meta.get("owner", "") or CONFIG.get("person", ""),
        "state": read_state(folder),
    }


def _added(folder):
    """When the reel was processed (creation time of its note)."""
    try:
        st = (folder / "note.md").stat()
        return int(getattr(st, "st_birthtime", 0) or st.st_ctime)
    except OSError:
        return 0


def manual_shots(folder):
    try:
        return json.loads((folder / "shots_manual.json").read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return []


def capture_shot(reel_id, t):
    """Take a snapshot at time t (seconds) from the downloaded video, while the user watches."""
    folder = reel_dir(reel_id)
    if not (folder / "note.md").exists():
        raise ValueError("unknown reel")
    busy_check(folder.name)
    return _capture(folder, t)


def _capture(folder, t, manual=True):
    """Grab the frame at t into shots/ (or reuse a snapshot already within 0.05 s of it).
    manual=False: a favorite re-captured from a share, not marked as taken by hand."""
    vids = [p for p in folder.glob("video.*") if p.suffix.lower() in VIDEO_EXT]
    if not vids:
        raise ValueError("no video file")
    t = max(0.0, float(t))
    shots_dir = folder / "shots"
    shots_dir.mkdir(exist_ok=True)
    with DATA_LOCK:
        names = [p.name for p in shots_dir.glob("shot_*.jpg")]
        times = shot_times(folder, sorted(names))
        for n, tt in times.items():                       # same moment already captured
            if abs(tt - t) < 0.05:
                return {"shot": n, "t": tt, "mt": int((shots_dir / n).stat().st_mtime), "existing": True}
        nums = [int(m.group(1)) for n in names for m in [re.match(r"shot_(\d+)\.jpg$", n)] if m]
        name = f"shot_{(max(nums) if nums else 0) + 1:03d}.jpg"
        out = shots_dir / name
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(vids[0]),
                            "-frames:v", "1", "-vf", "scale=720:-2", "-q:v", "3", str(out)],
                           capture_output=True, text=True)
        if not out.exists():
            raise ValueError("ffmpeg could not grab that frame: " + (r.stderr or "")[-300:])
        sj = folder / "shots.json"
        try:
            d = json.loads(sj.read_text(encoding="utf-8", errors="replace")) if sj.exists() else {}
        except ValueError:
            d = {}
        d[name] = round(t, 3)
        atomic_write(sj, json.dumps(d, indent=1))
        if manual:
            man = manual_shots(folder) + [name]
            atomic_write((folder / "shots_manual.json"), json.dumps(man))
        note = folder / "note.md"                        # list it in the note's Snapshots section too
        lines = note.read_text(encoding="utf-8", errors="replace").split("\n")
        emb = [i for i, l in enumerate(lines) if l.startswith(f"![[{folder.name}/shots/")]
        if emb:
            lines.insert(emb[-1] + 1, f"![[{folder.name}/shots/{name}]]")
            atomic_write(note, "\n".join(lines))
    return {"shot": name, "t": round(t, 3), "mt": int(out.stat().st_mtime), "existing": False}


def shot_times(folder, shots):
    """Seconds into the video for each snapshot (shots.json from reels2notes, else every 2 s)."""
    saved = {}
    f = folder / "shots.json"
    if f.exists():
        try:
            saved = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            saved = {}
    return {s: float(saved.get(s, i * 2)) for i, s in enumerate(shots)}


def read_state(folder):
    s, bad = {}, False
    f = folder / "hub.json"
    if f.exists():
        try:
            s = json.loads(read_text(f))
            if not isinstance(s, dict):
                s, bad = {}, True
        except (ValueError, OSError):
            s, bad = {}, True                     # unreadable: never treat it as "no note yet"
    out = {"category": s.get("category", "") or "", "favorites": s.get("favorites", []) or [],
           "note_name": s.get("note_name", "") or "", "note_path": s.get("note_path", "") or "", "sent": s.get("sent", "") or "",
           # sharing: who shared this video with you {person, name, at}; whom you shared it with [{to, name, at}]
           "shared_by": s["shared_by"] if isinstance(s.get("shared_by"), dict) else None,
           "shared": [x for x in s["shared"] if isinstance(x, dict)][-10:] if isinstance(s.get("shared"), list) else [],
           "tags": _clean_tags(s.get("tags")), "tag_tokens": [str(t) for t in s.get("tag_tokens") or [] if isinstance(t, str)],
           "cat_tag": str(s.get("cat_tag") or "")}
    if bad:
        out["_bad"] = True
    return out


def write_state(folder, state):
    state = {k: v for k, v in state.items() if not k.startswith("_") and not (k in ("shared_by", "shared", "tags", "tag_tokens", "cat_tag") and not v)}
    atomic_write(folder / "hub.json", json.dumps(state, indent=1, ensure_ascii=False))


def list_reels():
    if not OUT.exists():
        return []
    items = []
    for f in OUT.iterdir():
        if not f.is_dir() or f.name.startswith(("_", ".")):
            continue
        try:
            r = parse_note(f)
        except Exception as e:                    # one broken folder must not hide the whole library
            print(f"  skipped {f.name}: {e}")
            continue
        if not r:
            continue
        try:
            r["obsidian"] = ensure_vault_note(f, r)
        except Exception as e:
            r["obsidian"] = {"error": f"Obsidian: {e}"}
        items.append(r)
    return sorted(items, key=lambda r: (r["date"], r["id"]), reverse=True)


def platform_of(url):
    u = (url or "").lower()
    if "tiktok.com" in u:
        return "TikTok"
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    if "instagram.com" in u:
        return "Instagram"
    if re.match(r"^https?://([a-z0-9-]+\.)*(x|twitter)\.com(/|$)", u):
        return "X"
    return "Web"


# ---------------------------------------------------------------- Obsidian
def vault_dir(platform):
    """Folder for new notes (your existing per-platform folders at the vault root)."""
    return platform if platform in PLATFORM_DIRS else "Videos"


# Your note conventions (see Templates/'YouTube Template.md and `TikTok Template.md)
NOTE_PREFIX = {"YouTube": "'", "TikTok": "`"}
NOTE_RELATED = {
    "YouTube": "[[900_Meta/MOCs/YouTube_MOC|YouTube_MOC]] [[900_Meta/MOCs/YouTube_MOC.canvas|YouTube_MOC]]",
    "TikTok": "[[TikTok_MOC]] [[TikTok_MOC.canvas|TikTok_MOC]]",
}
ATTACH_DIR = "910_Attachments/ReelsHub"
AI_SECTIONS = ["Obsidian Tags", "Summary", "Memorable Things / Main Takeaways", "Key Concepts and Themes",
               "Reflective Questions", "Actionable Insights", "Compare and Contrast"]
AI_TODO = "<!-- reels-hub:ai-todo - ask Claude to fill this in, or use \"Copy AI prompt\" in Reels Hub -->"
MISS = {}                                   # reel id -> time of the last failed vault search


def vault_ok():
    return VAULT.is_dir()


def obsidian_uri(rel_path):
    from urllib.parse import quote
    rel = rel_path[:-3] if rel_path.endswith(".md") else rel_path
    return f"obsidian://open?vault={quote(VAULT.name)}&file={quote(rel)}"


def clean_name(text, fallback):
    name = re.sub(r'[\\/:*?"<>|#^\[\]\r\n\t]+', " ", text or "")
    name = re.sub(r"\s+", " ", name).strip(" .")[:80].strip(" .")
    return name or fallback


def _marker(reel_id):
    return f"<!-- reels-hub:id={reel_id} -->"


def find_vault_note(folder, r, state):
    """Where the reel's note is now: the saved path, or wherever you moved it in the vault."""
    rel = state.get("note_path") or ""
    if rel and (VAULT / rel).is_file():
        return rel
    if state["note_name"]:                                  # notes made by older versions
        old = f"{vault_dir(r['platform'])}/{state['note_name']}.md"
        if (VAULT / old).is_file():
            return old
    if not (rel or state["note_name"]):
        return None                                         # never created: nothing to look for
    if time.time() - MISS.get(r["id"], 0) < 1800:        # a full vault scan is costly on OneDrive
        return None
    hits = []
    for p in VAULT.rglob("*.md"):                           # it was moved or renamed in Obsidian
        if any(part.startswith(".") for part in p.relative_to(VAULT).parts):
            continue
        try:
            with open(p, encoding="utf-8", errors="ignore") as fh:
                head = fh.read(4000)
        except OSError:
            continue
        if has_marker(head, r["id"]):
            hits.append(p.relative_to(VAULT).as_posix())
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        print(f"  {len(hits)} notes carry the id {r['id']} (a copy?) - not guessing: {hits}")
    MISS[r["id"]] = time.time()
    return None


def has_marker(text, rid):
    """The note belongs to this reel: exact id marker (new notes) or exact reel_id line (older notes)."""
    return _marker(rid) in text or re.search(rf"^reel_id:\s*{re.escape(rid)}\s*$", text, re.M) is not None


def ensure_vault_note(folder, r):
    """Create the reel's note once, in your note format; afterwards only the Favorites part is ever updated."""
    if not vault_ok():
        return {"error": f"Vault not found: {VAULT}"}
    with DATA_LOCK:
        return _ensure_vault_note(folder, r)


def _ensure_vault_note(folder, r):
    state = read_state(folder)
    if state.get("_bad"):
        return {"error": "hub.json is unreadable - not touching the Obsidian note"}
    rel = find_vault_note(folder, r, state)
    if not rel:
        if state.get("note_path") or state["note_name"]:
            return {"missing": True, "error": "Obsidian note not found (deleted?) - Send to Obsidian creates a new one"}
        with INBOX_LOCK:
            waiting = folder.name in INBOX["partial"].values()       # downloaded, fill-in to be tried again
        if waiting or (JOB["running"] and JOB.get("share") and r.get("url") in (JOB.get("urls") or [])):
            return {"pending": True}       # a video being added from the inbox: its note waits for the share's notes
        rel = _adopt_transcript_note(folder, r)      # the same video as a transcript-only note: keep that note
    if not rel:
        dname = note_home(r["platform"], state["category"] if state["category"] in read_cats() else "")
        base = NOTE_PREFIX.get(r["platform"], "") + clean_name(r["label"], r["id"]) + (f" (v{r['version']})" if r["version"] > 1 else "")
        name, k = base, 2
        while (VAULT / dname / f"{name}.md").exists():
            name = f"{base} ({k})"; k += 1
        rel = f"{dname}/{name}.md"
        (VAULT / dname).mkdir(parents=True, exist_ok=True)
        atomic_write(VAULT / rel, vault_note_text(folder, r, state))
    if rel != state.get("note_path"):
        st = read_state(folder); st["note_path"] = rel; st["note_name"] = Path(rel).stem; write_state(folder, st)
    return {"name": Path(rel).stem, "path": rel, "uri": obsidian_uri(rel), "sent": state["sent"]}


def _dur(v):
    try:
        d = int(float(v))
        return f"{d // 60}:{d % 60:02d}"
    except (TypeError, ValueError):
        return ""


def _paragraphs(text, size=420):
    """Split transcript text into sentences, then paragraphs of about `size` characters."""
    sents = re.split(r"(?<=[.!?…])\s+", re.sub(r"\s+", " ", text).strip())
    paras, cur = [], ""
    for sn in sents:
        cur = f"{cur} {sn}".strip()
        if len(cur) >= size:
            paras.append(cur); cur = ""
    if cur:
        paras.append(cur)
    return paras


def _info(folder):
    try:
        return json.loads((folder / "video.info.json").read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}


def _text_lines(r):
    """The note's Content (paragraphs) and Original text (mm:ss lines) from the transcript."""
    full_text = " ".join(x["text"] for x in r["transcript"])
    lines = ["# **Content**:", ""]
    lines += [p + "\n" for p in _paragraphs(full_text)] or ["_No speech detected._", ""]
    lines += ["# **Original text:**", ""]
    lines += [f"{mmss(x['t'])} {x['text']}" for x in r["transcript"]] or ["_No speech detected._"]
    return lines


def vault_note_text(folder, r, state, tonly=None):
    """The note in your template. tonly (transcript-only videos): {label, video}: a Transcript line under Posted,
    that text instead of the video placeholder, and the Content + Original text between the text markers (replaced
    by Whisper's transcript if the video is processed in full later)."""
    info = _info(folder)
    plat = r["platform"]
    creator_url = info.get("uploader_url") or info.get("channel_url") or ""
    tag_cat = ("#" + re.sub(r"[^0-9A-Za-z]+", "", state["category"].title())) if state["category"] else ""
    tag_cat = " ".join(dict.fromkeys([t for t in [tag_cat] + [_tag_token(x) for x in state.get("tags") or []] if t]))
    lines = [time.strftime("%Y-%m-%d"), time.strftime("%H:%M"),
             f"**Title:** {r['title'] or r['label']}",
             f"**Source**: {r['url']}",
             f"**Tags**: #{plat} {tag_cat}".rstrip(),
             f"**Related**: {NOTE_RELATED.get(plat, f'[[{plat}_MOC]]')}",
             f"**Creator:** {r['author']}" + (f" - {creator_url}" if creator_url else ""),
             f"**Posted:** {r['date']}" + (f" · {len(r['slides'])} slides" if r.get("slides") else
                                           f" · {_dur(r['duration'])}" if _dur(r["duration"]) else ""),
             *([f"**Transcript:** {tonly['label']}"] if tonly else []),
             f"**Category**: {state['category'] or '-'}",
             _marker(r["id"]),
             VID_START, tonly["video"] if tonly else '_Press "Send to Obsidian" in Reels Hub to add the video._', VID_END,
             "",
             "Formula to remove timestamp: =RIGHT(A1,LEN(A1)-13)", ""]
    for sec in AI_SECTIONS:
        lines += [f"## {sec}", "", AI_TODO, "", "---", ""]
    lines += ["## Favorites (Reels Hub)", FAV_START,
              '_Star snapshots in Reels Hub and press "Send to Obsidian"._', FAV_END, "",
              "## My notes", "", r["notes"] or "", "",
              "# **Description**:", "", r["caption"] or "_none_", ""]
    if r["ocr"]:
        lines += ["## On-screen text", ""] + [f"- **{tlabel(r, r['times'].get(x['shot'], 0))}** {x['text']}" for x in r["ocr"]] + [""]
    if r.get("slides") and not r["transcript"]:            # photo slideshow: the slides' text, in order
        lines += ["# **Content**:", ""]
        lines += [f"**{tlabel(r, r['times'].get(x['shot'], 0))}:** {x['text']}\n" for x in r["ocr"]] or ["_No text found on the slides._", ""]
        return "\n".join(lines) + "\n"
    lines += [TXT_START] if tonly else []
    lines += _text_lines(r)
    lines += [TXT_END] if tonly else []
    return "\n".join(lines) + "\n"


def send_to_obsidian(reel_id):
    """Copy starred images into the vault and write them, with their text, into the reel's note."""
    import shutil
    if not vault_ok():
        raise ValueError(f"Obsidian vault not found: {VAULT}")
    folder = reel_dir(reel_id)
    reel_id = folder.name
    busy_check(reel_id)
    r = parse_note(folder)
    if not r:
        raise ValueError("unknown reel")
    info = ensure_vault_note(folder, r)
    if info.get("missing"):                                   # note was deleted: make a fresh one
        with DATA_LOCK:
            st = read_state(folder); st["note_path"] = ""; st["note_name"] = ""; write_state(folder, st)
        MISS.pop(reel_id, None)
        info = ensure_vault_note(folder, r)
    if info.get("pending"):
        raise ValueError("This video is still being added from the inbox - send it when it is ready")
    if "error" in info:
        raise ValueError(info["error"])
    state = read_state(folder)
    favs = state["favorites"]
    att_rel = f"{ATTACH_DIR}/{reel_id}"
    att = VAULT / att_rel
    att.mkdir(parents=True, exist_ok=True)
    vid_line = "_No video file._"                      # the video itself, so the note can play it
    vids = [p for p in folder.glob("video.*") if p.suffix.lower() in VIDEO_EXT]
    if vids:
        src = vids[0]
        dst = att / f"video{src.suffix.lower()}"
        if not (dst.is_file() and dst.stat().st_size == src.stat().st_size):
            tmp = att / (dst.name + ".part")
            shutil.copyfile(src, tmp)
            os.replace(tmp, dst)
        for old in att.glob("video.*"):                # an older copy with another extension
            if old != dst:
                old.unlink()
        vid_line = f"![[{att_rel}/{dst.name}]]"
    keep = set()
    for f in favs:
        src = folder / "shots" / f["shot"]
        if src.exists():
            shutil.copy2(src, att / f["shot"]); keep.add(f["shot"])
    for old in att.glob("*.jpg"):                      # drop images that are no longer starred
        if old.name not in keep:
            old.unlink()
    legacy = VAULT / vault_dir(r["platform"]) / "attachments" / reel_id   # older versions kept images here
    if legacy.is_dir():
        shutil.rmtree(legacy, ignore_errors=True)

    block = []
    for i, f in enumerate(favs, 1):
        block += [f"![[{att_rel}/{f['shot']}|320]]",
                  f"**{i}. [{tlabel(r, r['times'].get(f['shot'], 0))}]({_at(r['url'], r['times'].get(f['shot'], 0))})** {f['text']}"]
        if f.get("corrected"):
            block.append("*Corrected text*")
        block.append("")
    if not favs:
        block = ["_No starred images._"]
    block = "\n".join(block).rstrip("\n")

    path = VAULT / info["path"]
    with DATA_LOCK:
        text = read_text(path)
        if not has_marker(text, reel_id):
            raise ValueError(f"{info['path']} does not belong to this video (its id line is missing) - not changing it")
        if FAV_START in text and FAV_END in text and text.find(FAV_START) < text.find(FAV_END):
            pre, rest = text.split(FAV_START, 1)
            text = pre + FAV_START + "\n" + block + "\n" + FAV_END + rest.split(FAV_END, 1)[1]
        else:                                          # markers removed by hand: append a fresh section
            text = text.rstrip() + f"\n\n## Favorites (Reels Hub)\n{FAV_START}\n{block}\n{FAV_END}\n"
        vid_block = f"{VID_START}\n{vid_line}\n{VID_END}"
        if VID_START in text and VID_END in text and text.find(VID_START) < text.find(VID_END):
            pre, rest = text.split(VID_START, 1)
            text = pre + vid_block + rest.split(VID_END, 1)[1]
        elif _marker(reel_id) in text:                 # notes made before the video block: right under the id line
            text = text.replace(_marker(reel_id), _marker(reel_id) + "\n" + vid_block, 1)
        else:                                          # oldest notes (reel_id line): top of the favorites block
            text = text.replace(FAV_START + "\n", FAV_START + "\n" + vid_line + "\n\n", 1)
        cat = state["category"]
        text = re.sub(r'^\*\*Category\*\*:.*$', lambda m: f'**Category**: {cat or "-"}', text, count=1, flags=re.M)
        fm = re.match(r"^---\n.*?\n---\n", text, re.S)                # older notes: only inside the frontmatter
        if fm:
            new_fm = re.sub(r'^category:.*$', lambda m: 'category: "' + cat.replace('"', "'") + '"', fm.group(0), count=1, flags=re.M)
            text = new_fm + text[fm.end():]
        atomic_write(path, text)
        st = read_state(folder); st["sent"] = time.strftime("%Y-%m-%d %H:%M"); write_state(folder, st)
    return {"name": info["name"], "uri": info["uri"], "count": len(favs), "sent": st["sent"]}


def _at(url, t):
    """Link to the video at time t, where the site supports it (YouTube)."""
    if not url:
        return ""
    if re.search(r"(youtube\.com|youtu\.be)", url):
        base = re.sub(r"([?&])t=\d+s?&?", r"\1", url).rstrip("?&")
        return f"{base}{'&' if '?' in base else '?'}t={int(t)}s"
    return url


def ai_prompt(reel_id):
    """Your Obsidian AI prompt + this reel's transcript, ready to paste into ChatGPT/Claude."""
    return _ai_prompt(reel_dir(reel_id))


def _ai_prompt(folder):
    r = parse_note(folder)
    if not r:
        raise ValueError("unknown reel")
    prompt = ""
    pf = VAULT / "030_AI" / "001_Prompts" / "$Obsidian AI prompts.md"
    if pf.is_file():
        txt = read_text(pf)
        i = txt.find("# Obsidian Tags")
        prompt = txt[i:] if i >= 0 else txt
    if not prompt:
        prompt = ("For the transcript below, write these Obsidian sections with key words highlighted as "
                  '<mark style="background: #FFF3A3A6;">keyword</mark>: ' + ", ".join(AI_SECTIONS) + ".")
    head = (f"Video: {r['title'] or r['label']}\nSource: {r['url']}\nCreator: {r['author']}\n\n"
            "Answer each section below as Markdown under '## <section name>' headings, in this order.\n\n")
    slides = ""
    if r.get("slides"):                                   # a slideshow: its text is on the slides
        slides = "\n\nText on the slides:\n" + "\n".join(f"{tlabel(r, r['times'].get(x['shot'], 0))}: {x['text']}" for x in r["ocr"])
    spoken = " ".join(x["text"] for x in r["transcript"])
    return head + prompt.strip() + "\n\n---\n" + (("Transcript:\n\n" + spoken) if spoken or not slides else "Photo slideshow (no speech).") + \
        slides + ("\n\nDescription:\n" + r["caption"] if r["caption"] else "")


# ---------------------------------------------------------------- transcripts (transcript-only videos, whole channels)
# reels2notes.py --transcripts puts each video in <reels folder>/_transcripts/<channel>/<date>_<id>/ (note.md as for a
# reel, a trimmed video.info.json; hub.json gets note_path once the vault note exists, and "full" once the same video
# was processed in full). The page's Transcripts catalogue lists them by channel. Nothing here touches the gallery.
def tr_cdir(key):
    """A channel folder inside _transcripts, from a key sent by the page."""
    if not isinstance(key, str) or not re.fullmatch(r"[^/\\:*?\"<>|\x00-\x1f]{1,200}", key) \
            or key.startswith((".", "_")) or key.strip() != key:
        raise PermissionError("bad channel")
    folder = OUT / TR_DIR / key
    if folder.parent.resolve() != (OUT / TR_DIR).resolve() or not folder.is_dir():
        raise ValueError("unknown channel")
    return folder


def tr_dir(key, rid):
    """A transcript folder from a channel key and a video folder name sent by the page (or by the pipeline)."""
    cdir = tr_cdir(key)
    if not isinstance(rid, str) or not re.fullmatch(r"[^/\\:*?\"<>|\x00-\x1f]{1,200}", rid) \
            or rid.startswith((".", "_")) or rid.strip() != rid:
        raise PermissionError("bad video id")
    folder = cdir / rid
    if folder.parent.resolve() != cdir.resolve() or not (folder / "note.md").is_file():
        raise ValueError("unknown transcript")
    return folder


def _hub(folder):
    """hub.json as it is (a transcript folder's note_path, note_name, full)."""
    try:
        d = json.loads(read_text(folder / "hub.json"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _channel_meta(cdir):
    try:
        d = json.loads(read_text(cdir / "_channel.json"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_channel_meta(cdir, meta):
    atomic_write(cdir / "_channel.json", json.dumps(meta, indent=1, ensure_ascii=False))


def tr_item(folder, key):
    """One transcript-only video for the catalogue: reads only the head of note.md (cached by file times)."""
    try:
        mt = ((folder / "note.md").stat().st_mtime, (folder / "hub.json").stat().st_mtime if (folder / "hub.json").exists() else 0)
    except OSError:
        return None
    hit = TR_CACHE.get(str(folder))
    if hit and hit[0] == mt:
        it = dict(hit[1])
    else:
        try:
            with open(folder / "note.md", encoding="utf-8", errors="replace") as fh:
                head = fh.read()
        except OSError:
            return None
        meta, body = {}, head
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", head, re.S)
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = m.group(2)
        t = re.search(r"^# (.+)$", body, re.M)
        lm = re.search(r"^## Transcript \((.*?)\)", body, re.M)
        tl = re.search(r"^## Transcript\b[^\n]*\n(.*?)(?=^## |\Z)", body, re.M | re.S)
        n = len(re.findall(r"^\[\d+:\d+\]", tl.group(1), re.M)) if tl else 0
        url = meta.get("source", "")
        it = {"ch": key, "id": folder.name, "title": (t.group(1).strip() if t else folder.name)[:200],
              "author": meta.get("author", ""), "date": meta.get("date", ""), "duration": meta.get("duration_s", ""),
              "url": url, "platform": meta.get("platform") or platform_of(url), "lang": lm.group(1) if lm else "",
              "source": meta.get("transcript", ""), "lines": n, "added": int(mt[0])}
        st = _hub(folder)
        it["note_path"] = st.get("note_path") or ""
        it["full_flag"] = st.get("full") or ""
        it["category"] = str(st.get("category") or "")[:100]
        it["tags"] = _clean_tags(st.get("tags"))
        it["cat_set"] = "category" in st or st.get("from") == "link"    # a channel's video with no category yet: see _auto_category
        TR_CACHE[str(folder)] = (mt, dict(it))
    rel = it.pop("note_path")
    it["note"] = {"name": Path(rel).stem, "path": rel, "uri": obsidian_uri(rel)} if rel and (VAULT / rel).is_file() else None
    it.pop("full_flag")
    it["full"] = folder.name if (OUT / folder.name / "note.md").is_file() else None   # the same video, in full
    return it


def _tr_folders(cdir):
    return sorted(f for f in cdir.iterdir() if f.is_dir() and not f.name.startswith((".", "_")) and (f / "note.md").is_file())


def list_transcripts():
    """The catalogue: channels (folders in _transcripts) and their transcript-only videos."""
    root = OUT / TR_DIR
    chans, items = [], []
    for cdir in (sorted(root.iterdir()) if root.is_dir() else []):
        if not cdir.is_dir() or cdir.name.startswith((".", "_")):
            continue
        meta = _channel_meta(cdir)
        n = 0
        for f in _tr_folders(cdir):
            it = tr_item(f, cdir.name)
            if it and not it["cat_set"] and meta.get("run"):     # a channel run's video: the channel is its category
                try:
                    _auto_category(f)
                    it = tr_item(f, cdir.name) or it
                except Exception as e:
                    print(f"  {f.name}: category not set ({e})")
            if it:
                items.append(it); n += 1
        rel = meta.get("note_path") or ""
        note = {"name": Path(rel).stem, "path": rel, "uri": obsidian_uri(rel)} if rel and (VAULT / rel).is_file() else None
        chans.append({"key": cdir.name, "name": meta.get("name") or cdir.name, "handle": meta.get("handle", ""),
                      "url": meta.get("url", ""), "platform": meta.get("platform", ""), "count": n, "note": note,
                      "run": tr_run_state(meta.get("run")),
                      "wrong_lang": sum(1 for it in items if it["ch"] == cdir.name and _pref_lang() and "caption" in (it["source"] or "")
                                        and not it["full"] and not _lang_ok(it["lang"], _pref_lang()))})
    _add_reels(chans, items)
    items.sort(key=lambda x: (x["date"], x["id"]), reverse=True)
    return {"channels": chans, "items": items, "channel_cats": channel_cats(), "tags": read_tags()}


def _video_id(folder_name):
    """The site's video id from a folder name <date>_<id>[_vN]."""
    return re.sub(r"_v\d+$", "", folder_name).split("_", 1)[-1]


def _add_reels(chans, items):
    """Downloaded videos in the catalogue too (2026-11-07): one entry per video, under its channel, marked as a video.
    A transcript of the same video (same id from the link) is joined to it: the entry then shows the video's
    transcript, category and note, so a Re-process of the video updates it. Clicking opens the video."""
    import reels2notes as r2n
    reels = {}
    for f in sorted(OUT.iterdir()):                       # sorted: the original before _v2, _v3
        if f.is_dir() and not f.name.startswith(("_", ".")) and (f / "note.md").is_file():
            reels.setdefault(_video_id(f.name), f)
    if not reels:
        return
    by_vid = {_video_id(it["id"]): it for it in items}
    known = {c["key"]: c for c in chans}
    for vid, f in reels.items():
        rk = tr_item(f, "")
        if not rk:
            continue
        st = read_state(f)
        it = by_vid.get(vid)
        if it:                                            # joined: the video's data wins
            for k in ("title", "duration", "lang", "lines", "url", "date"):
                if rk.get(k):
                    it[k] = rk[k]
            it.update(reel=f.name, full=f.name, category=st["category"], tags=st["tags"], source="video")
            if rk.get("note"):
                it["note"] = rk["note"]
            continue
        info = _info(f)
        has_ch = info and any(info.get(k) for k in ("channel", "uploader", "uploader_id"))
        url = rk.get("url") or (info.get("webpage_url") if info else "") or ""
        key = r2n.tr_key(info, url) if has_ch else "_unknown"
        if key not in known:
            known[key] = {"key": key, "name": r2n.tr_name(info, url) if has_ch else "Unknown channel",
                          "handle": ("@" + r2n._handle(info, url).lstrip("@")) if has_ch else "",
                          "url": r2n._channel_url(url, info) if has_ch else "", "platform": rk.get("platform") or "",
                          "count": 0, "note": None, "run": None, "wrong_lang": 0, "videos_only": True}
            chans.append(known[key])
        known[key]["count"] += 1
        rk.update(ch=key, reel=f.name, full=f.name, category=st["category"], tags=st["tags"], source="video")
        items.append(rk)


def _tr_set_category(folder, cat, prev=None):
    """A video's category (transcript folder or reel): hub.json, and in its note the #Category tag on the **Tags**
    line (the tag the app set before is taken out) plus the **Category** line where the note has one; then the note
    moves to <platform>/<category>/ (move_note)."""
    with DATA_LOCK:
        st = _hub(folder)
        if not st and (folder / "hub.json").is_file() and read_text(folder / "hub.json").strip():
            raise ValueError(f"{folder.name}/hub.json can't be read - not changing it")
        old_cat = str(st.get("category") or "") if prev is None else prev
        st["category"] = cat
        tag = ("#" + re.sub(r"[^0-9A-Za-z]+", "", cat.title())) if cat else ""
        old_tag = st.get("cat_tag") or ""
        gone = {t for t in (old_tag, _tag_token(old_cat) if old_cat else "") if t} - set(st.get("tag_tokens") or [])
        rel = st.get("note_path") or ""
        if vault_ok() and rel and (VAULT / rel).is_file():
            text = read_text(VAULT / rel)
            if has_marker(text, folder.name):
                def tags(m):
                    parts = [t for t in m.group(1).split() if t not in gone and t != tag]
                    return "**Tags**: " + " ".join(parts + ([tag] if tag else []))
                new = re.sub(r"^\*\*Tags\*\*:(.*)$", tags, text, count=1, flags=re.M)
                new = re.sub(r"^\*\*Category\*\*:.*$", lambda m: f"**Category**: {cat or '-'}", new, count=1, flags=re.M)
                if new != text:
                    atomic_write(VAULT / rel, new)
        st["cat_tag"] = tag
        atomic_write(folder / "hub.json", json.dumps(st, indent=1, ensure_ascii=False))
        TR_CACHE.pop(str(folder), None)
        try:
            move_note(folder, cat, extra=[old_cat])
        except Exception as e:
            print(f"  {folder.name}: note not moved ({e})")
    return cat


def _auto_category(folder):
    """A transcript from a channel run starts in a category named after the channel (made if missing), so it never
    sits in Unassigned; a category you set yourself, Unassigned included, is left alone (the key is then present)."""
    st = _hub(folder)
    if "category" in st or st.get("from") == "link":
        return None
    meta = _channel_meta(folder.parent)
    kept = (meta.get("keep_cats") or {}).get(folder.name.split("_", 1)[-1])
    if kept is not None:                                   # the category it had before a redo
        if kept and kept not in read_cats():
            with DATA_LOCK:
                write_cats(read_cats() + [kept])
        return _tr_set_category(folder, kept)
    name = str(meta.get("name") or folder.parent.name).strip()[:100]
    if not name or name.lower() == "unassigned":
        return None
    with DATA_LOCK:
        cats = read_cats()
        if name not in cats:
            cats.append(name)
            write_cats(cats)
    mark_channel(name)
    return _tr_set_category(folder, name)


# ---------------------------------------------------------------- note folders by category (2026-11-07)
# Simeon: changing a video's category moves its note to <platform>/<category>/ (Unassigned: back to <platform>/).
# Only notes still in the app's own folders move (the platform folder, <platform>/<a category>, <platform>/<a
# channel's name>); a note you filed somewhere else stays where it is. The file name is kept (links by name keep
# working); a note of the same name already in the target folder: not moved. Empty folders are left in place.

def _note_platform(folder):
    try:
        with open(folder / "note.md", encoding="utf-8", errors="replace") as fh:
            head = fh.read(3000)
    except OSError:
        return ""
    m = re.search(r"^platform:\s*(.+?)\s*$", head, re.M)
    if m:
        return m.group(1).strip().strip('"')
    m = re.search(r"^source:\s*(.+?)\s*$", head, re.M)
    return platform_of(m.group(1).strip().strip('"')) if m else ""


CH_DIR = "Channels"          # channel categories: <platform>/Channels/<channel>/, as the app's Channels group


def note_home(platform, cat):
    base = vault_dir(platform)
    sub = clean_name(cat, "") if cat else ""
    if sub and cat in channel_cats():
        return f"{base}/{CH_DIR}/{sub}"
    return f"{base}/{sub}" if sub else base


def _app_dirs(platform, extra=()):
    base = vault_dir(platform)
    names = list(read_cats()) + [c for c in extra if c]
    root = OUT / TR_DIR
    if root.is_dir():
        for cdir in root.iterdir():
            if cdir.is_dir() and not cdir.name.startswith((".", "_")):
                names.append(str(_channel_meta(cdir).get("name") or cdir.name))
    subs = [clean_name(n, "") for n in names if clean_name(n, "")]
    return {base} | {f"{base}/{x}" for x in subs} | {f"{base}/{CH_DIR}/{x}" for x in subs}


def _move_file(src, dst):
    for i in range(40):
        try:
            os.rename(src, dst)
            return
        except PermissionError:
            time.sleep(0.05)
    raise PermissionError(f"could not move {Path(src).name} (open in another program?)")


def _plan_move(folder, cat, extra=()):
    """(old rel, new rel) for this video's note, or None (no vault, no note, filed by you, already there, clash)."""
    if not vault_ok():
        return None
    st = _hub(folder)
    rel = st.get("note_path") or ""
    if not rel or not (VAULT / rel).is_file():
        return None
    plat = _note_platform(folder)
    cur = rel.rsplit("/", 1)[0] if "/" in rel else ""
    if cur.casefold() not in {d.casefold() for d in _app_dirs(plat, extra)}:   # Windows: case does not matter
        return None
    dest_dir = note_home(plat, cat)
    if cur == dest_dir:
        return None
    new = f"{dest_dir}/{rel.rsplit('/', 1)[-1]}"
    if cur.casefold() == dest_dir.casefold():      # same folder, other case (a channel first known by its handle)
        return None
    if (VAULT / new).exists():
        return ("clash", new)
    try:
        if not has_marker(read_text(VAULT / rel), folder.name):
            return None
    except OSError:
        return None
    return (rel, new)


def move_note(folder, cat, extra=()):
    """Move this video's note to its category folder. Returns the new path or None."""
    with DATA_LOCK:
        plan = _plan_move(folder, cat, extra)
        if not plan or plan[0] == "clash":
            if plan:
                print(f"  {folder.name}: note not moved, {plan[1]} already exists")
            return None
        old, new = plan
        (VAULT / new).parent.mkdir(parents=True, exist_ok=True)
        _move_file(VAULT / old, VAULT / new)
        st = _hub(folder); st["note_path"] = new; st["note_name"] = Path(new).stem
        atomic_write(folder / "hub.json", json.dumps(st, indent=1, ensure_ascii=False))
        TR_CACHE.pop(str(folder), None)
        keys = set()
        root = OUT / TR_DIR                      # the same video's transcript folder points to the same note
        if root.is_dir():
            for cdir in root.iterdir():
                rec = cdir / folder.name
                if cdir.is_dir() and not cdir.name.startswith((".", "_")) and (rec / "hub.json").is_file():
                    h = _hub(rec)
                    if h.get("note_path") == old:
                        h["note_path"] = new; h["note_name"] = Path(new).stem
                        atomic_write(rec / "hub.json", json.dumps(h, indent=1, ensure_ascii=False))
                        TR_CACHE.pop(str(rec), None)
                    if rec == folder or h.get("note_path") == new:
                        keys.add(cdir.name)
        for k in keys:                            # the channel note links by path
            if _channel_meta(OUT / TR_DIR / k).get("note_path"):
                try:
                    channel_note(k)
                except Exception as e:
                    print(f"  channel note {k} not refreshed ({e})")
    return new


def sort_notes(dry=False):
    """Settings > Move notes to category folders: every video's note that is still in the app's folders goes to
    <platform>/<category>/ (Unassigned: <platform>/). dry: count and examples only."""
    if JOB["running"] or RUNQ:
        raise ValueError("Wait for the current run (and the ones waiting) to finish")
    if not vault_ok():
        raise ValueError("No Obsidian vault set (Settings)")
    folders = [f for f in OUT.iterdir() if f.is_dir() and not f.name.startswith((".", "_")) and (f / "note.md").is_file()]
    root = OUT / TR_DIR
    if root.is_dir():
        for cdir in root.iterdir():
            if cdir.is_dir() and not cdir.name.startswith((".", "_")):
                folders += [f for f in _tr_folders(cdir) if not (OUT / f.name / "note.md").is_file()]
    cats = read_cats()
    moves, clashes, mine = [], [], 0
    for f in folders:
        st = _hub(f)
        cat = str(st.get("category") or "")
        cat = cat if cat in cats else ""
        plan = _plan_move(f, cat)
        if plan is None:
            rel = st.get("note_path") or ""
            if rel and vault_ok() and (VAULT / rel).is_file() and (rel.rsplit("/", 1)[0] if "/" in rel else "").casefold() not in {d.casefold() for d in _app_dirs(_note_platform(f))}:
                mine += 1
            continue
        (clashes if plan[0] == "clash" else moves).append((f, cat, plan))
    chmoves = []                                   # the channel notes go with their channel's folder
    if root.is_dir():
        for cdir in root.iterdir():
            if not cdir.is_dir() or cdir.name.startswith((".", "_")):
                continue
            meta = _channel_meta(cdir)
            rel = meta.get("note_path") or ""
            if not rel or not (VAULT / rel).is_file():
                continue
            plat = meta.get("platform") or "YouTube"
            cname = clean_name(meta.get("name") or cdir.name, cdir.name)
            cur, dest = rel.rsplit("/", 1)[0], f"{vault_dir(plat)}/{CH_DIR}/{cname}"
            if cur.casefold() == dest.casefold() or cur.casefold() != f"{vault_dir(plat)}/{cname}".casefold():
                continue
            new = f"{dest}/{rel.rsplit('/', 1)[-1]}"
            if (VAULT / new).exists():
                clashes.append(None); continue
            if f"<!-- reels-hub:channel={cdir.name} -->" in read_text(VAULT / rel):
                chmoves.append((cdir, meta, rel, new))
    if dry:
        return {"count": len(moves) + len(chmoves), "clashes": len(clashes), "yours": mine,
                "examples": [f"{p[0]}  ->  {p[1].rsplit('/', 1)[0]}/" for _, _, p in moves[:8]]
                            + [f"{r}  ->  {n.rsplit('/', 1)[0]}/" for _, _, r, n in chmoves[:2]]}
    done = 0
    for cdir, meta, rel, new in chmoves:
        try:
            with DATA_LOCK:
                (VAULT / new).parent.mkdir(parents=True, exist_ok=True)
                _move_file(VAULT / rel, VAULT / new)
                meta["note_path"] = new
                _save_channel_meta(cdir, meta)
            done += 1
        except Exception as e:
            print(f"  channel note {cdir.name} not moved ({e})")
    for f, cat, _ in moves:
        try:
            if move_note(f, cat):
                done += 1
        except Exception as e:
            print(f"  {f.name}: note not moved ({e})")
    return {"count": done, "clashes": len(clashes), "yours": mine}


def sort_by_channel(dry=False):
    """Unassigned videos -> a category named after their channel or creator (made if missing). Videos with a
    category are left alone. dry: only count."""
    if JOB["running"] or RUNQ:
        raise ValueError("Wait for the current run (and the ones waiting) to finish")
    import reels2notes as r2n
    plan = []
    for f in sorted(OUT.iterdir()):
        if not f.is_dir() or f.name.startswith(("_", ".")) or not (f / "note.md").is_file():
            continue
        st = read_state(f)
        if st.get("_bad") or (st["category"] and st["category"] in read_cats()):
            continue
        info = _info(f)
        known = info and any(info.get(k) for k in ("channel", "uploader", "uploader_id"))   # never a title as a name
        name = re.sub(r"\s+", " ", r2n.tr_name(info, info.get("webpage_url") or "")).strip()[:100] if known else ""
        if name and name.lower() not in ("unassigned", "channel") and not name.isdigit():
            plan.append((f, name))
    if dry:
        return {"count": len(plan), "categories": sorted(set(n for _, n in plan))}
    with DATA_LOCK:
        cats = read_cats()
        cats += [n for n in dict.fromkeys(n for _, n in plan) if n not in cats]
        write_cats(cats, channel_cats() + [n for _, n in plan])
    done = 0
    for f, name in plan:
        try:
            _tr_set_category(f, name)                       # hub.json + the note's tag and Category line
            done += 1
        except Exception as e:
            print(f"  {f.name}: not sorted ({e})")
    return {"count": done, "categories": sorted(set(n for _, n in plan))}


def tr_set_category(key, rid, cat):
    cat = str(cat or "")[:100]
    if cat and cat not in read_cats():
        raise ValueError("No such category")
    return {"category": _tr_set_category(tr_dir(key, rid), cat)}


# ---------------------------------------------------------------- tags (2026-11-07)
# A video has one main category and any number of tags (Simeon: "training" as the category, "kettlebell" as a tag).
# The tag list is in _categories.json "tags"; a video's tags in its hub.json "tags" (reels and transcript folders
# alike). Each tag also goes on the note's **Tags** line as #Tag; the tokens the app wrote are kept in "tag_tokens"
# so a removed tag takes out only its own token (never the platform's or the category's, never one you typed).

def _clean_tags(v):
    out, seen = [], set()
    for t in v if isinstance(v, list) else []:
        t = re.sub(r"\s+", " ", str(t).replace("#", "")).strip()[:60]
        if t and t.lower() not in seen and t.lower() != "unassigned":
            seen.add(t.lower()); out.append(t)
    return out[:30]


def _tag_token(name):
    return ("#" + re.sub(r"[^0-9A-Za-z]+", "", str(name).title())) if re.sub(r"[^0-9A-Za-z]+", "", str(name)) else ""


def read_tags():
    return _clean_tags(_cats_raw().get("tags"))


def write_tags(tags):
    raw = _cats_raw()
    raw.setdefault("categories", read_cats())
    raw["tags"] = _clean_tags(tags)
    atomic_write(cats_file(), json.dumps(raw, indent=1, ensure_ascii=False))


def _write_item_tags(folder, tags):
    """hub.json "tags" and the note's Tags line. Caller holds DATA_LOCK."""
    st = _hub(folder)
    if not st and (folder / "hub.json").is_file() and read_text(folder / "hub.json").strip():
        raise ValueError(f"{folder.name}/hub.json can't be read - not changing it")
    tags = _clean_tags(tags)
    old = [t for t in st.get("tag_tokens") or [] if isinstance(t, str)]
    want = [t for t in dict.fromkeys(_tag_token(x) for x in tags) if t]
    keep = set(want) | {st.get("cat_tag") or "", _tag_token(st.get("category") or "")}
    rel = st.get("note_path") or ""
    if vault_ok() and rel and (VAULT / rel).is_file():
        text = read_text(VAULT / rel)
        if has_marker(text, folder.name):
            def line(m):
                parts = [t for t in m.group(1).split() if not (t in old and t not in keep)]
                return "**Tags**: " + " ".join(parts + [t for t in want if t not in parts])
            new = re.sub(r"^\*\*Tags\*\*:(.*)$", line, text, count=1, flags=re.M)
            if new != text:
                atomic_write(VAULT / rel, new)
    st["tags"], st["tag_tokens"] = tags, want
    if not tags:
        st.pop("tags"); st.pop("tag_tokens")
    atomic_write(folder / "hub.json", json.dumps(st, indent=1, ensure_ascii=False))
    TR_CACHE.pop(str(folder), None)
    return tags


def set_tags(body):
    """POST /api/tags/set {id (a reel) | channel + id (a transcript), tags}: new names join the tag list."""
    if body.get("channel"):
        folder = tr_dir(body.get("channel"), body.get("id"))
    else:
        folder = reel_dir(body.get("id"))
        if not (folder / "note.md").exists():
            raise ValueError("unknown reel")
        busy_check(folder.name)
    tags = _clean_tags(body.get("tags"))
    with DATA_LOCK:
        known = read_tags()
        low = {t.lower(): t for t in known}
        tags = [low.get(t.lower(), t) for t in tags]          # same spelling as the list
        new = [t for t in tags if t.lower() not in low]
        if new:
            write_tags(known + new)
        tags = _write_item_tags(folder, tags)
    return {"tags": tags, "all": read_tags()}


def _all_tagged():
    for f in OUT.iterdir() if OUT.is_dir() else []:
        if f.is_dir() and not f.name.startswith((".", "_")) and (f / "hub.json").is_file():
            yield f
    root = OUT / TR_DIR
    if root.is_dir():
        for cdir in root.iterdir():
            if cdir.is_dir() and not cdir.name.startswith((".", "_")):
                for f in cdir.iterdir():
                    if f.is_dir() and (f / "hub.json").is_file():
                        yield f


def change_tags(body):
    """POST /api/tags {action add | rename | delete, name, new}: rename and delete reach every video and its note."""
    action = body.get("action")
    name = _clean_tags([body.get("name")])
    name = name[0] if name else ""
    if not name:
        raise ValueError("Tag name is empty")
    with DATA_LOCK:
        tags = read_tags()
        low = [t.lower() for t in tags]
        if action == "add":
            if name.lower() not in low:
                write_tags(tags + [name])
            return read_tags()
        if name.lower() not in low:
            raise ValueError("No such tag")
        name = tags[low.index(name.lower())]
        if action == "rename":
            new = _clean_tags([body.get("new")])
            if not new:
                raise ValueError("Invalid new name")
            new = new[0]
            if new.lower() in low and new.lower() != name.lower():
                raise ValueError("That tag already exists")
            tags[tags.index(name)] = new
        elif action == "delete":
            tags.remove(name); new = None
        else:
            raise ValueError("Unknown action")
        write_tags(tags)
        for f in _all_tagged():
            st = _hub(f)
            cur = _clean_tags(st.get("tags"))
            if name in cur:
                try:
                    _write_item_tags(f, [x for x in (new if x == name else x for x in cur) if x])
                except Exception as e:
                    print(f"  {f.name}: tag not changed ({e})")
        return read_tags()


def _pref_lang():
    return str(CONFIG.get("sub_lang", "en") if "sub_lang" in CONFIG else "en").lower()


def _lang_ok(lang, pref):
    l = str(lang or "").lower()
    return not pref or l == pref or l.startswith(pref + "-") or l.startswith(pref + "_")


def tr_wrong_lang(key):
    """A channel's transcript-only videos whose text came from captions in another language than the one set
    (Whisper hears the spoken language, so those are left out; so are videos in the library in full)."""
    pref = _pref_lang()
    out = []
    for f in _tr_folders(tr_cdir(key)):
        it = tr_item(f, key)
        if it and pref and "caption" in (it["source"] or "") and not it["full"] and not _lang_ok(it["lang"], pref):
            out.append({"id": it["id"], "title": it["title"], "lang": it["lang"]})
    return {"lang": pref, "items": out}


def tr_redo_lang(body):
    """Redo the listed videos: their folders to _trash/_transcripts, their notes (id marker only) to the vault's
    .trash, the category each had kept for the new copy, then the channel's listing runs again (only what is
    missing is done)."""
    key = body.get("channel")
    cdir = tr_cdir(key)
    meta = _channel_meta(cdir)
    url = (meta.get("run") or {}).get("url") or meta.get("url")
    if not url:
        raise ValueError("This channel has no link to run again")
    wanted = set(str(x) for x in (body.get("ids") or []))
    ids = [x["id"] for x in tr_wrong_lang(key)["items"] if x["id"] in wanted]
    if not ids:
        raise ValueError("Nothing to redo")
    keep = meta.get("keep_cats") or {}
    for rid in ids:
        st = _hub(tr_dir(key, rid))
        if "category" in st:
            keep[rid.split("_", 1)[-1]] = st["category"]
    meta["keep_cats"] = keep
    _save_channel_meta(cdir, meta)
    res = remove_transcripts({"channel": key, "ids": ids, "obsidian": True})
    ahead = queue_run({"kind": "run", "urls": [url], "max_shots": "auto", "redo": False, "flags": ["--transcripts"],
                       "target": "", "label": "Transcripts again: " + tr_label(url), "platform": platform_of(url),
                       "transcripts": True})
    return {"removed": res["removed"], "errors": res["errors"], "queued": ahead}


def tr_run_state(run):
    """The last run of a channel as the page shows it. A record left at "running" by a run that is no longer going
    (the app was closed, the pipeline died) is "interrupted"."""
    if not isinstance(run, dict) or not run.get("url"):
        return None
    r = {k: run.get(k) for k in ("url", "title", "started", "at", "listed", "here", "todo", "done", "failed", "last", "next", "state")}
    r["left"] = max(0, int(run.get("todo") or 0) - int(run.get("done") or 0) - int(run.get("failed") or 0))
    going = JOB["running"] and JOB.get("transcripts") and JOB.get("pid") and run.get("pid") == JOB.get("pid")
    if r["state"] == "running" and not going:
        r["state"] = "interrupted"
    return r


def tr_control(action):
    """Pause, Resume or Stop the transcripts run that is going: reels2notes reads the control file before each video."""
    if not (JOB["running"] and JOB.get("transcripts")):
        raise ValueError("No transcripts run is going")
    if action not in ("pause", "resume", "stop"):
        raise ValueError("Bad request")
    atomic_write(OUT / TR_DIR / TR_CONTROL, json.dumps({"pause": action == "pause", "stop": action == "stop"}))
    JOB["paused"] = action == "pause"
    JOB["stopping"] = action == "stop"
    return {"paused": JOB["paused"], "stopping": JOB["stopping"]}


def transcript_text(key, rid):
    """A transcript-only video's lines, description and language for the page."""
    r = parse_note(tr_dir(key, rid))
    if not r:
        raise ValueError("unknown transcript")
    return {"lines": r["transcript"], "caption": r["caption"], "lang": r["lang"], "title": r["title"], "url": r["url"]}


def template_note_text(folder, r):
    """A transcript-only note in your own template, read from the vault's Templates folder at that moment (else the
    copy kept here): date, time, Title, Source (Creator and Description in the TikTok one), the id marker and the
    transcript's source as comments after Related, the transcript under Content and Original text between the text
    markers. Nothing else is added, so the note looks exactly like one made from the template by hand."""
    plat = r["platform"]
    tf = VAULT / "Templates" / TEMPLATE_FILES[plat]
    text = read_text(tf) if tf.is_file() else TEMPLATE_FALLBACK[plat]
    lines = [l.rstrip() for l in text.replace("\r\n", "\n").split("\n")]
    needed = ("**Source**", "**Related**", "# **Content**", "# **Original text")
    if not all(any(l.startswith(n) for l in lines) for n in needed):
        lines = [l.rstrip() for l in TEMPLATE_FALLBACK[plat].split("\n")]
    body = _text_lines(r)                                     # # **Content**:, paragraphs, # **Original text:**, lines
    paras = [x.rstrip("\n") for x in body[2:body.index("# **Original text:**")] if x.strip()]
    content = ["", "\n\n".join(paras)]                        # one blank line between paragraphs
    original = body[body.index("# **Original text:**") + 1:]
    out = []
    for line in lines:
        if line.strip() == "{{date}}":
            out.append(time.strftime("%Y-%m-%d"))
        elif line.strip() == "{{time}}":
            out.append(time.strftime("%H:%M"))
        elif line.startswith("**Title:**"):
            out.append(f"**Title:** {r['title'] or r['label']}")
        elif line.startswith("**Source**"):
            out.append(f"**Source**: {r['url']}")
        elif line.startswith("**Creator:**"):
            out.append(f"**Creator:** {r['author']}")
        elif line.startswith("**Description:**"):
            out.append("**Description:** " + (r["caption"].strip() or "-"))
        elif line.startswith("**Related**"):
            out += [line, _marker(r["id"]), f"<!-- reels-hub:transcript={r['tsource'] or 'captions'}, video not downloaded -->"]
        elif line.startswith("# **Content**"):
            out += [TXT_START, line] + content
        elif line.startswith("# **Original text"):
            out += [line] + original + [TXT_END]
        else:
            out.append(line)
    return "\n".join(out).rstrip("\n") + "\n"


def transcript_note(folder, from_channel):
    """Create the Obsidian note of a transcript-only video once: your template, the transcript between the text
    markers. From a channel link the note goes to YouTube/<channel>/, from a single link to YouTube/ as usual."""
    if not vault_ok():
        return {"error": f"Vault not found: {VAULT}"}
    with DATA_LOCK:
        r = parse_note(folder)
        if not r:
            return {"error": "no note.md"}
        st = _hub(folder)
        rel = st.get("note_path") or ""
        if rel and (VAULT / rel).is_file():
            return {"name": Path(rel).stem, "path": rel, "uri": obsidian_uri(rel)}
        label = tr_source_label(r["tsource"]) + " · video not downloaded"
        dname = vault_dir(r["platform"])
        if from_channel:
            cm = _channel_meta(folder.parent)
            dname = f"{dname}/{CH_DIR}/{clean_name(cm.get('name') or folder.parent.name, folder.parent.name)}"
        base = NOTE_PREFIX.get(r["platform"], "") + clean_name(r["label"], r["id"])
        name, k = base, 2
        while (VAULT / dname / f"{name}.md").exists():
            name = f"{base} ({k})"; k += 1
        rel = f"{dname}/{name}.md"
        (VAULT / dname).mkdir(parents=True, exist_ok=True)
        text = template_note_text(folder, r) if r["platform"] in TEMPLATE_FILES else \
            vault_note_text(folder, r, read_state(folder), tonly={"label": label, "video": TR_VIDEO_LINE})
        atomic_write(VAULT / rel, text)
        st.update(note_path=rel, note_name=name, **{"from": "channel" if from_channel else "link"})
        atomic_write(folder / "hub.json", json.dumps(st, indent=1, ensure_ascii=False))
        return {"name": name, "path": rel, "uri": obsidian_uri(rel)}


def channel_note(key):
    """The channel's own note, YouTube/<channel>/'<channel> (channel).md: the list of its videos' notes. Only the
    block between the videos markers is ever rewritten; the rest of the note is yours."""
    if not vault_ok():
        return None
    cdir = tr_cdir(key)
    meta = _channel_meta(cdir)
    cname = clean_name(meta.get("name") or key, key)
    rows = []
    items = [it for it in (tr_item(f, key) for f in _tr_folders(cdir)) if it]
    for it in sorted(items, key=lambda x: (x["date"], x["id"]), reverse=True):
        title = re.sub(r"[\[\]|]+", " ", it["title"]).strip() or it["id"]
        link = f"[[{it['note']['path'][:-3]}|{title}]]" if it["note"] else title
        src = tr_source_label(it["source"]) if it["source"] else ""
        rows.append(f"- {it['date'] or '----'} · {_dur(it['duration']) or '-'} · {link}" + (f" · {src}" if src else "")
                    + (" · in Reels Hub with the video" if it["full"] else ""))
    block = "\n".join(rows) or "_No videos yet._"
    with DATA_LOCK:
        rel = meta.get("note_path") or ""
        mark = f"<!-- reels-hub:channel={key} -->"
        if rel and (VAULT / rel).is_file():
            text = read_text(VAULT / rel)
            if mark not in text:
                print(f"  {rel} is not this channel's note (its marker is missing) - not changing it")
                return None
            if VIDS_START in text and VIDS_END in text and text.find(VIDS_START) < text.find(VIDS_END):
                pre, rest = text.split(VIDS_START, 1)
                text = pre + VIDS_START + "\n" + block + "\n" + VIDS_END + rest.split(VIDS_END, 1)[1]
            else:
                text = text.rstrip() + f"\n\n## Videos (Reels Hub)\n{VIDS_START}\n{block}\n{VIDS_END}\n"
        else:
            plat = meta.get("platform") or "YouTube"
            dname = f"{vault_dir(plat)}/{CH_DIR}/{cname}"
            rel = f"{dname}/{NOTE_PREFIX.get(plat, '')}{cname} (channel).md"
            if (VAULT / rel).is_file() and mark not in read_text(VAULT / rel):
                rel = f"{dname}/{NOTE_PREFIX.get(plat, '')}{cname} (channel, Reels Hub).md"
            (VAULT / dname).mkdir(parents=True, exist_ok=True)
            text = "\n".join([time.strftime("%Y-%m-%d"), time.strftime("%H:%M"),
                              f"**Channel:** {meta.get('name') or key}" + (f" ({meta['handle']})" if meta.get("handle") else ""),
                              f"**Source**: {meta.get('url') or '-'}",
                              f"**Tags**: #{meta.get('platform') or 'YouTube'} #Channel",
                              f"**Related**: {NOTE_RELATED.get(meta.get('platform') or 'YouTube', '')}".rstrip(),
                              mark, "",
                              "## Videos (Reels Hub)", VIDS_START, block, VIDS_END, "",
                              "## My notes", "", ""])
        atomic_write(VAULT / rel, text)
        meta["note_path"] = rel
        _save_channel_meta(cdir, meta)
    return rel


def _adopt_transcript_note(folder, r):
    """A video processed in full that already has a transcript-only note (same id): that note is kept, and
    Whisper's transcript goes in place of the caption text between the text markers. Returns the note's path."""
    root = OUT / TR_DIR
    if not root.is_dir() or not vault_ok():
        return None
    for cdir in sorted(root.iterdir()):
        rec = cdir / folder.name
        if not cdir.is_dir() or cdir.name.startswith((".", "_")) or not (rec / "note.md").is_file():
            continue
        st = _hub(rec)
        rel = st.get("note_path") or ""
        if not rel or not (VAULT / rel).is_file():
            continue
        text = read_text(VAULT / rel)
        if not has_marker(text, folder.name):
            continue
        if r["transcript"] and TXT_START in text and TXT_END in text and text.find(TXT_START) < text.find(TXT_END):
            pre, rest = text.split(TXT_START, 1)
            ocr = ["## On-screen text", ""] + [f"- **{tlabel(r, r['times'].get(x['shot'], 0))}** {x['text']}" for x in r["ocr"]] + [""] if r["ocr"] else []
            text = pre + "\n".join(ocr + _text_lines(r)) + "\n" + rest.split(TXT_END, 1)[1].lstrip("\n")
            text = re.sub(r"^\*\*Transcript:\*\* .*\n", "", text, count=1, flags=re.M)
            text = re.sub(r"^<!-- reels-hub:transcript=.*-->\n", "", text, count=1, flags=re.M)
            atomic_write(VAULT / rel, text)
            print(f"  {rel}: the caption text was replaced by the full video's transcript")
        st["full"] = folder.name
        atomic_write(rec / "hub.json", json.dumps(st, indent=1, ensure_ascii=False))
        TR_CACHE.pop(str(rec), None)
        # the category and tags you gave the transcript carry over to the video (instead of the channel's category
        # the download started with), with the note tokens that go with them, so the note stays where it is
        rh = _hub(folder)
        if "category" in st or st.get("tags"):
            if "category" in st:
                rh["category"] = str(st.get("category") or "")
            for k in ("tags", "tag_tokens", "cat_tag"):
                if st.get(k):
                    rh[k] = st[k]
            atomic_write(folder / "hub.json", json.dumps(rh, indent=1, ensure_ascii=False))
        try:
            channel_note(rec.parent.name)                   # the channel's list now says the video is here in full
        except Exception as e:
            print(f"  channel note: {e}")
        return rel
    return None


def remove_transcripts(body):
    """Take transcript-only videos, or a whole channel, out of the catalogue: their folders go to
    _trash/_transcripts/<channel>, and with obsidian=true their notes to the vault's .trash (only notes that carry
    the video's id marker, and never the note of a video that is in the library in full)."""
    if JOB["running"]:
        raise ValueError("Wait for the current run to finish before removing")
    key = body.get("channel")
    cdir = tr_cdir(key)
    whole = bool(body.get("all"))
    with_obsidian = bool(body.get("obsidian"))
    folders = _tr_folders(cdir) if whole else [tr_dir(key, rid) for rid in (body.get("ids") or [])]
    if not folders and not whole:
        raise ValueError("Nothing to remove")
    trash = OUT / "_trash" / TR_DIR / key
    vtrash = VAULT / ".trash"
    done, errors = [], []
    for f in folders:
        try:
            st = _hub(f)
            rel = st.get("note_path") or ""
            note = None
            if with_obsidian and vault_ok() and rel and (VAULT / rel).is_file() and not (OUT / f.name / "note.md").is_file() \
                    and has_marker(read_text(VAULT / rel), f.name):
                note = VAULT / rel
            trash.mkdir(parents=True, exist_ok=True)
            with DATA_LOCK:
                os.replace(f, _unique(trash / f.name))
            TR_CACHE.pop(str(f), None)
            if note:
                vtrash.mkdir(exist_ok=True)
                try:
                    os.replace(note, _unique(vtrash / note.name))
                except OSError as e:
                    errors.append(f"{f.name}: Obsidian note not moved ({e})")
            done.append(f.name)
        except Exception as e:
            errors.append(f"{f.name}: {e}")
    if whole and not errors:
        meta = _channel_meta(cdir)
        rel = meta.get("note_path") or ""
        if with_obsidian and vault_ok() and rel and (VAULT / rel).is_file() and f"<!-- reels-hub:channel={key} -->" in read_text(VAULT / rel):
            vtrash.mkdir(exist_ok=True)
            try:
                os.replace(VAULT / rel, _unique(vtrash / Path(rel).name))
            except OSError as e:
                errors.append(f"channel note not moved ({e})")
        trash.mkdir(parents=True, exist_ok=True)
        for extra in cdir.iterdir():                        # _channel.json and anything else left
            try:
                os.replace(extra, _unique(trash / extra.name))
            except OSError as e:
                errors.append(f"{extra.name}: {e}")
        try:
            cdir.rmdir()
        except OSError as e:
            errors.append(f"the channel folder stays ({e})")
    return {"removed": done, "errors": errors, "channel": key, "gone": whole and not errors}


def tr_listing(url):
    """A YouTube channel or playlist link, or a TikTok profile (many videos): only Transcripts takes those."""
    u = str(url or "").split("#")[0]
    if re.search(r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|live/|embed/|v/)|youtu\.be/)[A-Za-z0-9_-]{11}", u):
        return False
    if re.search(r"youtube\.com/(@[^/?]+|channel/|c/|user/|playlist\?)", u, re.I):
        return True
    return bool(re.search(r"tiktok\.com/@[\w.-]+/?(?:$|[?])", u, re.I))


def tr_label(url):
    """How a transcripts run shows in the Waiting list: the creator's handle, or the link."""
    m = re.search(r"(?:youtube|tiktok)\.com/(@[^/?#]+)", url, re.I)
    return m.group(1) if m else short_url(url)


def safe_path(rel):
    from pathlib import PureWindowsPath
    if not isinstance(rel, str) or not rel or "\\" in rel or ":" in rel or rel.startswith("/") \
            or PureWindowsPath(rel).drive or PureWindowsPath(rel).root:
        raise PermissionError(rel)                # blocks C:\..., \\server\share and //server/share
    p = (OUT / rel).resolve()
    if OUT.resolve() not in p.parents and p != OUT.resolve():
        raise PermissionError(rel)
    return p


def mmss(t):
    t = int(round(t))
    return f"{t // 60:02d}:{t % 60:02d}"


def read_slides(folder):
    """A photo slideshow's slides [{n, kind, start, dur}] (slides.json from reels2notes), else None."""
    try:
        d = json.loads((folder / "slides.json").read_text(encoding="utf-8", errors="replace"))
        out = [{"n": int(x["n"]), "kind": "video" if x.get("kind") == "video" else "image",
                "start": float(x["start"]), "dur": float(x["dur"])} for x in d.get("slides", [])]
        return out or None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def slide_at(slides, t):
    """The slide shown at time t."""
    cur = slides[0]
    for x in slides:
        if x["start"] <= t + 0.001:
            cur = x
    return cur


def tlabel(r, t):
    """Where a moment is: mm:ss for a video, 'Slide 3' (or 'Slide 3 · 00:04' in a video slide) for a slideshow."""
    if not r.get("slides"):
        return mmss(t)
    x = slide_at(r["slides"], t)
    return f"Slide {x['n']}" + (f" · {mmss(t - x['start'])}" if x["kind"] == "video" else "")


# ---------------------------------------------------------------- saving
def _share_guard(old, page_at):
    """A page that still shows a reel as it was before a share filled it in must not overwrite it."""
    sb = old.get("shared_by")
    if sb and str(sb.get("at") or "") != str(page_at or ""):
        raise ValueError("This video was just filled in from a share: it reloads now, then make your change again")


def save_notes(reel_id, notes, page_at=None):
    folder = reel_dir(reel_id)
    busy_check(folder.name)
    with DATA_LOCK:
        _share_guard(read_state(folder), page_at)
        _write_notes(folder, notes)


def _write_notes(folder, notes):
    body = str(notes).replace(NOTES_E, "").strip()
    with DATA_LOCK:
        note = folder / "note.md"
        text = read_text(note)
        block = f"{NOTES_S}\n{body}\n{NOTES_E}"
        if NOTES_S in text and NOTES_E in text and text.find(NOTES_S) < text.find(NOTES_E):
            text = text[:text.find(NOTES_S)] + block + text[text.find(NOTES_E) + len(NOTES_E):]
        elif "\n## My notes" in text:              # older notes: My notes is the last section
            text = text.split("\n## My notes")[0] + "\n## My notes\n" + block + "\n"
        else:
            text = text.rstrip() + "\n\n## My notes\n" + block + "\n"
        atomic_write(note, text)


def save_state(reel_id, state):
    folder = reel_dir(reel_id)
    if not (folder / "note.md").exists():
        raise ValueError("unknown reel")
    busy_check(folder.name)
    if not isinstance(state, dict):
        raise ValueError("bad state")
    shots = {p.name for p in (folder / "shots").glob("*.jpg")}
    favs, seen = [], set()
    for f in state.get("favorites", []) if isinstance(state.get("favorites"), list) else []:
        if not isinstance(f, dict):
            continue
        shot = str(f.get("shot", ""))
        if shot in shots and shot not in seen:
            seen.add(shot)
            favs.append({"shot": shot, "text": str(f.get("text", ""))[:2000],
                         "corrected": bool(f.get("corrected"))})
    with DATA_LOCK:
        old = read_state(folder)                     # keep server-owned fields (vault note name, last send, sharing)
        page_sb = state.get("shared_by")
        _share_guard(old, page_sb.get("at") if isinstance(page_sb, dict) else None)
        clean = {"category": str(state.get("category", ""))[:100], "favorites": favs,
                 "note_name": old["note_name"], "note_path": old["note_path"], "sent": old["sent"],
                 "shared_by": old["shared_by"], "shared": old["shared"],
                 "tags": old["tags"], "tag_tokens": old["tag_tokens"], "cat_tag": old["cat_tag"]}
        write_state(folder, clean)
        write_note_favorites(folder, favs)
        if clean["category"] != old["category"]:          # the note follows at once: tag, Category line, folder
            try:
                _tr_set_category(folder, clean["category"], prev=old["category"])
            except Exception as e:
                print(f"  {folder.name}: note not updated for the category ({e})")
            clean = {**clean, **{k: v for k, v in read_state(folder).items() if k in ("note_path", "note_name", "cat_tag")}}
    return clean


def write_note_favorites(folder, favs):
    """Keep a ## Favorites section in note.md in sync (for Obsidian), inside its own markers."""
    note = folder / "note.md"
    text = read_text(note)
    held = None                                    # keep the user's notes out of reach of the edits below
    if NOTES_S in text and NOTES_E in text and text.find(NOTES_S) < text.find(NOTES_E):
        a, b = text.find(NOTES_S), text.find(NOTES_E) + len(NOTES_E)
        held, text = text[a:b], text[:a] + "\x00NOTES\x00" + text[b:]
    if LFAV_S in text and LFAV_E in text and text.find(LFAV_S) < text.find(LFAV_E):
        a = text.find(LFAV_S); b = text.find(LFAV_E) + len(LFAV_E)
        a = text.rfind("\n## Favorites", 0, a) if text.rfind("\n## Favorites", 0, a) >= 0 else a
        text = text[:a] + text[b:]
    else:                                          # older notes without markers
        text = re.sub(r"\n## Favorites\n.*?(?=\n## (?:Snapshots|My notes)|\x00NOTES|\Z)", "", text, flags=re.S)
    if favs:
        times = shot_times(folder, sorted(p.name for p in (folder / "shots").glob("*.jpg")))
        sl = {"slides": read_slides(folder)}
        lines = ["", "## Favorites"]
        for f in favs:
            lines.append(f"![[{folder.name}/shots/{f['shot']}]]")
            mark = " _(corrected text)_" if f["corrected"] else ""
            lines.append(f"**{tlabel(sl, times.get(f['shot'], 0))}** {f['text']}{mark}")
            lines.append("")
        lines = lines[:2] + [LFAV_S] + lines[2:]
        block = "\n".join(lines).rstrip("\n") + "\n" + LFAV_E + "\n"
        if "\n## Snapshots" in text:
            text = text.replace("\n## Snapshots", block + "\n## Snapshots", 1)
        else:
            text = text.rstrip() + "\n" + block
    if held is not None:
        text = text.replace("\x00NOTES\x00", held, 1)
    atomic_write(note, text)


# ---------------------------------------------------------------- categories
def cats_file():
    return OUT / "_categories.json"


def read_cats():
    f = cats_file()
    if f.exists():
        try:
            return [c for c in json.loads(f.read_text(encoding="utf-8", errors="replace")).get("categories", []) if c]
        except ValueError:
            pass
    return []


def _cats_raw():
    try:
        d = json.loads(read_text(cats_file())) if cats_file().exists() else {}
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_cats(cats, channels=None):
    """The category list; channels = the names that are channel categories (kept as they were when not given)."""
    raw = _cats_raw()
    ch = raw.get("channels") if channels is None else channels
    ch = [c for c in dict.fromkeys(ch or []) if c in cats]
    d = {"categories": cats, "channels": ch, "tags": read_tags()}
    if raw.get("channels_checked"):
        d["channels_checked"] = True
    atomic_write(cats_file(), json.dumps(d, indent=1, ensure_ascii=False))


def mark_channel(name):
    with DATA_LOCK:
        cats = read_cats()
        ch = channel_cats()
        if name in cats and name not in ch:
            write_cats(cats, ch + [name])


def channel_cats():
    """Categories made from a channel or creator name (shown under one Channels group). Categories made before
    2026-11-07 are worked out once: a category whose videos or transcripts all come from a channel of that name."""
    raw = _cats_raw()
    cats = read_cats()
    ch = [c for c in raw.get("channels") or [] if c in cats]
    if raw.get("channels_checked"):
        return ch
    try:
        import reels2notes as r2n
        found, other = set(), set()
        for f in OUT.iterdir():
            if not f.is_dir() or f.name.startswith(("_", ".")) or not (f / "note.md").is_file():
                continue
            c = read_state(f)["category"]
            if not c or c not in cats:
                continue
            info = _info(f)
            name = re.sub(r"\s+", " ", r2n.tr_name(info, info.get("webpage_url") or "")).strip() if info else ""
            (found if name == c else other).add(c)
        root = OUT / TR_DIR
        if root.is_dir():
            for cdir in root.iterdir():
                if cdir.is_dir() and not cdir.name.startswith((".", "_")):
                    cname = str(_channel_meta(cdir).get("name") or cdir.name).strip()
                    for tf in _tr_folders(cdir):
                        c = _hub(tf).get("category") or ""
                        if c in cats:
                            (found if c == cname else other).add(c)
        ch = list(dict.fromkeys(ch + [c for c in cats if c in found and c not in other]))
        with DATA_LOCK:
            raw = _cats_raw(); raw.update(categories=read_cats(), channels=ch, channels_checked=True)
            atomic_write(cats_file(), json.dumps(raw, indent=1, ensure_ascii=False))
    except Exception as e:
        print(f"  channel categories not worked out: {e}")
    return ch


def recategorize(old, new):
    for folder in OUT.iterdir():
        f = folder / "hub.json"
        if folder.is_dir() and f.exists() and not folder.name.startswith((".", "_")):
            s = read_state(folder)
            if s["category"] == old and not s.get("_bad"):
                try:
                    _tr_set_category(folder, new)          # hub.json, the note's tag and Category line, its folder
                except Exception as e:
                    print(f"  {folder.name}: category not changed ({e})")
    root = OUT / TR_DIR                                   # transcript-only videos keep their category in hub.json too
    if root.is_dir():
        for cdir in root.iterdir():
            if cdir.is_dir() and not cdir.name.startswith((".", "_")):
                for folder in cdir.iterdir():
                    if folder.is_dir() and (folder / "hub.json").is_file() and _hub(folder).get("category") == old:
                        try:
                            _tr_set_category(folder, new)
                        except Exception as e:
                            print(f"  {folder.name}: category not changed ({e})")


def _shot_path(reel_id, name):
    folder = reel_dir(reel_id)
    if not (folder / "note.md").exists():
        raise ValueError("unknown reel")
    busy_check(folder.name)
    if not re.fullmatch(r"shot_\d+\.jpg", str(name)):
        raise ValueError("bad snapshot name")
    p = folder / "shots" / name
    if not p.exists():
        raise ValueError("snapshot not found")
    return p


def _backup(p):
    """Keep the untouched original once, in shots/_orig/, before the first edit."""
    import shutil
    orig = p.parent / "_orig" / p.name
    if not orig.exists():
        orig.parent.mkdir(exist_ok=True)
        shutil.copy2(p, orig)


def save_edit(reel_id, name, data_url):
    import base64
    p = _shot_path(reel_id, name)
    m = re.match(r"data:image/(jpeg|png);base64,(.+)$", data_url or "", re.S)
    if not m:
        raise ValueError("bad image data")
    raw = base64.b64decode(m.group(2))
    if len(raw) > 25_000_000:
        raise ValueError("image too large")
    with DATA_LOCK:
        _backup(p)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(raw)
        tmp.replace(p)
    return int(p.stat().st_mtime)


def reset_edit(reel_id, name):
    import shutil
    p = _shot_path(reel_id, name)
    orig = p.parent / "_orig" / p.name
    if not orig.exists():
        raise ValueError("this snapshot has not been edited")
    with DATA_LOCK:
        shutil.copy2(orig, p)
        orig.unlink()
        os.utime(p, None)                     # new mtime so the browser reloads it
    return int(p.stat().st_mtime)


def open_external(reel_id, name):
    """Open the snapshot in the Windows image editor (Paint) - saves go straight back to the file."""
    p = _shot_path(reel_id, name)
    _backup(p)
    if sys.platform.startswith("win"):
        try:
            os.startfile(str(p), "edit")          # default 'Edit' app for JPG, usually Paint
        except OSError:
            subprocess.Popen(["mspaint", str(p)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)])


def delete_shots(reel_id, names, force=False):
    """Delete snapshot files (never favorites) and drop them from shots.json and note.md."""
    folder = reel_dir(reel_id)
    if not (folder / "note.md").exists():
        raise ValueError("unknown reel")
    busy_check(folder.name)
    if not isinstance(names, list):
        raise ValueError("bad snapshot list")
    with DATA_LOCK:
        st = read_state(folder)
        favs = {f["shot"] for f in st["favorites"]}
        if force:                                          # deleting favorites too: unstar them first
            drop = {str(n) for n in names} & favs
            if drop:
                st["favorites"] = [f for f in st["favorites"] if f["shot"] not in drop]
                write_state(folder, st)
                write_note_favorites(folder, st["favorites"])
                favs -= drop
        gone = []
        for n in names:
            n = str(n)
            if n in favs or not re.fullmatch(r"shot_\d+\.jpg", n):
                continue
            p = folder / "shots" / n
            if p.exists():
                p.unlink(); gone.append(n)
                o = folder / "shots" / "_orig" / n
                if o.exists():
                    o.unlink()
        man = manual_shots(folder)
        if any(n in man for n in gone):
            atomic_write((folder / "shots_manual.json"), json.dumps([n for n in man if n not in gone]))
        sj = folder / "shots.json"
        if sj.exists():
            try:
                d = json.loads(sj.read_text(encoding="utf-8", errors="replace"))
                atomic_write(sj, json.dumps({k: v for k, v in d.items() if k not in gone}, indent=1))
            except ValueError:
                pass
        note = folder / "note.md"
        text = note.read_text(encoding="utf-8", errors="replace")
        drop = {f"![[{folder.name}/shots/{n}]]" for n in gone}
        atomic_write(note, "\n".join(l for l in text.split("\n") if l.strip() not in drop))
    return {"deleted": gone}


def _unique(dest):
    if not dest.exists():
        return dest
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return dest.with_name(f"{dest.stem} ({stamp}){dest.suffix}")


def delete_reels(ids, with_obsidian):
    """Move reels to <out>/_trash (and optionally their vault notes to Obsidian's .trash)."""
    if not can_delete():
        raise PermissionError("Deleting videos is turned off for you on this laptop (set in the Cloudflare inbox)")
    if JOB["running"]:
        raise ValueError("Wait for the current run to finish before deleting")
    done, errors = [], []
    trash = OUT / "_trash"
    for rid in ids:
        try:
            folder = reel_dir(rid)
            vault_items = []
            if with_obsidian and vault_ok():           # work out what to move before the folder goes
                r = parse_note(folder)
                st = read_state(folder)
                if r and (st["note_name"] or st["note_path"]):
                    rel = find_vault_note(folder, r, st)
                    if rel and has_marker(read_text(VAULT / rel), folder.name):
                        vault_items.append((VAULT / rel, Path(rel).name))
                    for att in (VAULT / ATTACH_DIR / folder.name, VAULT / vault_dir(r["platform"]) / "attachments" / folder.name):
                        if att.exists():
                            vault_items.append((att, f"attachments {folder.name}"))
            trash.mkdir(exist_ok=True)
            with DATA_LOCK:
                try:                                   # a rename only: never copy-then-delete
                    os.replace(folder, _unique(trash / folder.name))
                except OSError:
                    raise ValueError("files in it are in use - stop the video / close Paint or Explorer and try again")
            vtrash = VAULT / ".trash"
            for src, name in vault_items:              # only after the reel itself was moved
                vtrash.mkdir(exist_ok=True)
                try:
                    os.replace(src, _unique(vtrash / name))
                except OSError as e:
                    errors.append(f"{rid}: Obsidian item not moved ({e})")
            done.append(rid)
        except Exception as e:
            errors.append(f"{rid}: {e}")
    return {"deleted": done, "errors": errors}


def change_cats(body):
    action = body.get("action")
    name = str(body.get("name", "")).strip()[:100]
    with DATA_LOCK:
        cats = read_cats()
        if action == "add":
            if not name:
                raise ValueError("Category name is empty")
            if name.lower() == "unassigned":
                raise ValueError("'Unassigned' is reserved")
            if name not in cats:
                cats.append(name)
        elif action == "rename":
            new = str(body.get("new", "")).strip()[:100]
            if not new or new.lower() == "unassigned":
                raise ValueError("Invalid new name")
            if name not in cats:
                raise ValueError("No such category")
            if new in cats and new != name:
                raise ValueError("That category already exists")
            cats[cats.index(name)] = new
            recategorize(name, new)
            write_cats(cats, [new if c == name else c for c in channel_cats()])
            return cats
        elif action == "delete":
            if name in cats:
                cats.remove(name)
            recategorize(name, "")
        elif action == "move":
            d = int(body.get("dir", 0))
            if name in cats:
                i = cats.index(name)
                j = max(0, min(len(cats) - 1, i + d))
                cats.insert(j, cats.pop(i))
        else:
            raise ValueError("Unknown action")
        write_cats(cats)
        return cats


# ---------------------------------------------------------------- sharing (Cloudflare inbox)
# "Share with <name>" sends the link, the favorites (time, text, Corrected mark, and the image only where you
# edited it), My notes and the category to the private Cloudflare inbox (cloud/ in this folder). The other
# laptop's Reels Hub picks it up into <reels>/_inbox (and deletes it from Cloudflare), shows it in its Inbox,
# and Add downloads and processes the video on that laptop, re-captures the favorites at the same times and
# fills in the rest. The video itself never travels. Each laptop signs in with its own Access service token.
class CloudError(ValueError):
    def __init__(self, msg, fatal=True):           # fatal: the inbox can't be used right now (offline, token)
        super().__init__(msg)
        self.fatal = fatal


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Access answers a refused token with a redirect to its login page: report it instead of following it."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def cloud_url_ok(url):
    u = urlparse(url)
    if u.scheme == "https" and u.hostname:
        return True
    return u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost")    # the workspace tests only


def cloud_settings(c=None):
    c = CONFIG.get("cloud") if c is None else c
    return c if isinstance(c, dict) and c.get("url") and c.get("id") and c.get("secret") else None


def cloud_call(method, path, body=None, c=None, raw=False, timeout=30):
    """One request to the Cloudflare inbox, signed in with this laptop's service token."""
    c = cloud_settings(c)
    if not c:
        raise CloudError("Sharing is not set up: add the Cloudflare address and this laptop's token in Settings")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(c["url"].rstrip("/") + path, data=data, method=method)
    req.add_header("CF-Access-Client-Id", c["id"])
    req.add_header("CF-Access-Client-Secret", c["secret"])
    req.add_header("User-Agent", "ReelsHub")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    host = urlparse(c["url"]).hostname or c["url"]
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as res:
            payload, ctype = res.read(40_000_000), res.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            raise CloudError("Cloudflare Access did not accept this laptop's token: check the Client ID and Client Secret in Settings")
        try:
            msg = json.loads(e.read(20000).decode("utf-8", "replace")).get("error")
        except (ValueError, AttributeError, OSError):
            msg = None
        if not msg and e.code in (401, 403):
            raise CloudError("Cloudflare Access refused this laptop's token: check the Client ID and Client Secret in Settings")
        raise CloudError(f"{msg or e.reason} ({e.code})", fatal=e.code >= 500 or e.code in (401, 403, 429))
    except urllib.error.URLError as e:
        raise CloudError(f"Can't reach {host}: {getattr(e, 'reason', e)}")
    except OSError as e:                            # timeouts, dropped connections
        raise CloudError(f"Can't reach {host}: {e}")
    if raw:
        return payload
    if "json" not in ctype.lower():
        raise CloudError("The Cloudflare address answered with a web page, not the inbox: check the address in Settings")
    try:
        return json.loads(payload.decode("utf-8"))
    except ValueError:
        raise CloudError("The inbox answered with something that is not JSON")


def _defuse(s):
    """Text from the other laptop must not carry Reels Hub's own markers into note.md or the vault note."""
    s = re.sub(r"(?i)<!--(\s*reels-hub)", r"<!- -\1", str(s or ""))
    return re.sub(r"(?im)^(\s*)reel_id:", r"\1reel_id :", s)


def _plain(s, n):
    """One line of text from the other laptop: no control characters or line breaks, no markers."""
    return _defuse(re.sub(r"[\s\x00-\x1f\x7f]+", " ", str(s or ""))).strip()[:n]


def cloud_whoami(c=None):
    """Who this laptop is in the inbox, whom it can share with, and whether this person may delete videos."""
    c = cloud_settings(c)
    j = cloud_call("GET", "/api/whoami", c=c, timeout=20)
    person = str(j.get("person") or "")
    if not person:
        raise CloudError("The inbox did not say who this laptop is")
    people = [{"person": str(p["person"]), "name": _plain(p.get("name") or p["person"], 60)}
              for p in (j.get("people") or []) if isinstance(p, dict) and p.get("person") and p.get("person") != person]
    return {"person": person, "name": _plain(j.get("name") or person, 60), "people": people,
            "can_delete": j.get("can_delete") is not False, "token": (c or {}).get("id"), "inbox": (c or {}).get("url")}


def remember_cloud(info):
    """Keep the inbox's answer in config.json: who this laptop is, whom it can share with, the delete right.
    An answer for a token or address that Settings has replaced in the meantime is dropped."""
    with CONFIG_LOCK:
        cfg = load_config()
        if not cfg or not cloud_settings(cfg.get("cloud") or {}):
            return
        if (cfg["cloud"].get("id"), cfg["cloud"].get("url")) != (info.get("token"), info.get("inbox")):
            return
        cfg["cloud"] = {**cfg["cloud"], "person": info["person"], "name": info["name"], "people": info["people"],
                        "checked": time.strftime("%Y-%m-%d %H:%M")}
        cfg["can_delete"] = info["can_delete"]
        atomic_write(config_path(), json.dumps(cfg, indent=1, ensure_ascii=False))
        CONFIG["cloud"] = cfg["cloud"]
        CONFIG["can_delete"] = cfg["can_delete"]


def cloud_test(body):
    """Settings, Test connection: try the address and token typed in the form (nothing is saved)."""
    c, _ = cloud_from_form(body, CONFIG.get("cloud") or {})
    if not c:
        raise ValueError("Fill in the Cloudflare address, Client ID and Client Secret first")
    info = cloud_whoami(c)
    return {k: info[k] for k in ("person", "name", "people", "can_delete")}


def _probe_duration(video):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
                            str(video)], capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def _smaller_jpeg(p):
    """A smaller JPEG copy of an image (ffmpeg), for an edited favorite that is too large to send."""
    fd, tmp = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(p), "-vf", "scale='min(1280,iw)':-2",
                        "-q:v", "5", tmp], capture_output=True, timeout=60)
        return Path(tmp).read_bytes()
    except (OSError, subprocess.SubprocessError):
        return b""
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _share_image(folder, shot):
    """The edited favorite image to send (JPEG bytes); None when it was not edited, b"" when too large."""
    p, o = folder / "shots" / shot, folder / "shots" / "_orig" / shot
    if not (p.is_file() and o.is_file()):
        return None
    raw = p.read_bytes()
    if raw == o.read_bytes():                       # opened in Paint but not changed
        return None
    if len(raw) > SHARE_IMG_MAX or not raw.startswith(b"\xff\xd8\xff"):
        raw = _smaller_jpeg(p)
    return raw if raw.startswith(b"\xff\xd8\xff") and len(raw) <= SHARE_IMG_MAX else b""


def build_share(folder, r, to):
    """What goes to the inbox for one reel, plus the edited images (uploaded one by one after it).
    Nothing else: no video, snapshots, transcript or vault note."""
    st = read_state(folder)
    allf, favs, images, notes, big = st["favorites"], [], [], [], 0
    if len(allf) > SHARE_FAVS_MAX:
        notes.append(f"only the first {SHARE_FAVS_MAX} of its {len(allf)} favorites went")
    for f in allf[:SHARE_FAVS_MAX]:
        item = {"t": round(float(r["times"].get(f["shot"], 0)), 3), "text": f["text"], "corrected": bool(f["corrected"]),
                "image": False}
        img = _share_image(folder, f["shot"]) if len(images) < SHARE_IMGS_MAX else None
        if img:
            item["image"] = True
            images.append(img)
        elif img == b"":
            big += 1
        favs.append(item)
    if big:
        notes.append(f"{big} edited image{'s were' if big > 1 else ' was'} too large and went as plain favorite{'s' if big > 1 else ''}")
    over = sum(1 for f in allf[:SHARE_FAVS_MAX] if f["shot"] in (r.get("edited") or [])) - SHARE_IMGS_MAX
    if over > 0:
        notes.append(f"{over} more edited images went as plain favorites (at most {SHARE_IMGS_MAX} per video)")
    cat = st["category"] if st["category"] in read_cats() else ""
    meta = {"to": to, "url": r["url"], "title": (r["label"] or r["title"] or "")[:300], "platform": (r["platform"] or "")[:40],
            "category": cat, "notes": (r["notes"] or "")[:100000], "favorites": favs}
    return meta, images, notes


def _send_share(meta, images):
    """Create the share, upload its edited images one per request, then mark it ready for the other person.
    If anything fails on the way, the half-sent share is dropped again."""
    res = cloud_call("POST", "/api/shares", meta, timeout=60)
    sid = str(res.get("id") or "")
    if not images:
        return sid
    if not SHARE_ID_RE.fullmatch(sid):
        raise CloudError("The inbox gave back no share id", fatal=False)
    try:
        for n, raw in enumerate(images):
            cloud_call("PUT", f"/api/shares/{sid}/image/{n}", {"data": base64.b64encode(raw).decode("ascii")}, timeout=120)
        cloud_call("POST", f"/api/shares/{sid}/ready", {}, timeout=30)
    except Exception:
        try:
            cloud_call("DELETE", f"/api/shares/{sid}", timeout=20)
        except Exception:
            pass                                    # the inbox drops unfinished shares after a day anyway
        raise
    return sid


def share_reels(ids, to):
    """Send reels to another person's inbox. Returns what was sent and why anything was not."""
    if not cloud_settings():
        raise ValueError("Sharing is not set up: add the Cloudflare address and this laptop's token in Settings")
    if not isinstance(ids, list) or not ids:
        raise ValueError("Nothing to share")
    if len(ids) > 200:
        raise ValueError("Share at most 200 reels at a time")
    people = {p["person"]: p for p in (CONFIG.get("cloud") or {}).get("people") or []}
    if to not in people:                            # the list may be old: ask the inbox once
        remember_cloud(cloud_whoami())
        people = {p["person"]: p for p in (CONFIG.get("cloud") or {}).get("people") or []}
        if to not in people:
            raise ValueError("That person is not in the inbox's PEOPLE list")
    name = people[to]["name"]
    done, errors, notes = [], [], []
    for k, rid in enumerate(ids):
        label = str(rid)
        try:
            folder = reel_dir(rid)
            busy_check(folder.name)
            r = parse_note(folder)
            if not r:
                raise ValueError("unknown reel")
            label = r["label"]
            if not r["url"]:
                raise ValueError("it has no source link")
            meta, images, extra = build_share(folder, r, to)
            _send_share(meta, images)
            with DATA_LOCK:
                st = read_state(folder)
                if not st.get("_bad"):
                    st["shared"] = st["shared"][-9:] + [{"to": to, "name": name, "at": time.strftime("%Y-%m-%d %H:%M")}]
                    write_state(folder, st)
            done.append({"id": folder.name, "shared": st["shared"]})
            notes += [f"{label}: {x}" for x in extra]
        except CloudError as e:
            errors.append(f"{label}: {e}")
            if e.fatal:                             # offline or token refused: don't wait for each of the others
                if len(ids) - k - 1:
                    errors.append(f"{len(ids) - k - 1} more not sent")
                break
        except Exception as e:
            errors.append(f"{label}: {e}")
    return {"shared": done, "errors": errors, "notes": notes, "name": name}


def inbox_dir():
    return OUT / "_inbox"


def _seen():
    """Shares already picked up (id -> time), so one that was added or dismissed never comes back."""
    try:
        d = json.loads((inbox_dir() / "_seen.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _mark_seen(sid):
    d = _seen()
    d[sid] = int(time.time())
    if len(d) > 1000:
        d = dict(sorted(d.items(), key=lambda kv: kv[1])[-1000:])
    inbox_dir().mkdir(parents=True, exist_ok=True)
    atomic_write(inbox_dir() / "_seen.json", json.dumps(d))


def _fetch_image(sid, n):
    """An edited image of a share (JPEG bytes), or b"" if it can't be had: that favorite is then re-captured."""
    try:
        raw = cloud_call("GET", f"/api/inbox/{sid}/image/{n}", raw=True, timeout=60)
        data = base64.b64decode(raw.strip(), validate=True)
    except CloudError as e:
        if e.fatal:
            raise
        return b""
    except ValueError:                              # not base64
        return b""
    return data if data[:3] == b"\xff\xd8\xff" and len(data) <= 2_000_000 else b""


def _save_share(s, sid):
    """Keep one share in <reels>/_inbox/<id>/ (share.json + the edited images). False if it can't be used.
    Everything from the other laptop is cleaned on the way in: markers, control characters, lengths."""
    url = str(s.get("url") or "").strip()
    if not re.fullmatch(r"https?://\S{3,2000}", url):
        return False
    tmp = inbox_dir() / f".tmp-{sid}"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    favs, lost = [], 0
    for i, f in enumerate((s.get("favorites") or [])[:SHARE_FAVS_MAX]):
        if not isinstance(f, dict):
            continue
        try:
            t = float(f.get("t") or 0)
        except (TypeError, ValueError):
            continue
        if not 0 <= t <= 86400:
            continue
        img, n = "", f.get("image")
        if isinstance(n, int) and not isinstance(n, bool) and 0 <= n < 100:
            raw = _fetch_image(sid, n)
            if raw:
                img = f"img_{i:03d}.jpg"
                (tmp / img).write_bytes(raw)
            else:
                lost += 1
        favs.append({"t": round(t, 3), "text": _defuse(f.get("text"))[:2000], "corrected": bool(f.get("corrected")), "image": img})
    plat = str(s.get("platform") or "")
    item = {"id": sid, "from": _plain(s.get("from"), 40), "from_name": _plain(s.get("from_name") or s.get("from"), 60),
            "sent": _plain(s.get("created"), 40), "received": time.strftime("%Y-%m-%d %H:%M"), "url": url,
            "title": _plain(s.get("title"), 300), "platform": plat if plat in PLATFORMS else platform_of(url),
            "category": _plain(s.get("category"), 100), "notes": _defuse(s.get("notes"))[:100000],
            "favorites": favs, "lost_images": lost}
    atomic_write(tmp / "share.json", json.dumps(item, indent=1, ensure_ascii=False))
    os.replace(tmp, inbox_dir() / sid)
    return True


def pickup():
    """Fetch the shares waiting for this laptop into <reels>/_inbox, then delete them from Cloudflare.
    Each share on its own: one that fails waits for the next round without holding up the others."""
    shares = cloud_call("GET", "/api/inbox").get("shares") or []
    seen, problems = _seen(), []
    for s in shares:
        sid = str((s or {}).get("id") or "")
        if not SHARE_ID_RE.fullmatch(sid):
            continue
        try:
            if sid not in seen and not (inbox_dir() / sid).exists():
                _save_share(s, sid)
            _mark_seen(sid)
            cloud_call("DELETE", f"/api/inbox/{sid}")    # picked up: no longer kept in Cloudflare
        except CloudError as e:
            if e.fatal:                                 # offline or token refused: try again next round
                raise
            problems.append(str(e))
        except Exception as e:
            problems.append(f"a share from {_plain((s or {}).get('from_name'), 60) or 'someone'} could not be saved ({e})")
    if problems:
        raise ValueError("; ".join(problems[:3]))


def _load_item(sid):
    if not isinstance(sid, str) or not SHARE_ID_RE.fullmatch(sid):
        raise ValueError("bad share id")
    try:
        it = json.loads((inbox_dir() / sid / "share.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("That share is no longer in the inbox")
    if not isinstance(it, dict) or it.get("id") != sid or not isinstance(it.get("favorites"), list):
        raise ValueError("That share is damaged")
    return it


def inbox_status():
    items, d = [], inbox_dir()
    if d.is_dir():
        for p in d.iterdir():
            if p.is_dir() and SHARE_ID_RE.fullmatch(p.name):
                try:
                    items.append(_load_item(p.name))
                except ValueError:
                    continue
    items.sort(key=lambda x: str(x.get("sent") or x.get("received") or ""))
    have = {}                                       # videos you already have, by folder name (<date>_<video id>)
    if items:
        names = [p.name for p in OUT.iterdir() if p.is_dir() and "_" in p.name and not p.name.startswith(("_", "."))]
        for it in items:
            u = str(it.get("url") or "")
            for name in names:
                vid = re.sub(r"_v\d+$", "", name.split("_", 1)[1])
                if len(vid) >= 4 and vid in u and (OUT / name / "note.md").exists():
                    have.setdefault(it["id"], []).append(name)
    with INBOX_LOCK:
        queued = [q["id"] for q in INBOX["queue"]]
        for it in items:
            it["status"] = "adding" if INBOX["adding"] == it["id"] else ("queued" if it["id"] in queued else "")
            it["error"] = INBOX["errors"].get(it["id"], "")
            it["have"] = sorted(have.get(it["id"], []))
        return {"on": bool(cloud_settings()), "items": items, "checked": INBOX["checked"], "error": INBOX["error"],
                "busy": INBOX["busy"], "last": INBOX["last"], "as": (CONFIG.get("cloud") or {}).get("name", ""),
                "running": JOB["running"], "changed": CHANGED["list"], "boot": BOOT}


def inbox_add(body):
    """Add (queue) a share: its video is downloaded and processed on this laptop, then filled in."""
    sid = str(body.get("id") or "")
    item = _load_item(sid)
    with INBOX_LOCK:
        if INBOX["adding"] == sid or any(q["id"] == sid for q in INBOX["queue"]):
            raise ValueError("It is already being added")
        part = INBOX["partial"].get(sid)
    category = _plain(body.get("category"), 100)
    if category.lower() == "unassigned":
        category = ""
    max_shots = shots_setting(body.get("max_shots"))
    task = {"kind": "add", "id": sid, "category": category, "new": False, "max_shots": max_shots,
            "label": f"From {item.get('from_name') or item.get('from') or 'the inbox'}: {item.get('title') or short_url(item['url'])}",
            "platform": item.get("platform") or platform_of(item["url"])}
    fill = None
    if part:                                        # downloaded before, but the fill-in failed: only fill in now
        try:
            fill = reel_dir(part)
        except (PermissionError, ValueError):
            fill = None
        if fill is not None and ((not (fill / "note.md").exists()) or read_state(fill).get("shared_by")):
            fill = None
    if fill is not None:
        task["fill"] = fill.name
    else:
        mode = body.get("existing")
        if mode is None:
            hits = find_done(item["url"])
            if hits:                                # you have this video already: ask first (new version or open yours)
                return {"existing": [{"id": f.name, "label": (parse_note(f) or {}).get("label", f.name)} for f in hits[:6]],
                        "total": 1}
        elif mode != "new":
            raise ValueError("Bad choice")
        task["new"] = mode == "new"
    with INBOX_LOCK:
        if INBOX["adding"] == sid or any(q["id"] == sid for q in INBOX["queue"]):
            raise ValueError("It is already being added")
        INBOX["errors"].pop(sid, None)
        task["seq"] = next(QSEQ)
        INBOX["queue"].append(task)
        n = len(INBOX["queue"]) + len(RUNQ) + bool(JOB["running"])   # this one and all that run before it
    start_next()
    return {"queued": n}


def inbox_dismiss(body):
    """Dismiss a share: it moves to <reels>/_trash/_inbox (not added; can be restored by hand)."""
    sid = str(body.get("id") or "")
    _load_item(sid)
    with INBOX_LOCK:
        if INBOX["adding"] == sid:
            raise ValueError("It is being added right now - wait until it finishes")
        INBOX["queue"] = [q for q in INBOX["queue"] if q["id"] != sid]
        INBOX["errors"].pop(sid, None)
        INBOX["partial"].pop(sid, None)
        trash = OUT / "_trash" / "_inbox"
        trash.mkdir(parents=True, exist_ok=True)
        os.replace(inbox_dir() / sid, _unique(trash / sid))
    return {"dismissed": sid}


# ---------------------------------------------------------------- run queue
# One run at a time (Whisper and OCR keep the processor busy). Run, Re-process, New version and inbox Adds pressed
# while a run is going wait in line and start by themselves, first come first served. When a run ends it hands the
# slot straight to the next one (JOB["running"] stays true, so the page sees one long run) and the log keeps the
# earlier lines, so a video that failed further up can still be read.
def short_url(u):
    """A link as the waiting list shows it: no https://, www., query or trailing slash (a YouTube ?v= stays)."""
    u = str(u or "")
    s = re.sub(r"^https?://(www\.|m\.)?", "", u, flags=re.I).split("#", 1)[0].split("?", 1)[0].rstrip("/")
    v = re.search(r"[?&]v=([\w-]+)", u)
    if v and s.lower().endswith("youtube.com/watch"):
        s += "?v=" + v.group(1)
    return s if len(s) <= 70 else s[:67] + "..."


def _take_next():
    """With INBOX_LOCK and JOB_LOCK held: the task that has waited longest (a Run or an inbox Add), out of its line."""
    run = RUNQ[0] if RUNQ else None
    add = INBOX["queue"][0] if INBOX["queue"] else None
    if run and (add is None or run["seq"] < add.get("seq", 0)):
        return RUNQ.pop(0)
    return INBOX["queue"].pop(0) if add else None


def _claim(task, chained):
    """With both locks held and the slot taken: get a waiting task ready to start. Returns its thread, or None
    when it can no longer run (an Add's reason goes to the Inbox, a Run's to the log)."""
    if task.get("kind") == "add":
        try:
            item = _load_item(task["id"])
        except ValueError as e:
            INBOX["errors"][task["id"]] = str(e)
            return None
        INBOX["adding"] = task["id"]
        task = {**task, "item": item}
        JOB["busy"] = {task["fill"]} if task.get("fill") else set()
        if task.get("fill"):
            return threading.Thread(target=run_fill, args=(task, chained), daemon=True)
        flags = ["--new-version"] if task["new"] else []
        return threading.Thread(target=run_job, args=([item["url"]], task["max_shots"], False, flags, None, task,
                                                      task["label"], chained), daemon=True)
    target = task["target"]
    if target and not (OUT / target / "note.md").exists():
        JOB["log"].append(f"-- left out {task['label']}: that video is no longer in your library")
        return None
    JOB["busy"] = {target} if (target and task["redo"]) else set()
    return threading.Thread(target=run_job, args=(task["urls"], task["max_shots"], task["redo"], task["flags"],
                                                  target if task["redo"] else None, None, task["label"], chained),
                            daemon=True)


def start_next(after_run=False):
    """Give the run slot to what has waited longest. after_run: called by the run that just ended, which still
    holds the slot: it passes straight on, or goes free when nothing waits."""
    hold = after_run
    while True:
        with INBOX_LOCK:
            with JOB_LOCK:
                if JOB["running"] and not hold:
                    return                          # the run going now starts the next one when it ends
                task = _take_next()
                JOB["running"] = task is not None
                if task is None:
                    return
                hold = True                         # from here on this call holds the slot
                try:
                    th = _claim(task, after_run)
                except Exception as e:              # never leave the slot taken by a task that can't start
                    JOB["log"].append(f"-- could not start {task.get('label') or 'the next run'}: {e}")
                    th = None
        if th:
            try:
                th.start()
            except Exception as e:                  # no thread: free the slot again rather than block every run
                with INBOX_LOCK:
                    INBOX["adding"] = None
                    with JOB_LOCK:
                        JOB.update(running=False, busy=set(), share=None)
                        JOB["log"].append(f"-- could not start {task.get('label') or 'the next run'}: {e}")
            return


def end_run():
    """The run that holds the slot is over: start the next one waiting, or free the slot."""
    JOB.update(finished=time.time(), progress=None, busy=set(), share=None)   # running stays true while passing on
    try:
        start_next(after_run=True)
    except Exception as e:
        print(f"  run queue: {e}")
        JOB["running"] = False


def new_log(chained, label):
    """A run starts: a new log, or after a run in line the earlier lines stay, with a header for this one."""
    if chained and JOB["log"]:
        JOB["log"] = JOB["log"][-LOG_KEEP:] + ["", f"== next in line: {label}"]
    else:
        JOB["log"] = []
    JOB["mark"], JOB["done"] = len(JOB["log"]), 0


def run_lines():
    """This run's own lines of the log (the log can hold earlier runs' lines too)."""
    return JOB["log"][JOB["mark"]:]


def queue_list():
    """What waits for the run slot, in the order it will start (for the page's Waiting list)."""
    with INBOX_LOCK:
        L = [{"seq": t["seq"], "kind": "run", "label": t["label"], "platform": t["platform"], "target": t["target"],
              "redo": t["redo"], "transcripts": bool(t.get("transcripts"))} for t in RUNQ]
        L += [{"seq": t.get("seq", 0), "kind": "add", "label": t.get("label") or "From the inbox",
               "platform": t.get("platform") or "", "target": "", "redo": False, "share": t["id"]}
              for t in INBOX["queue"]]
    return sorted(L, key=lambda x: x["seq"])


def queue_remove(body):
    """Take a Run or an inbox Add out of the line before it starts (the Add's share stays in the Inbox)."""
    try:
        seq = int(body.get("seq"))
    except (TypeError, ValueError):
        raise ValueError("Bad request")
    with INBOX_LOCK:
        for line in (RUNQ, INBOX["queue"]):
            for i, t in enumerate(line):
                if t.get("seq") == seq:
                    line.pop(i)                     # pasted links go back to the page's box, so none is lost
                    return {"removed": seq, "urls": t["urls"] if t.get("kind") == "run" and not t.get("target") else []}
    raise ValueError("It is not waiting any more: it has started or finished")


def lined_up():
    """Links that are running now or waiting in line."""
    with INBOX_LOCK:
        s = set(JOB["urls"]) if JOB["running"] else set()
        for t in RUNQ:
            s.update(t["urls"])
        for t in INBOX["queue"]:
            try:
                s.add(_load_item(t["id"])["url"])
            except ValueError:
                pass
    return s


def queue_run(task):
    """Put a Run in line and start it if the slot is free. Returns how many run before it (0: it started now)."""
    with INBOX_LOCK:
        task["seq"] = next(QSEQ)
        RUNQ.append(task)
    start_next()
    with INBOX_LOCK:
        if not any(t is task for t in RUNQ):
            return 0
        return sum(1 for q in queue_list() if q["seq"] < task["seq"]) + bool(JOB["running"])


def run_fill(task, chained=False):
    """Add again after a failed fill-in: the video is already here, so only the share is filled in."""
    new_log(chained, task.get("label") or "a share")
    JOB["log"].append(f"-- filling in {task['item'].get('from_name') or 'the'}'s share again (the video is already here)")
    JOB.update(started=time.time(), finished=None, urls=[task["item"]["url"]], progress=None, busy={task["fill"]},
               share=task["id"])
    try:
        _finish_add(task, 0, [{"url": task["item"]["url"], "folder": task["fill"]}])
        JOB["log"].append("-- finished")
    except Exception as e:
        JOB["log"].append(f"-- could not fill in: {e}")
    finally:
        with INBOX_LOCK:
            INBOX["adding"] = None
        end_run()


def atomic_write_bytes(path, raw):
    """Like atomic_write, for an image: temp file + rename, retried while Windows has the file open."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(raw)
    for i in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)
    try:
        tmp.unlink()
    except OSError:
        pass
    raise PermissionError(f"could not write {path.name} (open in another program?)")


def apply_share(folder, item, category):
    """Fill a freshly processed reel from a share: the favorites re-captured at the sender's times (with the
    sender's edited image where there is one), their text and Corrected mark, My notes, the category, and who
    shared it. One favorite that fails does not stop the others."""
    vids = [p for p in folder.glob("video.*") if p.suffix.lower() in VIDEO_EXT]
    if not vids:
        raise ValueError("no video file")
    dur = _probe_duration(vids[0])
    src = inbox_dir() / item["id"]
    favs, seen, missed, frames = [], set(), 0, 0
    for f in item.get("favorites") or []:          # _capture takes DATA_LOCK for each frame, not for all of them
        try:
            t = float(f.get("t") or 0)
            tries = [t] + ([max(0.0, dur - 0.3)] if dur and t > dur - 0.3 else [])   # this copy may be a bit shorter
            shot = None
            for tt in tries:
                try:
                    shot = _capture(folder, tt, manual=False)["shot"]
                    break
                except ValueError:
                    continue
            if not shot:
                missed += 1
                continue
            if shot in seen:
                continue
            seen.add(shot)
            img = src / f["image"] if f.get("image") else None
            if img and img.is_file():
                raw = img.read_bytes()
                if raw[:3] == b"\xff\xd8\xff":
                    p = folder / "shots" / shot
                    try:
                        with DATA_LOCK:
                            _backup(p)              # the re-captured frame stays as the original (Reset to original)
                            atomic_write_bytes(p, raw)
                    except OSError:
                        frames += 1                 # file in use: the re-captured frame stays instead
            favs.append({"shot": shot, "text": _defuse(f.get("text"))[:2000], "corrected": bool(f.get("corrected"))})
        except Exception:
            missed += 1
    cat = _plain(category, 100)
    with DATA_LOCK:
        if cat:
            try:
                cats = read_cats()
                same = next((c for c in cats if c.lower() == cat.lower()), None)
                if same:
                    cat = same
                else:
                    cats.append(cat)
                    write_cats(cats)
            except OSError:
                cat = ""
        st = read_state(folder)
        st["favorites"] = favs
        if cat:
            st["category"] = cat
        st["shared_by"] = {"person": _plain(item.get("from"), 40), "name": _plain(item.get("from_name"), 60),
                           "at": time.strftime("%Y-%m-%d %H:%M"), "share": item["id"]}
        write_state(folder, st)
        write_note_favorites(folder, favs)
        if str(item.get("notes") or "").strip():
            _write_notes(folder, _defuse(item["notes"]))
    return {"favorites": len(favs), "missed": missed, "frames": frames, "category": cat}


def _finish_add(task, code, done):
    """After the run of an Add: fill in the share, or keep it in the inbox with the reason it failed."""
    item, sid = task["item"], task["id"]
    folder = None
    for d in done:
        if not d.get("skipped") and isinstance(d.get("folder"), str):
            try:
                folder = reel_dir(d["folder"])
            except (PermissionError, ValueError):
                folder = None
    if code != 0 or folder is None or not (folder / "note.md").exists():
        if any(d.get("skipped") for d in done):
            msg = "This video is already in your library: press Add again and choose New version"
        else:                                       # "Downloading failed: <the site's error>", not the whole command
            lines = run_lines()                     # this run's lines only, not an earlier run's error
            fail = next((l.split("FAILED:", 1)[1].strip() for l in reversed(lines) if "FAILED:" in l), "")
            err = next((l.strip()[6:].strip() for l in reversed(lines) if l.strip().startswith("ERROR:")), "")
            fail = fail.split(" - Command '", 1)[0]
            msg = ((f"{fail}: {err}" if fail and err else fail or err) or "The run did not finish (see Log)")[:400]
        with INBOX_LOCK:
            INBOX["errors"][sid] = msg
        JOB["log"].append(f"-- not added from the inbox: {msg}")
        return
    try:
        res = apply_share(folder, item, task["category"])
    except Exception as e:
        with INBOX_LOCK:
            INBOX["errors"][sid] = (f"The video is in your library ({folder.name}), but the favorites could not be "
                                    f"filled in: {e}. Press Add to try the fill-in again.")[:500]
            INBOX["partial"][sid] = folder.name
        JOB["log"].append(f"-- the share could not be filled in: {e}")
        mark_changed(folder.name)
        return
    shutil.rmtree(inbox_dir() / sid, ignore_errors=True)    # everything it held is in the reel now
    mark_changed(folder.name)
    with INBOX_LOCK:
        INBOX["partial"].pop(sid, None)
        INBOX["last"] = {"id": sid, "folder": folder.name, "title": item.get("title", ""),
                         "from_name": item.get("from_name", ""), "at": time.time()}
    n = res["favorites"]
    JOB["log"].append(f"-- added {item.get('from_name') or 'the'}'s share: {n} favorite{'' if n == 1 else 's'}"
                      + (", notes" if str(item.get("notes") or "").strip() else "")
                      + (f", category {res['category']}" if res["category"] else "")
                      + (f" ({res['missed']} favorite time{'s' if res['missed'] > 1 else ''} could not be captured)" if res["missed"] else "")
                      + (f" ({res['frames']} edited image{'s' if res['frames'] > 1 else ''} in use: the plain frame was kept)" if res["frames"] else ""))


def inbox_worker():
    """Background: pick up shares from Cloudflare now and then (or when asked), and start queued Adds."""
    last = 0.0
    while True:
        INBOX_WAKE.wait(5)
        INBOX_WAKE.clear()
        with INBOX_LOCK:
            force, INBOX["force"] = INBOX["force"], False
        if CONFIG.get("name") and cloud_settings() and (force or time.time() - last >= PICKUP_EVERY):
            last = time.time()
            with INBOX_LOCK:
                INBOX["busy"] = True
            try:
                if time.time() - INBOX["whoami_at"] >= WHOAMI_EVERY:
                    remember_cloud(cloud_whoami())
                    INBOX["whoami_at"] = time.time()
                pickup()
                with INBOX_LOCK:
                    INBOX.update(checked=time.strftime("%H:%M"), error="")
            except Exception as e:
                with INBOX_LOCK:
                    INBOX.update(checked=time.strftime("%H:%M"), error=str(e))
            finally:
                with INBOX_LOCK:
                    INBOX["busy"] = False
        try:
            start_next()                        # backstop: anything waiting while the slot is free starts now
        except Exception as e:
            print(f"  run queue: {e}")


# ---------------------------------------------------------------- job runner
def find_done(url):
    """Reel folders that already hold a finished copy of this link (same video id or same page)."""
    hits = []
    for info_file in sorted(OUT.glob("*/video.info.json")):
        folder = info_file.parent
        if folder.name.startswith("_") or not (folder / "note.md").exists():
            continue
        try:
            info = json.loads(info_file.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            continue
        if (info.get("id") and str(info["id"]) in url) or url.rstrip("/") in (
                str(info.get("webpage_url", "")).rstrip("/"), str(info.get("original_url", "")).rstrip("/")):
            hits.append(folder)
    return hits


def shots_setting(v):
    """The snapshot choice from the page: 'auto', a maximum (10 to 1000), or 'every:N' (seconds, 0.5 to 600)."""
    ms = str(v or "auto").strip().lower()
    try:
        if ms.startswith("every:"):
            return f"every:{min(600.0, max(0.5, float(ms.split(':', 1)[1]))):g}"
        return "auto" if ms == "auto" else str(max(10, min(1000, int(ms))))
    except ValueError:
        return "auto"


def run_job(urls, max_shots="auto", redo=False, flags=(), target=None, share=None, label="", chained=False):
    # JOB["running"] was already claimed under JOB_LOCK (by start_next, for a Run or an inbox Add)
    new_log(chained, label or short_url(urls[0] if urls else ""))
    JOB.update(started=time.time(), finished=None, urls=urls, progress=None, busy={target} if target else set(),
               share=share["id"] if share else None, total=None, transcripts="--transcripts" in flags, vault_warned=False,
               paused=False, stopping=False, pid=None)
    if "--transcripts" in flags:                # a Pause or Stop left over from an earlier run must not hold this one
        try:
            (OUT / TR_DIR).mkdir(parents=True, exist_ok=True)
            (OUT / TR_DIR / TR_CONTROL).unlink(missing_ok=True)
        except OSError:
            pass
    if share:
        JOB["log"].append(f"-- adding {share['item'].get('from_name') or 'a'}'s share from the inbox")
    done = []                                   # @@DONE lines from reels2notes: which folder each link went to
    try:
        _run_job(urls, max_shots, redo, flags, target, share, done)
    finally:
        with INBOX_LOCK:
            if share:
                INBOX["adding"] = None
        end_run()                               # the next one in line starts now


def _transcript_done(d, channels):
    """A transcript-only video is done: create its Obsidian note; remember its channel for the channel note."""
    try:
        parts = d["folder"].replace("\\", "/").split("/")
        if len(parts) != 3 or parts[0] != TR_DIR:
            raise ValueError("unexpected folder " + d["folder"])
        folder = tr_dir(parts[1], parts[2])
        from_channel = d.get("from") == "channel"
        note = transcript_note(folder, from_channel)
        if note.get("path"):
            JOB["log"].append(f"  Obsidian note: {note['path']}")
        if from_channel:
            channels.add(parts[1])
            try:
                _auto_category(folder)
            except Exception as e:
                JOB["log"].append(f"  category not set: {e}")
        elif note.get("error") and not JOB.get("vault_warned"):
            JOB["log"].append(f"  Obsidian: {note['error']} (no notes this run)")
            JOB["vault_warned"] = True
    except Exception as e:
        JOB["log"].append(f"  Obsidian: {e}")


def _run_job(urls, max_shots, redo, flags, target, share, done):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    cmd = [sys.executable, "-u", str(SCRIPT), "--out", str(OUT), "--max-shots", str(max_shots)]
    if CONFIG.get("person"):
        cmd += ["--owner", CONFIG["person"]]
    if redo:
        cmd.append("--redo")
    cmd += list(flags)
    if not share and not redo and "--transcripts" not in flags:   # an inbox Add takes the sender's category instead
        cmd.append("--channel-category")
    if "--transcripts" in flags:
        cmd += ["--prefer-lang", str(CONFIG.get("sub_lang", "en") if "sub_lang" in CONFIG else "en")]
    cookies = HERE / "cookies.txt"          # optional: login cookies next to the app (git-ignored)
    if cookies.exists():
        cmd += ["--cookies", str(cookies)]
        JOB["log"].append("-- using cookies.txt for sites that need login")
    cmd += urls
    channels = set()                        # transcripts run: channels whose own note is written at the end
    try:
        proc = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", env=env)
        JOB["pid"] = proc.pid
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("@@PROGRESS "):
                try:
                    JOB["progress"] = {**json.loads(line[11:]), "at": time.time()}
                except ValueError:
                    pass
                continue
            if line.startswith("@@TOTAL "):        # transcripts run: how many videos it will do
                try:
                    JOB["total"] = int(json.loads(line[8:]).get("n"))
                except (ValueError, TypeError, AttributeError):
                    pass
                continue
            if line.startswith("@@DONE "):
                try:
                    d = json.loads(line[7:])
                except ValueError:
                    continue
                if isinstance(d, dict):
                    done.append(d)
                    if share and not d.get("skipped") and isinstance(d.get("folder"), str):
                        JOB["busy"].add(d["folder"])   # no edits from the page until the share is filled in
                    if d.get("transcripts") and not d.get("skipped") and isinstance(d.get("folder"), str):
                        _transcript_done(d, channels)  # its Obsidian note, now
                continue
            if line.startswith("@@CHANNEL "):
                continue                               # channel notes are written below, once per channel
            if line and "warnings.warn" not in line:
                JOB["log"].append(line)
                if DONE_LINE.match(line) or "already done" in line:
                    JOB["done"] += 1
        proc.wait()
        JOB.update(paused=False, stopping=False)
        if "--transcripts" in flags:
            try:
                (OUT / TR_DIR / TR_CONTROL).unlink(missing_ok=True)
            except OSError:
                pass
        for key in sorted(channels):                   # the channel notes list every video, so once at the end
            try:
                rel = channel_note(key)
                if rel:
                    JOB["log"].append(f"-- channel note: {rel}")
            except Exception as e:
                JOB["log"].append(f"-- channel note of {key}: {e}")
        if share:
            _finish_add(share, proc.returncode, done)
        if redo and target and proc.returncode == 0:   # snapshots were replaced: old favorites no longer apply
            with DATA_LOCK:
                folder = OUT / target
                st = read_state(folder)
                if not st.get("_bad") and st["favorites"]:
                    st["favorites"] = []; write_state(folder, st); write_note_favorites(folder, [])
                    JOB["log"].append("-- favorites cleared (the snapshots were replaced)")
        elif redo and target:
            JOB["log"].append("-- the run failed, so this video's favorites were kept")
        JOB["log"].append(f"-- finished (exit code {proc.returncode})")
    except Exception as e:
        JOB["log"].append(f"-- could not start: {e}")
        if share:
            with INBOX_LOCK:
                INBOX["errors"][share["id"]] = f"Could not start: {e}"


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def allowed(self, post=False):
        """Only this app's own page may talk to the server: blocks other websites and DNS-rebinding tricks."""
        hosts = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
        if self.headers.get("Host", "") not in hosts:
            return False
        if post:
            origin = self.headers.get("Origin")
            if origin is not None and origin not in {f"http://{h}" for h in hosts}:
                return False
            if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                return False
        return True

    def send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.allowed():
            return self.send_error(403)
        try:
            self._get()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass
        except Exception as e:
            try:
                self.send_json({"error": f"Server error: {e}"}, 500)
            except Exception:
                pass

    def _get(self):
        path = unquote(urlparse(self.path).path)
        if path == "/":
            data = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif path == "/api/ping":                     # the page's heartbeat (status "Connected to this PC")
            self.send_json({"ok": True, "boot": BOOT})
        elif path == "/api/config":
            self.send_json(config_info())
        elif path == "/api/reels":
            cl = CONFIG.get("cloud") or {}
            seq = CHANGED["seq"]                     # read before listing: later fill-ins count as changed
            self.send_json({"reels": list_reels() if CONFIG.get("name") else [], "categories": read_cats(),
                            "channel_cats": channel_cats(), "tags": read_tags(),
                            "boot": BOOT, "changed_seq": seq,
                            "me": {"name": CONFIG.get("name", ""), "person": CONFIG.get("person", ""),
                                   "can_delete": can_delete(),
                                   "share": {"on": bool(cloud_settings()), "people": cl.get("people") or [],
                                             "as": cl.get("name", "")}},
                            "vault": {"path": str(VAULT), "ok": vault_ok()}})
        elif path == "/api/inbox":
            self.send_json(inbox_status())
        elif path == "/api/transcripts":
            self.send_json(list_transcripts() if CONFIG.get("name") else {"channels": [], "items": []})
        elif path == "/api/status":
            prog = JOB["progress"]
            if prog:   # age the ETA by the time since the last update
                prog = {**prog, "eta": None if prog.get("eta") is None else max(0, prog["eta"] - (time.time() - prog["at"])),
                        "elapsed": prog["elapsed"] + (time.time() - prog["at"])}
            self.send_json({"running": JOB["running"], "log": JOB["log"][-300:], "urls": JOB["urls"],
                            "finished": JOB["finished"], "progress": prog, "share": JOB["share"],
                            "done": JOB["done"], "busy": sorted(JOB["busy"]) if JOB["running"] else [],
                            "total": JOB.get("total") if JOB.get("total") is not None else len(JOB["urls"]),
                            "transcripts": bool(JOB.get("transcripts")),
                            "paused": bool(JOB.get("paused")) and JOB["running"], "stopping": bool(JOB.get("stopping")) and JOB["running"],
                            "queue": queue_list(), "changed": CHANGED["list"], "boot": BOOT,
                            "total_elapsed": time.time() - JOB["started"] if JOB["started"] else 0})
        elif path.startswith("/media/"):
            self.serve_file(path[len("/media/"):])
        else:
            self.send_error(404)

    def do_POST(self):
        if not self.allowed(post=True):
            return self.send_error(403)
        path = urlparse(self.path).path
        try:
            length = max(0, min(int(self.headers.get("Content-Length", 0)), 50_000_000))
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.send_json({"error": "Bad request"}, 400)
        if not isinstance(body, dict):
            return self.send_json({"error": "Bad request"}, 400)
        try:
            if path == "/api/run":                           # starts now, or waits in line behind the run going now
                urls = [u.strip() for u in re.split(r"[\s,]+", str(body.get("urls") or "")) if u.strip().startswith("http")]
                urls = list(dict.fromkeys(urls))
                if not urls and not body.get("target"):
                    return self.send_json({"error": "No links found"}, 400)
                max_shots = shots_setting(body.get("max_shots"))
                flags, redo, skipped = [], False, 0
                target = body.get("target") or ""
                if target:                                   # a button on one reel
                    folder = reel_dir(target)
                    target = folder.name
                    if not (folder / "note.md").exists():
                        raise ValueError("unknown reel")
                    r = parse_note(folder)
                    if not r or not r["url"]:
                        raise ValueError("This reel has no source link")
                    urls = [r["url"]]
                    flags = ["--target", target]
                    if body.get("action") == "redo":         # favorites are cleared only if the run succeeds
                        redo = True
                        with INBOX_LOCK:
                            if (JOB["running"] and target in JOB["busy"]) or any(t["redo"] and t["target"] == target for t in RUNQ):
                                raise ValueError("This video is already being re-processed, or waiting for it")
                    else:
                        flags.append("--new-version")
                    label = f"{'Re-process' if redo else 'New version'}: {r.get('label') or target}"
                else:
                    taken = lined_up()                       # a link running now or waiting is not queued twice
                    skipped = sum(1 for u in urls if u in taken)
                    urls = [u for u in urls if u not in taken]
                    if not urls:
                        raise ValueError("Already running or waiting in line" if skipped == 1
                                         else "These links are already running or waiting in line")
                    label = (f"{len(urls)} links: " if len(urls) > 1 else "") + short_url(urls[0])   # count first: it stays visible
                    mode = body.get("existing")              # what to do with links that are already done
                    listing = next((u for u in urls if tr_listing(u)), None)
                    if listing and not body.get("transcripts"):
                        raise ValueError(f"{short_url(listing)} is a channel, profile or playlist link: press Transcripts for it (Run takes single videos)")
                    if body.get("transcripts"):              # transcripts only: videos, channels, playlists
                        flags.append("--transcripts")
                        label = "Transcripts: " + (f"{len(urls)} links" if len(urls) > 1 else tr_label(urls[0]))
                        mode = "skip"                        # the pipeline leaves out what is already here
                    if mode is None:
                        done = [(u, f) for u in urls for f in find_done(u)[:1]]
                        if done:
                            return self.send_json({"existing": [
                                {"url": u, "id": f.name, "label": (parse_note(f) or {}).get("label", f.name)} for u, f in done],
                                "total": len(urls)})
                    elif mode == "new":
                        flags.append("--new-version")
                ahead = queue_run({"kind": "run", "urls": urls, "max_shots": max_shots, "redo": redo, "flags": flags,
                                   "target": target, "label": label, "platform": platform_of(urls[0]),
                                   "transcripts": "--transcripts" in flags})
                self.send_json({"ok": True, "count": len(urls), "queued": ahead, "skipped": skipped})
            elif path == "/api/queue/remove":
                self.send_json({"ok": True, **queue_remove(body)})
            elif path == "/api/config":
                self.send_json({"ok": True, **save_config(body), **config_info()})
            elif path == "/api/notes":
                save_notes(body["id"], body.get("notes", ""), body.get("sb"))
                self.send_json({"ok": True})
            elif path == "/api/state":
                self.send_json({"ok": True, "state": save_state(body["id"], body.get("state", {}))})
            elif path == "/api/obsidian":
                self.send_json({"ok": True, **send_to_obsidian(body["id"])})
            elif path == "/api/aiprompt":
                self.send_json({"ok": True, "text": ai_prompt(body["id"])})
            elif path == "/api/transcripts/text":
                self.send_json({"ok": True, **transcript_text(body.get("channel"), body.get("id"))})
            elif path == "/api/transcripts/aiprompt":
                self.send_json({"ok": True, "text": _ai_prompt(tr_dir(body.get("channel"), body.get("id")))})
            elif path == "/api/transcripts/remove":
                self.send_json({"ok": True, **remove_transcripts(body)})
            elif path == "/api/transcripts/control":
                self.send_json({"ok": True, **tr_control(str(body.get("action") or ""))})
            elif path == "/api/transcripts/wronglang":
                self.send_json({"ok": True, **tr_wrong_lang(body.get("channel"))})
            elif path == "/api/transcripts/redo":
                self.send_json({"ok": True, **tr_redo_lang(body)})
            elif path == "/api/sortchannels":
                self.send_json({"ok": True, **sort_by_channel(bool(body.get("dry")))})
            elif path == "/api/notes/sortfolders":
                self.send_json({"ok": True, **sort_notes(bool(body.get("dry")))})
            elif path == "/api/tags":
                self.send_json({"ok": True, "tags": change_tags(body)})
            elif path == "/api/tags/set":
                self.send_json({"ok": True, **set_tags(body)})
            elif path == "/api/transcripts/state":
                self.send_json({"ok": True, **tr_set_category(body.get("channel"), body.get("id"), body.get("category"))})
            elif path == "/api/shots/capture":
                self.send_json({"ok": True, **capture_shot(body["id"], body.get("t", 0))})
            elif path == "/api/shots/edit":
                self.send_json({"ok": True, "mt": save_edit(body["id"], body["shot"], body.get("data", ""))})
            elif path == "/api/shots/reset":
                self.send_json({"ok": True, "mt": reset_edit(body["id"], body["shot"])})
            elif path == "/api/shots/open":
                open_external(body["id"], body["shot"])
                self.send_json({"ok": True})
            elif path == "/api/shots/delete":
                self.send_json({"ok": True, **delete_shots(body["id"], body.get("shots") or [], bool(body.get("force")))})
            elif path == "/api/delete":
                ids = body.get("ids") or []
                if not isinstance(ids, list) or not ids:
                    raise ValueError("Nothing to delete")
                self.send_json({"ok": True, **delete_reels(ids, bool(body.get("obsidian")))})
            elif path == "/api/categories":
                self.send_json({"ok": True, "categories": change_cats(body), "channel_cats": channel_cats(), "tags": read_tags()})
            elif path == "/api/share":
                self.send_json({"ok": True, **share_reels(body.get("ids"), str(body.get("to") or ""))})
            elif path == "/api/inbox/check":
                with INBOX_LOCK:
                    INBOX["force"] = True
                INBOX_WAKE.set()
                self.send_json({"ok": True})
            elif path == "/api/inbox/add":
                self.send_json({"ok": True, **inbox_add(body)})
            elif path == "/api/inbox/dismiss":
                self.send_json({"ok": True, **inbox_dismiss(body)})
            elif path == "/api/cloud/test":
                self.send_json({"ok": True, **cloud_test(body)})
            else:
                self.send_error(404)
        except PermissionError as e:
            self.send_json({"error": str(e) or "Not allowed"}, 403)
        except (ValueError, KeyError, TypeError, OSError) as e:
            self.send_json({"error": str(e)}, 400)
        except Exception as e:
            self.send_json({"error": f"Server error: {e}"}, 500)

    def serve_file(self, rel):
        try:
            p = safe_path(rel)
        except PermissionError:
            return self.send_error(403)
        if not p.is_file():
            return self.send_error(404)
        size = p.stat().st_size
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng and size == 0:
            rng = None
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
            if start >= size or end < start:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        length = end - start + 1
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            with open(p, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass


# ---------------------------------------------------------------- page
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reels Hub</title>
<style>
:root{
  --bg:#0f1214; --panel:#171b1e; --panel2:#1d2226; --line:#2a3035; --text:#e6e8ea; --muted:#8b949b;
  --accent:#5fd0c4; --accent-ink:#06201d; --warn:#f0b35a; --star:#f5c451; --ok:#9fdcb4; --radius:10px;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--text);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;display:flex;flex-direction:column}
button,textarea,input,select{font:inherit;color:inherit}
[hidden]{display:none!important}
header{display:flex;flex-wrap:wrap;align-items:center;gap:12px;row-gap:8px;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--panel)}
header h1{font-size:16px;margin:0;font-weight:600;letter-spacing:.2px;cursor:pointer}
header h1:hover{color:var(--accent)}
header .count{color:var(--muted);font-size:13px}
header .spacer{flex:1}
.conn{flex:none;margin-left:auto;font-size:12px;color:var(--muted);white-space:nowrap}
.conn[data-state]::before{content:"";display:inline-block;width:8px;height:8px;margin-right:6px;border-radius:50%;background:var(--ok)}
.conn[data-state="down"]{color:var(--warn)}
.conn[data-state="down"]::before{background:var(--warn)}
@media (max-width:1000px){ header > .status{display:none} }
.btn{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:6px 12px;cursor:pointer}
.btn:hover{border-color:var(--accent)}
.btn.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);font-weight:600}
.btn.small{padding:3px 9px;font-size:12px}
.btn:disabled{opacity:.5;cursor:default}
.ico{background:none;border:0;color:var(--muted);cursor:pointer;padding:2px 5px;border-radius:5px;font-size:13px;line-height:1}
.ico:hover{color:var(--text);background:var(--panel2)}
.link{background:none;border:0;color:var(--accent);cursor:pointer;padding:0;font-size:12px;text-decoration:underline}
.layout{flex:1;display:grid;--fpc:clamp(180px,var(--filt-w,240px),28%);--lsc:clamp(240px,var(--side-w,310px),100% - 560px);
  grid-template-columns:var(--fpc) 0 var(--lsc) 0 minmax(0,1fr);min-height:0}
.layout.fp-closed{--fpc:44px}
.layout.ls-closed{--lsc:44px}
.layout.fp-closed .split[data-split="filt"],.layout.ls-closed .split[data-split="side"]{visibility:hidden}
/* panes fold to a rail, as in the town map: a ‹ in the title row folds, the › on the rail opens */
.fpane{display:flex;flex-direction:column;min-height:0;background:var(--panel);border-right:1px solid var(--line);overflow:hidden}
.phead{display:flex;align-items:center;gap:8px;padding:10px 12px 6px}
.pttl{flex:1;font-weight:700;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.pbody{flex:1;min-height:0;overflow-y:auto;padding-bottom:12px}
.pbtn{flex:none;width:28px;height:28px;border-radius:7px;border:1px solid var(--line);background:var(--panel2);color:var(--text);cursor:pointer;font-size:17px;line-height:1;padding:0}
.pbtn:hover{border-color:var(--accent);color:var(--accent)}
.prail{display:none;flex-direction:column;align-items:center;gap:12px;padding:10px 0}
.rail-label{writing-mode:vertical-rl;transform:rotate(180deg);font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);cursor:pointer}
.layout.fp-closed .fpane > :not(.prail),.layout.ls-closed aside > :not(.prail){display:none}
.layout.fp-closed .fpane > .prail,.layout.ls-closed aside > .prail{display:flex}
.fpane .listtools{padding:4px 12px 6px}
.fpane .sortbar{flex-direction:column;gap:6px}
.fpane .sortbar label{justify-content:space-between}
.fpane .sortbar select{flex:0 1 150px}
.fpane #filters{border-bottom:0}
.flist .fdlist{max-height:none}
.fdsep{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;padding:8px 5px 3px;border-top:1px solid var(--line);margin-top:4px}
.chgroup{margin-left:10px;border-left:1px solid var(--line);padding-left:4px}
.cgroup .gname{color:var(--text)}
.gchan{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:22px 0 4px}
.gsec.gsub{margin-left:14px;padding-left:12px;border-left:1px solid var(--line)}
.fsec{display:flex;flex-direction:column;gap:5px}
aside{border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0;background:var(--panel);overflow:hidden}
#sidetop{flex:none;height:var(--top-h,auto);max-height:calc(100% - 140px);overflow-y:auto}
.vsplit[data-split="top"]{flex:none;margin:0;height:9px;border-top:1px solid var(--line)}
/* drag handles: list | video column | text, and the bar under the video (sizes in --side-w, --player-w, --video-h) */
.split{position:relative;z-index:3;align-self:stretch;cursor:col-resize;touch-action:none;outline:none}
.split::before{content:"";position:absolute;top:0;bottom:0;left:50%;width:2px;margin-left:-1px;transition:background .15s}
.split::after{content:"";display:block;width:4px;height:40px;border-radius:2px;background:var(--line);transition:background .15s}
:is(.split[data-split="side"],.split[data-split="filt"]){margin:0 -6px 0 -2px}
:is(.split[data-split="side"],.split[data-split="filt"])::before{left:1px;margin-left:0}
:is(.split[data-split="side"],.split[data-split="filt"])::after{position:absolute;top:50%;left:0;margin-top:-20px}
.split[data-split="player"]::after{position:sticky;top:calc(50vh - 45px);margin:0 auto}
.vsplit{position:relative;height:12px;margin:-10px 0 -8px;cursor:row-resize;touch-action:none;outline:none}
.vsplit::before{content:"";position:absolute;left:0;right:0;top:50%;height:2px;margin-top:-1px;transition:background .15s}
.vsplit::after{content:"";position:absolute;top:50%;left:50%;width:40px;height:4px;margin:-2px 0 0 -20px;border-radius:2px;background:var(--line);transition:background .15s}
:is(.split,.vsplit):is(:hover,.on,:focus-visible)::before,:is(.split,.vsplit):is(:hover,.on,:focus-visible)::after{background:var(--accent)}
body.resizing-x,body.resizing-x *{cursor:col-resize!important;user-select:none}
body.resizing-y,body.resizing-y *{cursor:row-resize!important;user-select:none}
.runbox{padding:12px;border-bottom:1px solid var(--line);display:flex;flex-direction:column;gap:8px}
.runbox textarea{width:100%;height:70px;resize:vertical;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px;font-size:12px}
.runbox .row{display:flex;gap:8px;align-items:center}
.status{font-size:12px;color:var(--muted)}
.status.on{color:var(--warn)}
#prog{display:flex;flex-direction:column;gap:4px}
.pline{display:flex;justify-content:space-between;gap:8px;font-size:12px}
.pline.sub{color:var(--muted);font-size:11px}
#peta{color:var(--accent);font-variant-numeric:tabular-nums;white-space:nowrap}
.pbar{height:6px;background:var(--panel2);border-radius:4px;overflow:hidden}
#pfill{height:100%;width:0;background:var(--accent);transition:width .6s}
#pfill[data-stage="download"]{background:#6aa8ff}
#pfill[data-stage="ocr"]{background:var(--star)}
#queue{display:flex;flex-direction:column;gap:3px;border-top:1px dashed var(--line);padding-top:6px}
#queue .qhead{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
#qlist{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:1px;max-height:150px;overflow-y:auto;contain:inline-size}
/* contain: long links shorten with ... instead of widening the list (one column below 900 px) */
#qlist li{display:flex;align-items:center;gap:6px;min-width:0;font-size:12px;line-height:1.5}
#qlist .qn{color:var(--muted);font-variant-numeric:tabular-nums;flex:none;min-width:14px;text-align:right}
#qlist .ql{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#qlist .ico{flex:none}
.runbox .row{flex-wrap:wrap}
.modebar{display:flex;gap:4px;padding:10px 12px 0;align-items:center}
.item.tr .tcat{color:var(--accent)}
.group a.ico{text-decoration:none;line-height:1}
.mode{flex:1;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:5px 8px;cursor:pointer;color:var(--muted);font-size:12px;font-weight:600;letter-spacing:.4px;text-transform:uppercase;display:flex;justify-content:center;align-items:center;gap:6px}
.mode.on{color:var(--text);border-color:var(--accent);background:var(--panel)}
.mode .cn{font-size:10.5px;background:var(--panel2);border-radius:10px;padding:0 6px;color:var(--muted)}
.item.tr{padding:6px 8px 6px 22px}
.item.tr .t{-webkit-line-clamp:2}
.srcb{display:inline-block;font-size:9.5px;font-weight:700;letter-spacing:.4px;padding:0 4px;border-radius:4px;border:1px solid var(--muted);color:var(--muted);line-height:14px;vertical-align:1px}
.srcb.cc{border-color:var(--ok, #9fdcb4);color:var(--ok, #9fdcb4)}
.srcb.w{border-color:var(--accent);color:var(--accent)}
.pbtns{display:flex;gap:6px;margin-top:6px}
.gstat{font-size:11px;color:var(--muted);padding:0 8px 6px 22px;display:flex;gap:6px;align-items:center;flex-wrap:wrap;line-height:1.5}
.gstat .btn{padding:1px 8px;font-size:11px}
.gstat .warn{color:var(--warn)}
.trview{padding:16px 20px;max-width:1100px}
.trview h2{font-size:18px;margin:0 0 6px;line-height:1.3}
.trview .meta + .meta{margin-top:4px}
.trview .rowbtns{margin:10px 0 14px}
.trview .card{margin-top:12px}
.trview .pane{max-height:none}
.trline{display:flex;gap:10px;padding:4px 6px;border-radius:6px;white-space:normal}
.trline:hover{background:var(--panel2)}
.trline a.ts{text-decoration:none}
.trdesc{white-space:pre-wrap}
.listtools{display:flex;gap:8px;padding:10px 12px 6px;align-items:center}
.sortbar{display:flex;gap:10px;padding:0 12px 8px}
.sortbar label{display:flex;gap:5px;align-items:center;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;flex:1;min-width:0}
.sortbar select{flex:1;min-width:0;background:var(--panel2);border:1px solid var(--line);border-radius:7px;padding:3px 5px;font-size:12px;text-transform:none;letter-spacing:0;color:var(--text)}
#filters{padding:0 12px 6px;display:flex;flex-direction:column;gap:5px;border-bottom:1px solid var(--line)}
#filters:empty{display:none}
/* scroll bars everywhere: thin, a soft grey bar on the dark background instead of the white system ones */
*{scrollbar-width:thin;scrollbar-color:#4a5058 transparent}
.fdrop{display:flex;flex-direction:column;gap:4px}
.fdbtn{display:flex;align-items:center;gap:8px;width:100%;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:4px 9px;cursor:pointer;color:var(--text);font-size:12px;text-align:left}
.fdrop.open .fdbtn{border-color:var(--accent)}
.fdbtn .fl2{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;flex:none}
.fdbtn .fdv{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fdbtn .caret{color:var(--muted);flex:none}
.fdpanel{border:1px solid var(--line);border-radius:8px;background:var(--bg);padding:6px;display:flex;flex-direction:column;gap:5px}
.fdq{width:100%;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:4px 8px;font-size:12px;color:var(--text)}
.fdlist{max-height:220px;overflow-y:auto;display:flex;flex-direction:column}
.fdopt{display:flex;align-items:center;gap:7px;padding:3px 5px;border-radius:5px;cursor:pointer;font-size:12px}
.fdopt[hidden]{display:none}
.fdopt:hover{background:var(--panel2)}
.fdopt input{margin:0;pointer-events:none;accent-color:var(--accent)}
.fdopt .fdl{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fdopt .cn{font-size:10.5px;color:var(--muted)}
.frow{display:flex;flex-wrap:wrap;gap:5px;align-items:center}
.fl{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;width:100%}
.chip{display:inline-flex;align-items:center;gap:5px;font-size:12px;padding:2px 9px;border-radius:14px;border:1px solid var(--line);background:var(--panel2);cursor:pointer;color:var(--muted)}
.chip:hover{border-color:var(--accent)}
.chip.on{background:#1f2c2b;border-color:var(--accent);color:var(--text)}
.chip .cn{font-size:10.5px;opacity:.7}
#search{flex:1;min-width:0;padding:6px 10px;background:var(--bg);border:1px solid var(--line);border-radius:8px}
#list{overflow:auto;flex:1;min-height:120px;padding:0 6px 12px}
.group{display:flex;align-items:center;gap:4px;padding:10px 6px 4px;margin-top:4px;border-radius:6px;user-select:none}
.group.drop{background:#1f2c2b;outline:1px dashed var(--accent)}
.gname{flex:1;cursor:pointer;font-size:12px;font-weight:600;letter-spacing:.5px;text-transform:uppercase;color:var(--muted);display:flex;gap:6px;align-items:center}
.gname .caret{display:inline-block;width:10px;transition:transform .15s}
.group.closed .caret{transform:rotate(-90deg)}
.gcount{font-size:11px;color:var(--muted);background:var(--panel2);border-radius:10px;padding:0 7px}
.group .ico{opacity:0}
.group:hover .ico{opacity:1}
.gempty{font-size:12px;color:var(--muted);padding:2px 10px 6px 22px}
.item{display:flex;gap:10px;padding:7px 8px;border-radius:8px;cursor:pointer;align-items:center}
.item:hover{background:var(--panel2)}
.item.active{background:#1f2c2b;outline:1px solid #2f5551}
.item img{width:40px;height:54px;object-fit:cover;border-radius:6px;background:var(--panel2);flex:none}
.item .t{font-size:13px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.item .s{font-size:11px;color:var(--muted)}
.item .fc{color:var(--star)}
main{overflow:auto;min-height:0}
.empty{padding:60px;text-align:center;color:var(--muted)}
.detail{padding:16px;display:grid;grid-template-columns:clamp(240px,var(--player-w,360px),100% - 296px) 16px minmax(0,1fr);gap:16px 0;align-items:start}
.player{position:sticky;top:16px;display:flex;flex-direction:column;gap:10px;
  max-height:calc(100vh - 100px);overflow-y:auto;overflow-x:hidden;padding-right:4px;scrollbar-width:thin}
.player > *{flex-shrink:0}
video{width:100%;max-height:min(var(--video-h, min(68vh, calc(100vh - 330px))), calc(100vh - 120px));min-height:180px;background:#000;border-radius:var(--radius)}
.nav{display:flex;gap:8px;align-items:center}
.nav .pos{flex:1;text-align:center;color:var(--muted);font-size:12px}
.meta{font-size:12px;color:var(--muted)}
.meta a{color:var(--accent)}
.catsel{display:flex;gap:8px;align-items:center;font-size:12px;color:var(--muted)}
.stsort{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:4px 0 10px}
.stsort[hidden]{display:none}
.tagbox{display:flex;flex-wrap:wrap;gap:5px;align-items:center;font-size:12px;color:var(--muted);margin-top:6px}
.tagbox .tl{margin-right:4px}
.tagc{display:inline-flex;align-items:center;gap:3px;background:var(--panel2);border:1px solid var(--line);border-radius:999px;padding:1px 4px 1px 8px;color:var(--text)}
.tagc .x{background:none;border:0;color:var(--muted);cursor:pointer;font-size:10px;padding:0 3px;line-height:1}
.tagc .x:hover{color:var(--text)}
.tagin{flex:1;min-width:90px;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:4px 8px;color:var(--text);font-size:12px}
.ttg{color:var(--muted);font-size:11px}
.fhint{font-size:12px;color:var(--muted);padding:2px 0 4px}
.fdopt .tgtools{visibility:hidden;display:inline-flex;margin-left:4px}
.fdopt:hover .tgtools{visibility:visible}
.fdopt .tgtools .ico{padding:0 3px;font-size:11px}
.catsel select{flex:1;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:5px 8px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}
.cardhead{display:flex;align-items:center;gap:10px;padding:8px 10px 8px 14px;border-bottom:1px solid var(--line)}
.cardhead h2{font-size:13px;font-weight:600;margin:0;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}
.cardhead .hint{flex:1;font-size:12px;color:var(--muted)}
.tabs{display:flex;border-bottom:1px solid var(--line);overflow-x:auto}
.tab{padding:9px 14px;cursor:pointer;color:var(--muted);border-bottom:2px solid transparent;white-space:nowrap}
.tab.active{color:var(--text);border-bottom-color:var(--accent)}
.tab .n{font-size:11px;margin-left:4px;color:var(--muted)}
.pane{padding:14px;display:none;white-space:pre-wrap;max-height:320px;overflow:auto}
.pane.active{display:block}
.tline{display:flex;gap:10px;padding:4px 6px;border-radius:6px;cursor:pointer;white-space:normal}
.tline:hover{background:var(--panel2)}
.tline.now{background:#1f2c2b}
.ts{color:var(--accent);font-variant-numeric:tabular-nums;flex:none}
.oline{display:flex;gap:10px;padding:6px;border-radius:6px;white-space:normal;align-items:flex-start}
.oline img{width:48px;border-radius:4px;cursor:zoom-in}
textarea.notes{width:100%;min-height:200px;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;resize:vertical}
.saved{font-size:12px;color:var(--muted);margin-left:8px}
.favs{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px;padding:14px}
.fav{background:var(--panel2);border:1px solid var(--line);border-radius:8px;overflow:hidden;display:flex;flex-direction:column}
.fav.dragging{opacity:.4}
.fav.over{outline:2px solid var(--accent)}
.favimg{position:relative;cursor:grab}
.favimg img{width:100%;display:block;max-height:440px;object-fit:contain;background:#000}
.favimg .num{position:absolute;top:6px;left:6px;background:rgba(0,0,0,.7);border-radius:10px;padding:0 7px;font-size:11px}
.favimg .tb{position:absolute;bottom:6px;left:6px;background:rgba(0,0,0,.7);color:var(--accent);border:0;border-radius:6px;padding:1px 7px;font-size:11px;font-variant-numeric:tabular-nums;cursor:pointer}
.favimg .tb:hover{background:var(--accent);color:var(--accent-ink)}
.tlink{background:none;border:0;padding:1px 6px;border-radius:5px;color:inherit;font:inherit;font-variant-numeric:tabular-nums;cursor:pointer}
.tlink:hover{background:var(--accent);color:var(--accent-ink)}
.shots figcaption .tlink{font-size:11px}
.oline .tlink{color:var(--accent);padding:0 4px}
#lbplay{position:absolute;top:16px;left:140px;height:40px;padding:0 14px;border-radius:20px;border:1px solid var(--line);background:rgba(0,0,0,.65);color:var(--text);font-size:14px;cursor:pointer}
#lbplay:hover{border-color:var(--accent);color:var(--accent)}
@keyframes seekflash{0%{box-shadow:0 0 0 3px var(--accent)}100%{box-shadow:0 0 0 0 transparent}}
video.seekflash{animation:seekflash 1s}
.favimg .rm{position:absolute;top:4px;right:4px;background:rgba(0,0,0,.7);color:#fff;opacity:.85}
.favtext{width:100%;border:0;border-top:1px solid var(--line);background:var(--bg);padding:8px;resize:vertical;min-height:70px;font-size:13px}
.favfoot{display:flex;align-items:center;gap:8px;padding:5px 8px;min-height:28px}
.corr{font-size:11px;color:var(--warn);font-weight:600;flex:1}
.favhint{padding:18px 14px;color:var(--muted);font-size:13px}
.mv{margin-left:auto;display:flex;gap:2px}
.mv .ico:disabled{opacity:.25;cursor:default}
.favzone{display:flex;flex-direction:column;gap:16px}
.favzone.full{position:fixed;inset:0;z-index:15;background:var(--bg);overflow:auto;padding:16px}
.favzone.full .favs{grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px}
.favzone.full .favtext{min-height:90px;font-size:14px}
.favzone.full .shots{grid-template-columns:repeat(auto-fill,minmax(90px,1fr))}
.favzone.full{padding:20px 24px 40px}
#viewbtn.on{border-color:var(--accent);color:var(--accent)}
.pb{display:inline-block;font-size:9.5px;font-weight:700;letter-spacing:.4px;padding:0 4px;border-radius:4px;color:var(--pc);border:1px solid var(--pc);line-height:14px;vertical-align:1px}
.gallery{padding:16px 20px 40px}
.gsec{margin-bottom:26px}
.gsec h3{font-size:13px;font-weight:600;letter-spacing:.6px;text-transform:uppercase;color:var(--muted);margin:0 0 10px;display:flex;gap:8px;align-items:center}
.ggrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:14px}
.gcard{cursor:pointer;border-radius:10px;padding:6px;border:1px solid transparent;position:relative;user-select:none}
.gcard .tick{position:absolute;top:12px;left:12px;z-index:2;width:22px;height:22px;border-radius:50%;background:var(--accent);color:var(--accent-ink);font-size:13px;font-weight:700;display:none;align-items:center;justify-content:center}
.gcard.sel{border-color:var(--accent);background:#1a2a28}
.gcard.sel .tick{display:flex}
.gcard.sel .gthumb{outline:2px solid var(--accent)}
.item.sel{background:#1a2a28;outline:1px solid var(--accent)}
.item{user-select:none}
.dragging{opacity:.45}
.gsec{border-radius:12px;padding:6px 8px;margin-left:-8px;margin-right:-8px;border:1px dashed transparent}
.gsec.drop{border-color:var(--accent);background:rgba(95,208,196,.06)}
.gdrop{grid-column:1/-1;border:1px dashed var(--line);border-radius:10px;padding:22px;text-align:center;color:var(--muted);font-size:13px}
.ghint{font-size:12px;color:var(--muted);margin-bottom:14px}
#bulkbar{position:fixed;left:50%;bottom:18px;transform:translate(-50%,120px);display:flex;gap:10px;align-items:center;background:var(--panel);border:1px solid var(--accent);border-radius:12px;padding:8px 12px;box-shadow:0 8px 30px rgba(0,0,0,.5);z-index:12;transition:transform .2s;max-width:calc(100vw - 24px);flex-wrap:wrap}
#bulkbar.show{transform:translate(-50%,0)}
#bulkcount{font-weight:600;font-size:13px}
#bulkcat{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:5px 8px}
.btn.danger{color:#ef6f6c;border-color:#5a2c2b}
.btn.danger:hover{border-color:#ef6f6c}
.btn.danger.solid{background:#c94a47;color:#fff;border-color:#c94a47;font-weight:600}
#confirm{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;z-index:50;padding:16px}
#confirm.show{display:flex}
#setup{position:fixed;inset:0;background:rgba(6,8,10,.92);display:none;align-items:center;justify-content:center;z-index:60;padding:16px}
#setup.show{display:flex}
.setbox{width:min(560px,100%);display:flex;flex-direction:column;gap:12px}
.setbox h3{margin:0;font-size:18px}
.setbox p{margin:0}
.fld{display:flex;flex-direction:column;gap:5px;font-size:13px;font-weight:600}
.fld .meta{font-weight:400}
.fld input{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-weight:400}
.fld input:focus{outline:none;border-color:var(--accent)}
.setbox .sterr{color:#ef6f6c;font-size:13px}
.setbox .sterr.stwarn{color:var(--warn)}
.stpath{font-size:11px;word-break:break-all}
.who{font-size:12px;color:var(--accent);border:1px solid #2f5551;border-radius:12px;padding:1px 9px}
.who:empty{display:none}
#choice{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;z-index:50;padding:16px}
#choice.show{display:flex}
#choice p{white-space:normal}
.chlist{margin:0 0 12px;padding-left:20px;font-size:13px}
.vtag{display:inline-block;font-size:10px;font-weight:700;padding:0 5px;border-radius:4px;background:var(--panel2);border:1px solid var(--line);color:var(--accent);line-height:14px;vertical-align:1px}
.cbox{width:min(460px,100%);padding:18px 20px}
.cbox h3{margin:0 0 8px;font-size:16px}
.cbox p{margin:0 0 14px;white-space:pre-wrap;color:var(--muted);font-size:13px}
.cchk{display:flex;gap:8px;align-items:center;font-size:13px;margin-bottom:16px}
.cbtns{display:flex;gap:8px;justify-content:flex-end}
#toast{position:fixed;left:50%;top:70px;transform:translateX(-50%);background:var(--accent);color:var(--accent-ink);font-weight:600;padding:7px 14px;border-radius:10px;opacity:0;pointer-events:none;transition:opacity .2s;z-index:70;max-width:calc(100vw - 32px);text-align:center}
#toast.show{opacity:1}
#dragghost{position:fixed;top:-100px;left:-100px;background:var(--accent);color:var(--accent-ink);font-weight:700;padding:5px 12px;border-radius:14px;font-size:13px}
.gcard:hover,.gcard:focus{background:var(--panel);border-color:var(--line);outline:none}
.gcard.active{border-color:#2f5551;background:#1a2423}
.gthumb{position:relative;aspect-ratio:9/16;border-radius:8px;overflow:hidden;background:var(--panel2)}
.gthumb img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .2s}
.gcard:hover .gthumb img{transform:scale(1.03)}
.gstar{position:absolute;top:6px;right:6px;background:rgba(0,0,0,.7);color:var(--star);border-radius:10px;padding:0 7px;font-size:12px}
.gdur{position:absolute;bottom:6px;right:6px;background:rgba(0,0,0,.7);border-radius:6px;padding:0 6px;font-size:11px;font-variant-numeric:tabular-nums}
.gtitle{font-size:13px;margin-top:6px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.gcard .s{font-size:11px;color:var(--muted)}
.obsline{font-size:12px;color:var(--muted);padding:0 14px}
.obsline:not(:empty){padding:8px 14px 0}
.obsline a{color:var(--accent)}
.shots{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:8px;padding:14px}
.shots figure{margin:0;position:relative}
.shots img{width:100%;border-radius:6px;cursor:zoom-in;display:block}
.shots figcaption{font-size:11px;color:var(--muted);text-align:center;font-variant-numeric:tabular-nums}
.star{position:absolute;top:4px;right:4px;width:28px;height:28px;border-radius:50%;border:0;background:rgba(0,0,0,.6);color:#fff;cursor:pointer;font-size:16px;line-height:28px;padding:0}
.star.on{color:var(--star);background:rgba(0,0,0,.75)}
.shotcopy{position:absolute;bottom:22px;right:4px;width:26px;height:26px;border-radius:50%;border:0;background:rgba(0,0,0,.65);color:#fff;cursor:pointer;font-size:13px;line-height:26px;padding:0;opacity:0;transition:opacity .15s}
.shots figure:hover .shotcopy,.favimg:hover .shotcopy{opacity:1}
.shotcopy:hover{background:var(--accent);color:var(--accent-ink)}
.shotcopy.fc2{bottom:6px;right:6px}
.favtrash{position:absolute;bottom:6px;right:38px;width:26px;height:26px;border-radius:50%;border:0;background:rgba(0,0,0,.65);color:#fff;cursor:pointer;font-size:12px;line-height:26px;padding:0;opacity:0;transition:opacity .15s}
.favimg:hover .favtrash{opacity:1}
.favtrash:hover{background:#c94a47}
.shotdel{position:absolute;top:4px;left:4px;width:24px;height:24px;border-radius:50%;border:0;background:rgba(0,0,0,.6);color:#fff;cursor:pointer;font-size:12px;line-height:24px;padding:0;opacity:0;transition:opacity .15s}
.shots figure:hover .shotdel{opacity:1}
.shotdel:hover{background:#c94a47}
#maxshots{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:5px 6px;font-size:12px}
#everyn{width:58px;background:var(--panel2);color:inherit;border:1px solid var(--line);border-radius:8px;padding:5px 6px;font-size:12px}
.rowbtns{display:flex;gap:8px;flex-wrap:wrap}
.right{display:flex;flex-direction:column;gap:16px;min-width:0}
#log{position:fixed;right:16px;bottom:16px;width:min(560px,calc(100vw - 32px));max-height:40vh;display:none;flex-direction:column;z-index:5}
#log.show{display:flex}
#log pre{margin:0;padding:10px 12px;overflow:auto;font:12px/1.45 ui-monospace,Consolas,monospace;white-space:pre-wrap;flex:1}
#log .head{display:flex;align-items:center;padding:6px 10px;border-bottom:1px solid var(--line);gap:8px}
#log .head span{flex:1;font-size:12px;color:var(--muted)}
#lightbox{position:fixed;inset:0;background:rgba(6,8,10,.97);display:none;align-items:center;justify-content:center;z-index:30}
#lightbox.show{display:flex}
#lightbox img{max-width:92vw;max-height:84vh;border-radius:8px}
#lightbox .lb-nav{position:absolute;top:50%;transform:translateY(-50%);font-size:34px;background:none;border:0;color:#fff;cursor:pointer;padding:20px}
#lightbox .prev{left:8px}#lightbox .next{right:8px}
#lightbox .cap{position:absolute;bottom:14px;left:0;right:0;text-align:center;color:#ccc;font-size:13px;padding:0 60px}
#lbstar{position:absolute;top:16px;right:16px;width:44px;height:44px;font-size:22px;line-height:44px}
#editor{position:fixed;inset:0;z-index:35;background:#0b0d0f;display:none;flex-direction:column}
#editor.show{display:flex}
.edbar{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;padding:10px 14px;background:var(--panel);border-bottom:1px solid var(--line)}
.edgrp{display:flex;gap:4px;align-items:center;flex-wrap:nowrap}
.edbar button{white-space:nowrap}
.edgrp.edright{margin-left:auto}
.edbar button{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:6px 10px;cursor:pointer;font-size:13px}
.edbar button:hover{border-color:var(--accent)}
.edbar button.on{background:#1f2c2b;border-color:var(--accent);color:var(--accent)}
.edbar button.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);font-weight:600}
.edbar button:disabled{opacity:.5}
#edcolor{width:34px;height:30px;padding:0;border:1px solid var(--line);border-radius:6px;background:none;cursor:pointer}
#edsize{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:5px 6px;font-size:13px}
.edstage{flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden;min-height:0}
.edwrap{position:relative;box-shadow:0 0 0 1px var(--line),0 10px 40px rgba(0,0,0,.6)}
.edwrap canvas{position:absolute;left:0;top:0;display:block}
#edov{touch-action:none}
#edtext{position:absolute;display:none;background:rgba(0,0,0,.35);border:1px dashed #fff;outline:none;font-weight:700;padding:0 4px;min-width:120px;font-family:system-ui,"Segoe UI",sans-serif;z-index:2}
.edhint{text-align:center;color:var(--muted);font-size:12px;padding:8px}
.capbar{display:flex;gap:8px}
.slidebar{display:flex;gap:8px;align-items:center;justify-content:space-between}
.capbar #capbtn{flex:1}
#captime{font-variant-numeric:tabular-nums;color:var(--accent);margin-left:4px}
.shots figure.man figcaption{color:var(--accent)}
.shots figure.flash img{outline:3px solid var(--accent);outline-offset:-3px;animation:flash 1.2s}
@keyframes flash{0%{filter:brightness(2)}100%{filter:none}}
.shots figure.ed::after{content:"\270E edited";position:absolute;bottom:22px;left:4px;font-size:10px;background:rgba(0,0,0,.7);color:var(--accent);padding:0 5px;border-radius:4px}
#lbedit{position:absolute;top:16px;right:222px;height:40px;padding:0 14px;border-radius:20px;border:1px solid var(--line);background:rgba(0,0,0,.65);color:var(--text);font-size:14px;cursor:pointer}
#lbedit:hover{border-color:var(--accent);color:var(--accent)}
#lbcopy{position:absolute;top:16px;right:72px;height:40px;padding:0 14px;border-radius:20px;border:1px solid var(--line);background:rgba(0,0,0,.65);color:var(--text);font-size:14px;cursor:pointer}
#lbcopy:hover{border-color:var(--accent);color:var(--accent)}
#lbdel{position:absolute;top:16px;left:16px;height:40px;padding:0 14px;border-radius:20px;border:1px solid #6b2f2d;background:rgba(0,0,0,.65);color:#ff8a87;font-size:14px;cursor:pointer}
#lbdel:hover:not(:disabled){background:#c94a47;color:#fff;border-color:#c94a47}
#lbdel:disabled{opacity:.35;cursor:not-allowed}
kbd{background:var(--panel2);border:1px solid var(--line);border-bottom-width:2px;border-radius:4px;padding:0 5px;font-size:11px}

/* print preview */
#printsheet{position:fixed;inset:0;background:#3a3f44;z-index:20;overflow:auto;display:none}
#printsheet.show{display:block}
.ptools{position:sticky;top:0;display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;padding:10px 16px;background:var(--panel);border-bottom:1px solid var(--line);z-index:1}
.ptools label{font-size:13px;color:var(--muted);display:flex;gap:6px;align-items:center}
.ptools select{background:var(--panel2);border:1px solid var(--line);border-radius:6px;padding:3px 6px}
.ptools .spacer{flex:1}
.paper{width:210mm;min-height:297mm;margin:20px auto;background:#fff;color:#111;padding:10mm;box-shadow:0 4px 30px rgba(0,0,0,.4)}
.paper h1{font-size:15pt;margin:0 0 2mm}
.paper .psrc{font-size:8.5pt;color:#555;margin-bottom:5mm;word-break:break-all}
.pgrid{display:grid;gap:5mm}
.pfig{break-inside:avoid;page-break-inside:avoid;margin:0}
.pfig img{width:100%;display:block;border-radius:2mm;border:0.2mm solid #ccc}
.pcap{font-size:9pt;line-height:1.35;margin-top:1.5mm}
.pcap b{color:#0a6b61;font-variant-numeric:tabular-nums;margin-right:1.5mm}
.pnum{display:inline-block;min-width:4.5mm;font-weight:700;margin-right:1mm}
.pcorr{font-size:7.5pt;color:#9a5b00;font-weight:700;text-transform:uppercase;letter-spacing:.3pt;margin-top:.8mm}
@page{size:A4 portrait;margin:10mm}
@media print{
  body{display:block;background:#fff}
  body > *:not(#printsheet){display:none!important}
  #printsheet{position:static;display:block!important;background:#fff;overflow:visible}
  .ptools{display:none}
  .paper{width:auto;min-height:0;margin:0;padding:0;box-shadow:none}
}
@media (max-width:700px){
  header{flex-wrap:wrap;row-gap:8px}
  header > .status{display:none}
}
@media (max-width:900px){
  .layout,.layout.fp-closed,.layout.ls-closed{grid-template-columns:1fr}
  .fpane{border-right:0;border-bottom:1px solid var(--line);max-height:45vh}
  .layout.fp-closed .fpane > .prail,.layout.ls-closed aside > .prail{flex-direction:row;padding:6px 12px}
  .rail-label{writing-mode:horizontal-tb;transform:none}
  .split{display:none}
  aside{max-height:45vh;border-right:0;border-bottom:1px solid var(--line);overflow-y:auto}
  #list{flex:none;overflow:visible;height:auto;min-height:0}
  aside{overflow-y:auto}
  #sidetop{height:auto;max-height:none;overflow:visible}
  .vsplit[data-split="top"]{display:none}   /* the whole column scrolls: progress and Waiting can't squeeze the list away */
  .detail{grid-template-columns:1fr}
  .player{position:static}
  .paper{width:100%;min-height:0}
}

/* audit fixes */
.lbbar{position:absolute;top:12px;left:12px;right:12px;display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;z-index:2;pointer-events:none}
.lbgrp{display:flex;gap:8px;flex-wrap:wrap;pointer-events:auto}
.lbbar #lbdel,.lbbar #lbplay,.lbbar #lbedit,.lbbar #lbcopy{position:static}
.lbbar #lbstar{position:static;flex:none}
@media (max-width:600px){ .lbbar button{font-size:12px;padding:0 10px;height:34px} .lbbar #lbstar{width:34px;height:34px;line-height:34px;font-size:18px} }
.cardhead{flex-wrap:wrap}
.cardhead .hint{min-width:0}
@media (max-width:1200px){ .cardhead .hint{display:none} }
body:has(#bulkbar.show) #log{bottom:84px}
#main.busy #notes,#main.busy .favtext,#main.busy .favs,#main.busy .shots,#main.busy .capbar,#main.busy #favs + *,#main.busy .rowbtns{opacity:.45;pointer-events:none}
#main.busy .detail::before{content:"Re-processing this video... favorites and snapshots are paused until it finishes";grid-column:1/-1;background:#2a2412;border:1px solid var(--warn);color:var(--warn);border-radius:8px;padding:8px 12px;font-size:13px}
#main.busy.waiting .detail::before{content:"Waiting in line to be re-processed... favorites and snapshots are paused until it is done (× in the Waiting list cancels it)"}

/* sharing: share buttons, inbox, shared-by marks, Settings section */
#inboxbtn .ib{display:inline-block;min-width:18px;margin-left:6px;padding:0 6px;border-radius:9px;background:var(--accent);color:var(--accent-ink);font-size:11px;font-weight:700;line-height:17px;text-align:center}
#inbox{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:flex-start;justify-content:center;z-index:45;padding:60px 16px 16px;overflow:auto}
#inbox.show{display:flex}
.ibox{width:min(660px,100%);padding:16px 18px;display:flex;flex-direction:column;gap:10px}
.ihead{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.ihead h3{margin:0;font-size:16px}
.ihead .spacer{flex:1}
.iitem{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:var(--panel2);display:flex;flex-direction:column;gap:4px}
.ititle{font-weight:600;overflow-wrap:anywhere}
.imeta{font-size:12px;color:var(--muted)}
.imeta b{color:var(--text);font-weight:600}
.imeta a{color:var(--accent)}
.inotes{font-size:12px;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px 8px;white-space:pre-wrap;max-height:96px;overflow:auto;overflow-wrap:anywhere}
.irow{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:4px}
.irow .spacer{flex:1}
.irow select{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:4px 8px;font-size:12px;max-width:230px}
.istate{font-size:12px;color:var(--warn)}
.ierr{font-size:12px;color:#ef6f6c;overflow-wrap:anywhere}
.iempty{color:var(--muted);padding:22px 6px;text-align:center;font-size:13px}
.ifoot{font-size:11.5px;color:var(--muted)}
.ifoot b{color:var(--text);font-weight:600}
.ifoot .ierr{display:block;margin-top:3px}
.from{color:var(--accent);white-space:nowrap}
.shline{line-height:1.6}
.shline b{color:var(--text);font-weight:600}
.setbox{max-height:calc(100vh - 32px);overflow:auto}
.stshare{border-top:1px solid var(--line);padding-top:12px;display:flex;flex-direction:column;gap:10px}
.stshare h4{margin:0;font-size:14px}
.strow{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.st2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
@media (max-width:600px){ .st2{grid-template-columns:1fr} }
#sttestres{font-size:12px}
#sttestres.ok{color:var(--accent)}
#sttestres.bad{color:#ef6f6c}
</style>
</head>
<body>
<header>
  <h1 id="home" title="Home: back to the start">Reels Hub</h1><span class="count" id="count"></span>
  <span class="who" id="who" title="Whose Reels Hub this is"></span>
  <span class="spacer"></span>
  <span class="status"><kbd>&larr;</kbd> <kbd>&rarr;</kbd> switch reels</span>
  <button class="btn" id="viewbtn" title="Show all reels as a gallery">&#9638; Gallery</button>
  <button class="btn" id="inboxbtn" hidden title="Videos shared with you">&#128229; Inbox<span class="ib" id="inboxcount" hidden></span></button>
  <button class="btn" id="setbtn" title="Your name and folders">&#9881; Settings</button>
  <button class="btn" id="showlog">Log</button>
  <span class="conn" id="conn" role="status">Connecting</span>
</header>
<div class="layout" id="layout">
  <section class="fpane" id="fpane" aria-label="Filters">
    <div class="prail"><button class="pbtn" id="fpOpen" aria-controls="fpane" title="Show the filters">&#8250;</button><span class="rail-label">Filters</span></div>
    <div class="phead"><span class="pttl">Filters</span><button class="pbtn" id="fpFold" aria-controls="fpane" title="Fold the filters">&#8249;</button></div>
    <div class="pbody">
    <div class="listtools">
      <input id="search" placeholder="Search">
      <button class="btn small" id="addcat">+ Category</button>
    </div>
    <div class="sortbar">
      <label>Group <select id="groupby"><option value="category">Category</option><option value="source">Source</option><option value="none">None</option></select></label>
      <label>Sort <select id="sortby" title="Newest / Oldest: by the date it was posted. Just added: by when you added it here.">
        <option value="posted-new">Newest</option><option value="posted-old">Oldest</option>
        <option value="added-new">Just added</option><option value="title">A-Z</option></select></label>
    </div>
    <div id="filters"></div>
    </div>
  </section>
  <div class="split" data-split="filt" role="separator" aria-orientation="vertical" aria-label="Width of the filters" tabindex="0" title="Drag to change the width · double-click for the standard width"></div>
  <aside>
    <div class="prail"><button class="pbtn" id="lsOpen" title="Show the list">&#8250;</button><span class="rail-label" id="lsRail">Videos</span></div>
    <div id="sidetop">
    <div class="runbox">
      <textarea id="urls" placeholder="Paste Instagram, TikTok, YouTube or X links, one per line. Transcripts also takes YouTube channel and playlist links."></textarea>
      <div class="row">
        <button class="btn primary" id="run">Run</button>
        <button class="btn" id="trbtn" title="Only the transcript of each video, as a note in Obsidian: no video, no snapshots. YouTube channel and playlist links do every video in them. Captions from YouTube (the creator's, else automatic); Whisper where there are none.">Transcripts</button>
        <select id="maxshots" title="Auto: works out how many snapshots from the video's length and how fast it is edited. Or pick a maximum, or Every ... s to set the seconds between snapshots yourself.">
          <option value="auto" selected>Auto shots</option>
          <option value="60">Max 60</option><option value="120">Max 120</option>
          <option value="200">Max 200</option><option value="400">Max 400</option>
          <option value="every">Every ... s</option></select>
        <input id="everyn" type="number" min="0.5" max="600" step="0.5" value="5" hidden
          title="Seconds between snapshots: exactly one every this many seconds, no extra ones at scene cuts (at most 1000 per video)"> 
        <span class="status" id="status">Idle</span>
      </div>
      <div id="prog" hidden>
        <div class="pline"><span id="pstage"></span><span id="peta"></span></div>
        <div class="pbar"><div id="pfill"></div></div>
        <div class="pline sub"><span id="pvid"></span><span id="pel"></span></div>
        <div class="pbtns" id="pbtns" hidden>
          <button class="btn small" id="ppause" title="Finish the current video, then wait; Resume goes on">Pause</button>
          <button class="btn small" id="pstop" title="Finish the current video, then stop; Resume on the channel goes on later">Stop</button>
        </div>
      </div>
      <div id="queue" hidden>
        <div class="qhead" id="qhead"></div>
        <ol id="qlist"></ol>
      </div>
    </div>
    <div class="modebar" role="tablist">
      <button class="mode on" data-mode="reels" role="tab" aria-selected="true" title="Your videos">Videos</button>
      <button class="mode" data-mode="transcripts" role="tab" aria-selected="false" title="Transcript-only videos, by channel">Transcripts<span class="cn" id="trcount" hidden></span></button>
      <button class="pbtn" id="lsFold" title="Fold the list">&#8249;</button>
    </div>
    </div>
    <div class="vsplit" data-split="top" role="separator" aria-orientation="horizontal" aria-label="Height of the tools above the list" tabindex="0" title="Drag up for a longer list (the tools above then scroll) · double-click for the standard size"></div>
    <div id="list"></div>
  </aside>
  <div class="split" data-split="side" role="separator" aria-orientation="vertical" aria-label="Width of the list" tabindex="0" title="Drag to change the width · double-click for the standard width"></div>
  <main id="main"><div class="empty">No reels yet. Paste links on the left and press Run.</div></main>
</div>

<div id="bulkbar">
  <span id="bulkcount"></span>
  <select id="bulkcat"></select>
  <button class="btn small" id="bulktag" title="Add a tag to every selected video">+ Tag</button>
  <button class="btn small" id="bulkall">Select all shown</button>
  <span id="bulkshare" style="display:contents"></span>
  <button class="btn small danger" id="bulkdel">Delete</button>
  <button class="btn small" id="bulkclear">Clear</button>
</div>
<div id="toast"></div>
<div id="editor">
  <div class="edbar">
    <div class="edgrp" id="edtools">
      <button data-tool="crop" title="Crop: drag over the part to keep">&#9986; Crop</button>
      <button data-tool="pen" title="Draw freehand">&#9998; Draw</button>
      <button data-tool="arrow" title="Arrow: drag from tail to tip">&#10140; Arrow</button>
      <button data-tool="rect" title="Box: drag a rectangle">&#9634; Box</button>
      <button data-tool="text" title="Text: click where it should go, type, press Enter">T Text</button>
      <button data-tool="blur" title="Blur: drag over what to hide">&#9638; Blur</button>
    </div>
    <div class="edgrp">
      <button id="edrotl" title="Rotate left">&#10226;</button><button id="edrotr" title="Rotate right">&#10227;</button>
      <input type="color" id="edcolor" value="#ff3b30" title="Color">
      <select id="edsize" title="Line and text size"><option value="1">Thin</option><option value="2" selected>Medium</option><option value="3">Thick</option></select>
      <button id="edundo" title="Undo (Ctrl+Z)">&#8630; Undo</button>
    </div>
    <div class="edgrp edright">
      <button id="edreset" title="Throw away all edits and go back to the original snapshot">Reset to original</button>
      <button id="edpaint" title="Open in Paint for skew, resize and more - save there, then come back">Open in Paint</button>
      <button id="edcancel">Cancel</button>
      <button id="edsave" class="primary">Save</button>
    </div>
  </div>
  <div class="edstage" id="edstage"><div class="edwrap" id="edwrap"><canvas id="edcv"></canvas><canvas id="edov"></canvas><input id="edtext" autocomplete="off"></div></div>
  <div class="edhint" id="edhint"></div>
</div>
<div id="setup"><div class="cbox card setbox">
  <h3 id="sttitle">Welcome to Reels Hub</h3>
  <p class="meta" id="stintro">Tell the app who you are and where to keep things. You can change this later under Settings.</p>
  <label class="fld">Your name<input id="stname" autocomplete="off" placeholder="e.g. Anna"></label>
  <label class="fld">Reels folder <span class="meta">- videos, snapshots and notes are saved here</span>
    <input id="stout" autocomplete="off" spellcheck="false"></label>
  <label class="fld">Obsidian vault <span class="meta">- optional; leave empty if you don't use Obsidian</span>
    <input id="stvault" autocomplete="off" spellcheck="false" placeholder="C:\Users\you\Documents\Obsidian Vault"></label>
  <p class="meta sthelp">Tip: in File Explorer, open the folder, click the address bar and copy the path.</p>
  <div class="stsort" id="stsortrow"><button class="btn small" id="sortnotes" type="button">Move notes to category folders</button>
    <span class="meta">once, for notes made before: each goes to &lt;platform&gt;\&lt;category&gt;\ (asks first; notes you filed elsewhere stay)</span></div>
  <label class="fld">Transcript language <span class="meta">- Transcripts takes YouTube captions in this language first (e.g. en); empty: the video's own</span>
    <input id="stlang" autocomplete="off" spellcheck="false" maxlength="12" placeholder="en"></label>
  <div class="stshare">
    <h4>Sharing with another Reels Hub <span class="meta">(optional: leave empty if you don't share)</span></h4>
    <label class="fld">Cloudflare address<input id="stcurl" autocomplete="off" spellcheck="false" placeholder="https://name.pages.dev"></label>
    <div class="st2">
      <label class="fld">Client ID <span class="meta">of this laptop's service token</span>
        <input id="stcid" autocomplete="off" spellcheck="false" placeholder="ends with .access"></label>
      <label class="fld">Client Secret <span class="meta">stays on this computer</span>
        <input id="stcsec" type="password" autocomplete="new-password" spellcheck="false"></label>
    </div>
    <div class="strow"><button class="btn small" id="sttest" type="button">Test connection</button><span class="meta" id="sttestres"></span></div>
  </div>
  <p class="sterr" id="sterr" hidden></p>
  <div class="cbtns"><button class="btn" id="stcancel" hidden>Cancel</button><button class="btn primary" id="stsave">Save and start</button></div>
  <p class="meta stpath" id="stpath"></p>
</div></div>
<div id="choice" class="modal"><div class="cbox card">
  <h3 id="chtitle"></h3>
  <ul id="chlist" class="chlist"></ul>
  <p id="chtext">Run them again as a <b>new version</b>? The current one and its favorites stay. The downloaded video is reused.</p>
  <p id="chnote" class="meta"></p>
  <div class="cbtns"><button class="btn" id="chcancel">Cancel</button><button class="btn" id="chopen">Open it</button>
    <button class="btn" id="chskip">Skip</button><button class="btn primary" id="chnew">New version</button></div>
</div></div>
<div id="inbox"><div class="cbox card ibox" role="dialog" aria-label="Inbox">
  <div class="ihead"><h3>Inbox</h3><span class="meta" id="inboxsub"></span><span class="spacer"></span>
    <button class="btn small" id="inboxcheck" title="Look for new shares now">Check now</button><button class="btn small" id="inboxclose">Close</button></div>
  <div id="inboxlist"></div>
  <div class="ifoot" id="inboxfoot"></div>
</div></div>
<div id="confirm"><div class="cbox card">
  <h3 id="ctitle"></h3>
  <p id="cmsg"></p>
  <label class="cchk"><input type="checkbox" id="cobs"> Also delete their Obsidian notes and images</label>
  <div class="cbtns"><button class="btn" id="cno">Cancel</button><button class="btn danger solid" id="cyes">Delete</button></div>
</div></div>
<div id="dragghost"></div>
<div id="log" class="card">
  <div class="head"><span id="logtitle">Run log</span><button class="btn small" id="closelog">Close</button></div>
  <pre id="logtext"></pre>
</div>
<div id="lightbox">
  <div class="lbbar">
    <div class="lbgrp">
      <button id="lbdel" title="Delete this snapshot and show the next one (Delete key)">&#128465; Delete</button>
      <button id="lbplay" title="Close and play the video from this moment (P)">&#9654; Play from here</button>
    </div>
    <div class="lbgrp">
      <button id="lbedit" title="Crop, draw, add text, blur... (E)">&#9998; Edit</button>
      <button id="lbcopy" title="Copy image - then paste it in Viber, WhatsApp, email... (C)">&#10697; Copy image</button>
      <button class="star" id="lbstar" title="Favorite (F)">&#9734;</button>
    </div>
  </div>
  <button class="lb-nav prev" id="lbprev">&#8249;</button>
  <img id="lbimg" alt="">
  <button class="lb-nav next" id="lbnext">&#8250;</button>
  <div class="cap" id="lbcap"></div>
</div>
<div id="printsheet">
  <div class="ptools">
    <strong>Print favorites</strong>
    <label>Per row <select id="pcols"><option>2</option><option selected>3</option><option>4</option></select></label>
    <label><input type="checkbox" id="ptitle" checked> Title</label>
    <label><input type="checkbox" id="pnums" checked> Numbers</label>
    <span class="spacer"></span>
    <button class="btn" id="pclose">Close</button>
    <button class="btn primary" id="pprint">Print</button>
  </div>
  <div class="paper" id="paper"></div>
</div>

<script>
const $ = s => document.querySelector(s);
let reels = [], cats = [], filtered = [], current = null, tab = "transcript";
let lb = {list: [], i: 0}, wasRunning = false, dragFav = null;
let collapsed = new Set(), vault = {ok: false, path: ""}, view = "detail", favFull = false;
let panes = {fp: true, ls: true};            // filters pane and list pane open (localStorage rh-panes)
try { panes = {...panes, ...JSON.parse(localStorage.getItem("rh-panes") || "{}")}; } catch (e) {}
function applyPanes(){
  const L = $("#layout"); if (!L) return;
  L.classList.toggle("fp-closed", !panes.fp); L.classList.toggle("ls-closed", !panes.ls);
  try { localStorage.setItem("rh-panes", JSON.stringify(panes)); } catch (e) {}
}
let chanCats = new Set();                   // categories that are channels (under one "Channels" group)
let tags = [];                              // the tag list (a video: one category, any number of tags)
const CH_KEY = "\u2063channels";            // the Channels group's key in the collapsed sets
function orderCats(list){ return [...list.filter(c => !chanCats.has(c)), ...list.filter(c => chanCats.has(c))]; }
function assemble(parts, closedSet){        // the Channels group around the channel categories, Unassigned last
  const normal = parts.filter(p => !p.chan && !p.unas), chans = parts.filter(p => p.chan), unas = parts.filter(p => p.unas);
  let html = normal.map(p => p.html).join("");
  if (chans.length) {
    const closed = closedSet.has(CH_KEY), n = chans.reduce((a, p) => a + p.n, 0);
    html += `<div class="group cgroup ${closed ? "closed" : ""}" data-label="${CH_KEY}" data-trkey="${CH_KEY}">
      <span class="gname" data-chtoggle><span class="caret">&#9662;</span>Channels</span>
      <span class="gcount" title="${chans.length} channels, ${n} items">${chans.length}</span></div>`;
    if (!closed) html += `<div class="chgroup">${chans.map(p => p.html).join("")}</div>`;
  }
  return html + unas.map(p => p.html).join("");
}
let mode = "reels";                        // "reels" (the gallery) or "transcripts" (the catalogue of transcript-only videos)
let tr = {channels: [], items: [], current: null, shown: ""}, trCollapsed = new Set(), trTick = 0;
let trGroupBy = "channel", tfChan = new Set(), tfCat = new Set(), tfPlat = new Set(), tfTag = new Set();   // the catalogue's own grouping and filters
try { trCollapsed = new Set(JSON.parse(localStorage.getItem("rh-trcollapsed") || "[]")); } catch (e) {}
try { trGroupBy = localStorage.getItem("rh-trgroupby") || trGroupBy; const f = JSON.parse(localStorage.getItem("rh-trfilters") || "{}"); tfCat = new Set(f.c || []); tfPlat = new Set(f.p || []); tfTag = new Set(f.t || []); } catch (e) {}
const GROUP_OPTS = {reels: [["category", "Category"], ["source", "Source"], ["none", "None"]], transcripts: [["channel", "Channel"], ["category", "Category"], ["none", "None"]]};
function setGroupOptions(m){            // the Group menu offers Channel in Transcripts and Source in Videos
  const sel = $("#groupby");
  sel.innerHTML = GROUP_OPTS[m].map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
  sel.value = m === "transcripts" ? trGroupBy : groupBy;
  if (!sel.value) { sel.value = GROUP_OPTS[m][0][0]; if (m === "transcripts") trGroupBy = sel.value; else groupBy = sel.value; }
}
try { view = localStorage.getItem("rh-view") || "detail"; } catch (e) {}
try { collapsed = new Set(JSON.parse(localStorage.getItem("rh-collapsed") || "[]")); } catch (e) {}
const shotUrl = (r, shot) => media(r.id, "shots/" + shot) + (r.mt && r.mt[shot] ? `?v=${r.mt[shot]}` : "");
const media = (id, f) => `/media/${encodeURIComponent(id)}/${f.split("/").map(encodeURIComponent).join("/")}`;
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const mmss = t => { t = Math.round(t || 0); return `${String(Math.floor(t/60)).padStart(2,"0")}:${String(t%60).padStart(2,"0")}`; };
/* photo slideshows (Instagram posts with several slides): the video is the slides joined; say "Slide 3", not 00:10 */
const slideAt = (slides, t) => slides.reduce((cur, x) => x.start <= t + 0.001 ? x : cur, slides[0]);
const tlabel = (r, t) => { if (!r || !r.slides) return mmss(t); const x = slideAt(r.slides, +t || 0);
  return `Slide ${x.n}` + (x.kind === "video" ? ` · ${mmss((+t || 0) - x.start)}` : ""); };
const PB = {Instagram: ["IG", "#e1306c"], TikTok: ["TT", "#25f4ee"], YouTube: ["YT", "#ff4e45"], X: ["X", "#e7e9ea"]};
const pbadge = r => { const [t, c] = Object.prototype.hasOwnProperty.call(PB, r.platform) ? PB[r.platform] : [String(r.platform||"").slice(0,3), "#8b949b"]; return `<span class="pb" style="--pc:${c}" title="${esc(r.platform)}">${esc(t)}</span>`; };
const vtag = r => r.version > 1 ? ` <span class="vtag" title="Version ${r.version}">v${r.version}</span>` : "";
const catOf = r => cats.includes(r.state.category) ? r.state.category : "";
const isFav = (r, shot) => r.state.favorites.some(f => f.shot === shot);
const shareTargets = () => (me.share && me.share.on ? me.share.people || [] : []);   // whom you can share with
const mayDelete = () => me.can_delete !== false;                                     // off for a person in the inbox's PEOPLE
const fromName = r => r.state.shared_by ? (r.state.shared_by.name || r.state.shared_by.person || "") : "";
const fromBadge = r => r.state.shared_by ? ` · <span class="from" title="Shared with you by ${esc(fromName(r))}">&#8600; ${esc(fromName(r))}</span>` : "";

/* ---------- connection to the Reels Hub server on this PC (status at the top right) ---------- */
const CONN_DOWN = "The server does not answer: start Reels Hub again";
let connUp = null, connTimer = null, connBusy = false, leaving = false;
function setConn(up){
  if (up === connUp || (!up && leaving)) return;     // a page being closed or reloaded is not "down"
  connUp = up;
  const c = $("#conn");
  c.dataset.state = up ? "up" : "down";
  c.textContent = up ? "Connected to this PC" : CONN_DOWN;
  c.title = up ? `Reels Hub runs on this PC (${location.host}); only this computer can reach it`
               : "Double-click Reels Hub on the desktop, or reels-hub.bat in the app folder. This page connects again by itself.";
  if (!up) pingSoon(2000);
}
function pingSoon(ms){ clearTimeout(connTimer); connTimer = setTimeout(ping, ms); }
async function ping(){                     // heartbeat: every 5 s, every 2 s while the server does not answer
  if (connBusy) return;
  connBusy = true;
  const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), 10000);
  try { await fetch("/api/ping", {cache: "no-store", signal: ctl.signal}); setConn(true); }
  catch (e) { setConn(false); }
  finally { clearTimeout(t); connBusy = false; }
  pingSoon(connUp ? 5000 : 2000);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) ping(); });
window.addEventListener("pagehide", () => { leaving = true; });
window.addEventListener("pageshow", e => { leaving = false; if (e.persisted) ping(); });

async function api(path, body){
  let res;
  try { res = await fetch(path, body ? {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)} : {}); }
  catch (e) { setConn(false); throw new Error(CONN_DOWN); }       // no answer at all: its window was closed
  setConn(true);
  const j = await res.json();
  if (!res.ok) throw new Error(j.error || res.statusText);
  return j;
}

/* ---------- transcript text at a snapshot's time ---------- */
function textAt(r, shot){
  const t = r.times[shot] ?? 0;
  let txt = "";
  for (const seg of r.transcript) if (seg.t <= t + 0.5) txt = seg.text;
  if (!txt) { const o = r.ocr.find(x => x.shot === shot); if (o) txt = o.text; }
  return txt;
}

/* ---------- data ---------- */
async function loadReels(keepId, changedIds){
  await flushSaves();
  const j = await api("/api/reels");
  const old = new Map(reels.map(r => [r.id, r]));
  reels = j.reels.map(r => {                 // keep your in-progress edits for reels the run didn't touch
    const o = old.get(r.id);
    if (o && !(changedIds && changedIds.has(r.id))) { r.state = o.state; if (o.notesDraft !== undefined) r.notesDraft = o.notesDraft; }
    return r;
  });
  cats = j.categories; chanCats = new Set(j.channel_cats || []); tags = j.tags || tags; vault = j.vault || vault; me = j.me || me;
  if (changedSeq === null) { changedSeq = j.changed_seq || 0; bootId = j.boot; }
  else if (j.boot && j.boot !== bootId) { bootId = j.boot; changedSeq = 0; }
  applyMe();
  if (changedIds && current && !changedIds.has(current.id) && reels.some(r => r.id === current.id)) {
    current = reels.find(r => r.id === current.id);      // same reel still open: don't redraw (keeps video + typing)
    $("#count").textContent = reels.length ? `${reels.length} reel${reels.length>1?"s":""}` : "";
    renderList(); updateNavSafe(); return;
  }
  $("#count").textContent = reels.length ? `${reels.length} reel${reels.length>1?"s":""}` : "";
  renderList();
  const id = keepId || decodeURIComponent(location.hash.slice(1)) || (filtered[0] && filtered[0].id);
  if (id && reels.some(r => r.id === id)) select(id, false, true);
  else if (filtered[0]) select(filtered[0].id, false, true);
}

const saveTimers = {}, pending = {};
let busyIds = new Set();                  // reels being re-processed (or waiting for it): edits are paused
let waitingIds = new Set(), busyEpoch = 0; // waitingIds: Re-process still in line; busyEpoch: see syncBusy
function isBusy(r, quiet){
  if (!r || !busyIds.has(r.id)) return false;
  if (!quiet) toast(waitingIds.has(r.id) ? "This video is waiting in line to be re-processed - changes are paused until it is done"
                                         : "This video is being re-processed - changes are paused until it finishes");
  return true;
}
function showBusy(){                      // the banner over the open reel
  if (!current) return;
  $("#main").classList.toggle("busy", busyIds.has(current.id));
  $("#main").classList.toggle("waiting", waitingIds.has(current.id));
}
function saveState(r, now){
  if (isBusy(r)) return;
  clearTimeout(saveTimers[r.id]);
  const go = () => { delete pending[r.id]; return api("/api/state", {id: r.id, state: r.state}).catch(e => saveFailed(r, e)); };
  pending[r.id] = {url: "/api/state", body: () => ({id: r.id, state: r.state}), go};
  if (now) go(); else saveTimers[r.id] = setTimeout(go, 500);
}
const notesTimers = {};
function notesChanged(r, text){
  if (isBusy(r)) return;
  r.notesDraft = text;
  clearTimeout(notesTimers[r.id]);
  const go = () => { delete pending["n:" + r.id];
    return api("/api/notes", {id: r.id, notes: text, sb: sbAt(r)}).then(() => { r.notes = text; delete r.notesDraft; const sv = $("#saved"); if (sv && current === r) { sv.textContent = "Saved"; sv.hidden = false; } })
      .catch(e => saveFailed(r, e, "notes")); };
  pending["n:" + r.id] = {url: "/api/notes", body: () => ({id: r.id, notes: text, sb: sbAt(r)}), go};
  notesTimers[r.id] = setTimeout(go, 800);
}
const sbAt = r => (r.state && r.state.shared_by ? r.state.shared_by.at || "" : "");   // which fill-in this copy shows
function saveFailed(r, e, what){
  if (/filled in from a share/.test(e.message)) {   // this page had the reel from before a share filled it in
    delete r.notesDraft; toast(e.message);
    return loadReels(current && current.id, new Set([r.id])).catch(() => {});
  }
  alert(`Could not save${what ? " " + what : ""}: ` + e.message);
}
async function flushSaves(){               // write anything typed but not yet saved
  for (const k of Object.keys(pending)) { clearTimeout(saveTimers[k]); clearTimeout(notesTimers[k.replace(/^n:/, "")]); }
  await Promise.all(Object.values(pending).map(p => p.go()));
}
window.addEventListener("pagehide", () => {    // tab closing: send pending saves in the background
  for (const p of Object.values(pending)) {
    try { navigator.sendBeacon(p.url, new Blob([JSON.stringify(p.body())], {type: "application/json"})); } catch (e) {}
  }
});

/* ---------- sidebar ---------- */
/* filters: empty set = show all */
let fPlat = new Set(), fCat = new Set(), fTag = new Set();
try { const f = JSON.parse(localStorage.getItem("rh-filters") || "{}"); fPlat = new Set(f.p || []); fCat = new Set(f.c || []); fTag = new Set(f.t || []); } catch (e) {}
function saveFilters(){ try { localStorage.setItem("rh-filters", JSON.stringify({p: [...fPlat], c: [...fCat], t: [...fTag]})); } catch (e) {} }
const tagsOf = x => (x.state ? x.state.tags : x.tags) || [];
const hasTag = (x, t) => tagsOf(x).some(y => y.toLowerCase() === t.toLowerCase());
function tagList(kind, attr, sel, items, platOk){
  // the Tags section of the filters pane: ticks with counts (any ticked tag matches), rename / delete on each row
  [...sel].forEach(t => tags.includes(t) || sel.delete(t));
  const opts = tags.map(t => ({val: t, label: t, html: "#" + esc(t), tools: `<span class="tgtools"><button class="ico" data-tagren="${esc(t)}" title="Rename this tag">&#9998;</button><button class="ico" data-tagdel="${esc(t)}" title="Delete this tag (taken off every video)">&#10005;</button></span>`,
    n: items.filter(x => hasTag(x, t) && platOk(x)).length}));
  return (tags.length ? flist(kind, "Tags", attr, opts, sel) : `<div class="fsec"><span class="fl">Tags</span><div class="fhint">No tags yet: add one on a video (Tags, under Category).</div></div>`)
    + `<div class="frow"><button class="link" data-tagnew>+ New tag</button></div>`;
}
function tagBox(list, id){
  const have = new Set(list.map(t => t.toLowerCase()));
  const opts = [...tags, ...cats.filter(c => !tags.some(t => t.toLowerCase() === c.toLowerCase()))].filter(t => !have.has(t.toLowerCase()));
  return `<div class="tagbox" id="${id}"><span class="tl">Tags</span>${list.map(t => `<span class="tagc">#${esc(t)}<button class="x" data-untag="${esc(t)}" title="Take this tag off">&#10005;</button></span>`).join("")}<input class="tagin" data-tagin="${id}" list="${id}-dl" placeholder="+ tag" autocomplete="off" maxlength="60" title="Type a tag (new or existing) and press Enter"><datalist id="${id}-dl">${opts.map(t => `<option value="${esc(t)}"></option>`).join("")}</datalist></div>`;
}
const tagTarget = id => id === "rtags" ? (current && {reel: current.id}) : (tr.current && (tr.current.reel && reels.some(r => r.id === tr.current.reel) ? {reel: tr.current.reel} : {ch: tr.current.ch, id: tr.current.id}));
const targetTags = t => t.reel ? ((reels.find(r => r.id === t.reel) || {state: {}}).state.tags || []) : ((tr.items.find(x => x.ch === t.ch && x.id === t.id) || {}).tags || []);
async function saveTags(t, list, quiet){
  const body = t.reel ? {id: t.reel, tags: list} : {channel: t.ch, id: t.id, tags: list};
  let j; try { j = await api("/api/tags/set", body); } catch (e) { if (!quiet) alert(e.message); return false; }
  tags = j.all;
  if (t.reel) { const r = reels.find(x => x.id === t.reel); if (r) r.state.tags = j.tags; tr.items.forEach(it => { if (it.reel === t.reel) it.tags = j.tags; }); }
  else { const it = tr.items.find(x => x.ch === t.ch && x.id === t.id); if (it) it.tags = j.tags; }
  if (!quiet) redrawTags();
  return true;
}
function redrawTags(focus){
  renderList();
  if (current && $("#rtags")) $("#rtags").outerHTML = tagBox(current.state.tags || [], "rtags");
  if (tr.current && $("#ttags")) $("#ttags").outerHTML = tagBox(tr.current.tags || [], "ttags");
  const i = focus && document.querySelector(`[data-tagin="${focus}"]`); if (i) i.focus();
}
async function sortNotes(){
  let j; try { j = await api("/api/notes/sortfolders", {dry: true}); } catch (e) { return alert(e.message); }
  if (!j.count) return alert(`Nothing to move: every note is already in its category folder${j.yours ? ` (${j.yours} you filed yourself stay where they are)` : ""}${j.clashes ? `; ${j.clashes} not moved because a note of the same name is already there` : ""}.`);
  $("#setup").classList.remove("show");          // the confirm dialog would sit under Settings
  const a = await askConfirm(`Move ${j.count} note${j.count === 1 ? "" : "s"} to category folders?`,
    `Each goes to <platform>\\<category>\\ in your vault (Unassigned: the platform folder); the file names stay. For example:\n${j.examples.join("\n")}${j.count > j.examples.length ? "\n..." : ""}`
    + (j.yours ? `\n\n${j.yours} note${j.yours === 1 ? "" : "s"} you filed in your own folders stay where they are.` : "")
    + (j.clashes ? `\n${j.clashes} not moved: a note of the same name is already in the target folder.` : ""), null, "Move");
  if (!a.ok) return;
  try { j = await api("/api/notes/sortfolders", {}); } catch (e) { return alert(e.message); }
  toast(`Moved ${j.count} note${j.count === 1 ? "" : "s"}`);
  loadReels(current && current.id).catch(() => {}); loadTr().catch(() => {});
}
async function tagAction(body){
  let r; try { r = await api("/api/tags", body); } catch (e) { return alert(e.message); }
  tags = r.tags;
  const fix = l => l && (body.action === "rename" ? l.map(t => t === body.name ? body.new : t) : l.filter(t => t !== body.name));
  if (body.action !== "add") {
    reels.forEach(x => { x.state.tags = fix(x.state.tags); }); tr.items.forEach(x => { x.tags = fix(x.tags); });
    for (const set of [fTag, tfTag]) if (set.delete(body.name) && body.action === "rename") set.add(body.new);
    saveFilters(); saveTrFilters();
  }
  redrawTags();
}
const catShown = name => !fCat.size || fCat.has(name);
let openDrop = "", dropQ = {};              // which filter drop-down is open, and its search text
function flist(kind, label, attr, opts, sel){
  // the pane has room: the whole list with ticks and counts, a find box when it is long
  const q = (dropQ[kind] || "").toLowerCase();
  return `<div class="fsec"><span class="fl">${label}${sel.size ? ` · <button class="link" data-fdclear="${kind}">show all</button>` : ""}</span>
    <div class="fdpanel flist">${opts.length > 8 ? `<input class="fdq" data-fdq="${kind}" autocomplete="off" placeholder="Find ${label.toLowerCase()}" value="${esc(dropQ[kind] || "")}">` : ""}
      <div class="fdlist">${opts.map((o, i) => (o.chan && !(opts[i - 1] || {}).chan ? `<div class="fdsep">Channels</div>` : "") + `<div class="fdopt" ${q && !o.label.toLowerCase().includes(q) ? "hidden" : ""} ${attr}="${esc(o.val)}"><input type="checkbox" ${sel.has(o.val) ? "checked" : ""} tabindex="-1"><span class="fdl">${o.html || esc(o.label)}</span><span class="cn">${o.n}</span>${o.tools || ""}</div>`).join("")}</div></div></div>`;
}
function fdrop(kind, label, attr, opts, sel){
  // a compact multi-select: "Category: All" / "Category: 2 selected", a search box and a scrolling list of ticks
  const chosen = opts.filter(o => sel.has(o.val));
  const txt = !chosen.length ? "All" : chosen.length === 1 ? chosen[0].label : `${chosen.length} selected`;
  const open = openDrop === kind, q = (dropQ[kind] || "").toLowerCase();
  return `<div class="fdrop ${open ? "open" : ""}" data-fdrop="${kind}">
    <button class="fdbtn" data-fdtoggle="${kind}" title="${esc(chosen.map(o => o.label).join(", ") || "All")}"><span class="fl2">${label}</span><span class="fdv">${esc(txt)}</span><span class="caret">&#9662;</span></button>
    ${open ? `<div class="fdpanel"><input class="fdq" data-fdq="${kind}" placeholder="Find ${label.toLowerCase()}" value="${esc(dropQ[kind] || "")}">
      <div class="fdlist">${opts.map(o => `<div class="fdopt" ${q && !o.label.toLowerCase().includes(q) ? "hidden" : ""} ${attr}="${esc(o.val)}"><input type="checkbox" ${sel.has(o.val) ? "checked" : ""} tabindex="-1"><span class="fdl">${o.html || esc(o.label)}</span><span class="cn">${o.n}</span></div>`).join("")}</div>
      ${sel.size ? `<button class="link" data-fdclear="${kind}">Show all</button>` : ""}</div>` : ""}
  </div>`;
}
function renderFilters(){
  const plats = [...new Set(reels.map(r => r.platform))].sort();
  [...fPlat].forEach(p => plats.includes(p) || fPlat.delete(p));
  [...fCat].forEach(c => (c === "" || cats.includes(c)) || fCat.delete(c));
  const count = (pred) => reels.filter(pred).length;
  const chip = (attr, val, label, on, n, extra="") => `<button class="chip ${on?"on":""}" ${attr}="${esc(val)}" ${extra}>${label}<span class="cn">${n}</span></button>`;
  [...fTag].forEach(t => tags.includes(t) || fTag.delete(t));
  const active = fPlat.size + fCat.size + fTag.size;
  $("#filters").innerHTML = (plats.length > 1 || fPlat.size ? `<div class="frow"><span class="fl">Source</span>${plats.map(p =>
      chip("data-fplat", p, pbadge({platform: p}) + " " + esc(p), fPlat.has(p), count(r => r.platform === p))).join("")}</div>` : "")
    + flist("cat", "Category", "data-fcat", [...orderCats(cats).filter(c => !chanCats.has(c)), "", ...orderCats(cats).filter(c => chanCats.has(c))].map(c => ({val: c, label: c || "Unassigned", chan: chanCats.has(c), n: count(r => catOf(r) === c && (!fPlat.size || fPlat.has(r.platform)))}))
        .filter(o => !fPlat.size || o.n || fCat.has(o.val)), fCat)
    + tagList("tag", "data-ftag", fTag, reels, r => !fPlat.size || fPlat.has(r.platform))
    + (active ? `<div class="frow"><button class="link" id="fclear">Clear filters (${active})</button></div>` : "");
}

function matches(r, q){
  if (fPlat.size && !fPlat.has(r.platform)) return false;
  if (fCat.size && !fCat.has(catOf(r))) return false;
  if (fTag.size && ![...fTag].some(t => hasTag(r, t))) return false;
  if (!q) return true;
  return [r.label, r.platform, r.caption, r.author, r.notes, fromName(r), ...tagsOf(r), ...r.transcript.map(x=>x.text), ...r.ocr.map(x=>x.text),
          ...r.state.favorites.map(f=>f.text)].join(" ").toLowerCase().includes(q);
}
let groupBy = "category", sortBy = "posted-new", shown = [];
const navList = () => view === "gallery" ? filtered : shown;      // what you can actually see
try { groupBy = localStorage.getItem("rh-groupby") || groupBy; sortBy = localStorage.getItem("rh-sortby") || sortBy; } catch (e) {}
function sortReels(list){
  const by = {
    "posted-new": (a, b) => (b.date || "").localeCompare(a.date || "") || (b.added || 0) - (a.added || 0),
    "posted-old": (a, b) => (a.date || "9999").localeCompare(b.date || "9999") || (a.added || 0) - (b.added || 0),
    "added-new":  (a, b) => (b.added || 0) - (a.added || 0),
    "title":      (a, b) => a.label.localeCompare(b.label, undefined, {sensitivity: "base"}) || a.version - b.version,
  }[sortBy] || (() => 0);
  return [...list].sort(by);
}
function groupsFor(){
  if (groupBy === "source") {
    return [...new Set(reels.map(r => r.platform))].sort().map(p => ({key: "src:" + p, label: p, kind: "source", test: r => r.platform === p}));
  }
  if (groupBy === "none") return [{key: "all", label: "All reels", kind: "none", test: () => true}];
  return [...orderCats(cats).map(c => ({key: c, label: c, cat: c, kind: "cat", chan: chanCats.has(c), test: r => catOf(r) === c})),
          {key: "", label: "Unassigned", cat: "", kind: "cat", unas: true, test: r => catOf(r) === ""}];
}
function renderList(){
  if (mode === "transcripts") return renderTrList();      // the list shows the catalogue; reels keep their state
  const q = $("#search").value.trim().toLowerCase();
  const groups = groupsFor();
  filtered = []; shown = [];
  const parts = [];
  renderFilters();
  groups.forEach((g, gi) => {
    if (g.kind === "cat" && !catShown(g.cat)) return;
    const items = sortReels(reels.filter(r => g.test(r) && matches(r, q)));
    filtered.push(...items);
    if (!collapsed.has(g.label) && !(g.chan && collapsed.has(CH_KEY))) shown.push(...items);
    if (g.kind === "cat" && !items.length && (q || fPlat.size || fTag.size)) return;     // filtering: empty categories are left out
    if (g.kind === "source" && !items.length) return;
    const closed = collapsed.has(g.label);
    const name = g.cat || "";
    let html = `<div class="group ${closed?"closed":""}" ${g.kind === "cat" ? `data-cat="${esc(g.cat)}"` : ""} data-label="${esc(g.label)}">
      <span class="gname" data-toggle><span class="caret">&#9662;</span>${g.kind === "source" ? pbadge({platform: g.label}) + " " : ""}${esc(g.label)}</span>
      <span class="gcount">${items.length}</span>
      ${name ? `<button class="ico" data-catmove="-1" title="Move up" ${gi===0?"disabled":""}>&#8593;</button>
                  <button class="ico" data-catmove="1" title="Move down" ${gi===cats.length-1?"disabled":""}>&#8595;</button>
                  <button class="ico" data-rename title="Rename">&#9998;</button>
                  <button class="ico" data-delcat title="Delete category">&#10005;</button>` : ""}
      ${g.kind === "cat" && !g.cat && items.length ? `<button class="btn small" data-sortch title="Put each unassigned video in a category named after its channel or creator">Sort by channel</button>` : ""}
    </div>`;
    if (!closed) html += items.length ? items.map(r => `
      <div class="item ${current && current.id===r.id?"active":""} ${selected.has(r.id)?"sel":""}" data-id="${esc(r.id)}" draggable="true">
        ${r.shots[0] ? `<img loading="lazy" src="${shotUrl(r, r.shots[0])}" alt="">` : `<img alt="">`}
        <div><div class="t">${esc(r.label)}</div>
        <div class="s">${pbadge(r)}${vtag(r)} ${esc(r.author)} · ${esc(r.date)}${r.state.favorites.length?` · <span class="fc">&#9733; ${r.state.favorites.length}</span>`:""}${fromBadge(r)}${tagsOf(r).length ? ` · <span class="ttg">${tagsOf(r).map(t => "#" + esc(t)).join(" ")}</span>` : ""}</div></div>
      </div>`).join("") : `<div class="gempty">${name ? "Drag reels here or pick this category on a reel" : "Nothing here"}</div>`;
    parts.push({html, chan: !!g.chan, unas: !!g.unas, n: items.length});
  });
  const html = assemble(parts, collapsed);
  $("#list").innerHTML = html || `<div class="empty">No matches</div>`;
  if (view === "gallery") renderGallery();
}

/* ---------- multi-select + bulk move ---------- */
let selected = new Set(), anchor = null;
function pick(id, e){
  if (e.shiftKey && anchor) {
    const ids = navList().map(r => r.id), a = ids.indexOf(anchor), b = ids.indexOf(id);
    if (a >= 0 && b >= 0) { const [lo, hi] = a < b ? [a, b] : [b, a]; ids.slice(lo, hi + 1).forEach(x => selected.add(x)); }
    else selected.add(id);
  } else {
    selected.has(id) ? selected.delete(id) : selected.add(id);
    anchor = id;
  }
  refreshSel();
}
function clearSel(){ selected.clear(); anchor = null; refreshSel(); }
function refreshSel(){
  document.querySelectorAll(".item,[data-open]").forEach(el => el.classList.toggle("sel", selected.has(el.dataset.id || el.dataset.open)));
  const n = selected.size, bar = $("#bulkbar");
  bar.classList.toggle("show", n > 0);
  if (!n) return;
  $("#bulkcount").textContent = `${n} selected`;
  $("#bulkshare").innerHTML = shareTargets().map(p => `<button class="btn small" data-bulkshare="${esc(p.person)}" title="Send the links, your favorites, notes and categories to ${esc(p.name)}'s Reels Hub">&#8599; Share with ${esc(p.name)}</button>`).join("");
  $("#bulkdel").hidden = !mayDelete();
  $("#bulkcat").innerHTML = `<option value="" disabled selected>Move to category...</option>`
    + ["", ...cats].map(c => `<option value="cat:${esc(c)}">${c ? esc(c) : "Unassigned"}</option>`).join("")
    + `<option value="new">+ New category...</option>`;
}
function moveReels(ids, cat){
  const rs = ids.map(id => reels.find(r => r.id === id)).filter(r => r && catOf(r) !== cat);
  rs.forEach(r => { r.state.category = cat; saveState(r, true); });
  if (current && $("#catsel") && rs.includes(current)) $("#catsel").value = cat;
  renderList(); refreshSel(); if (current) updateNavSafe();
  toast(rs.length ? `Moved ${rs.length} reel${rs.length>1?"s":""} to ${cat || "Unassigned"}` : `Already in ${cat || "Unassigned"}`);
}
function updateNavSafe(){ if ($("#pos")) updateNav(); }
let toastTimer;
function toast(msg){ const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 2200); }

/* ---------- snapshots: delete, re-process ---------- */
function seekTo(t){
  const v = $("#vid"); if (!v || !isFinite(t)) return;
  $("#lightbox").classList.remove("show");
  if (favFull) toggleFull(false);
  v.currentTime = Math.max(0, t);
  if (current && current.slides && slideAt(current.slides, t).kind === "image") v.pause();   // a photo: stay on it
  else v.play().catch(() => {});
  v.scrollIntoView({block: "nearest", behavior: "smooth"});
  v.classList.remove("seekflash"); void v.offsetWidth; v.classList.add("seekflash");
}

function goHome(){
  if (ed) { if (ed.dirty && !confirm("Discard your image edits?")) return; closeEditor(); }
  $("#lightbox").classList.remove("show"); $("#printsheet").classList.remove("show"); $("#log").classList.remove("show");
  closeInbox();
  if (favFull) toggleFull(false);
  clearSel();
  if (mode === "transcripts") setMode("reels");
  panes.ls = true; applyPanes();
  view = "detail"; try { localStorage.setItem("rh-view", view); } catch (e) {} $("#viewbtn").classList.remove("on");
  history.replaceState(null, "", location.pathname);
  $("#list").scrollTop = 0; document.querySelector("aside").scrollTop = 0;   // below 900 px the whole column scrolls
  if (filtered[0]) select(filtered[0].id); else $("#main").innerHTML = `<div class="empty">No reels yet. Paste links on the left and press Run.</div>`;
  $("#main").scrollTop = 0;
}

function stepSlide(d){
  const v = $("#vid"), r = current; if (!v || !r || !r.slides) return;
  const L = r.slides, i = Math.max(0, Math.min(L.length - 1, L.indexOf(slideAt(L, v.currentTime)) + d)), x = L[i];
  v.currentTime = x.kind === "image" ? x.start + x.dur / 2 : x.start + 0.01;   // a photo: its middle, where its snapshot is
  if (x.kind === "image") v.pause();
  showSlidePos();
}
function showSlidePos(){
  const v = $("#vid"), el = $("#slpos"), r = current; if (!v || !el || !r || !r.slides) return;
  const x = slideAt(r.slides, v.currentTime);
  el.textContent = `Slide ${x.n} / ${r.slides.length}${x.kind === "video" ? " (video)" : ""}`;
  $("#slprev").disabled = x === r.slides[0]; $("#slnext").disabled = x === r.slides[r.slides.length - 1];
}

let capBusy = false;
async function capture(star){
  const v = $("#vid"); if (!v || !current || capBusy || isBusy(current)) return;
  capBusy = true;
  const r = current, t = v.currentTime;
  try {
    const j = await api("/api/shots/capture", {id: r.id, t});
    if (!j.existing) {
      r.times = {...r.times, [j.shot]: j.t}; r.mt = {...(r.mt || {}), [j.shot]: j.mt};
      r.manual = [...(r.manual || []), j.shot];
      r.shots = [...r.shots, j.shot].sort((a, b) => (r.times[a] - r.times[b]) || a.localeCompare(b));
    }
    if (star && !isFav(r, j.shot)) { r.state.favorites.push({shot: j.shot, text: textAt(r, j.shot), corrected: false}); saveState(r, true); renderList(); }
    if (current === r) {
      refreshShots(r);
      const fig = document.querySelector(`[data-fig="${CSS.escape(j.shot)}"]`);
      if (fig) { fig.classList.add("flash"); setTimeout(() => fig.classList.remove("flash"), 1200); }
    }
    toast(j.existing ? `Already have a snapshot at ${mmss(j.t)}` : `Snapshot at ${mmss(j.t)} added${star ? " and starred" : ""} (#${r.shots.indexOf(j.shot) + 1} of ${r.shots.length})`);
  } catch (e) { alert("Could not take the snapshot: " + e.message); }
  finally { capBusy = false; }
}

async function deleteShots(names, ask, allowFav=false){
  const r = current;
  if (isBusy(r)) return;
  const favHit = names.filter(n => isFav(r, n));
  const keep = allowFav ? names : names.filter(n => !isFav(r, n));
  if (!keep.length) return;
  if (ask && !confirm(`Delete ${keep.length} snapshot${keep.length>1?"s":""} that aren't favorites?\n\nYour ${r.state.favorites.length} favorite${r.state.favorites.length===1?"":"s"} stay. Deleted snapshots only come back with Re-process.`)) return;
  if (allowFav && favHit.length && !confirm(favHit.length === 1
      ? "This image is a favorite.\n\nDelete it? It is removed from your favorites and deleted from the snapshots."
      : `${favHit.length} of these are favorites.\n\nDelete them anyway? They are removed from your favorites and deleted from the snapshots.`)) return;
  let j;
  try { j = await api("/api/shots/delete", {id: r.id, shots: keep, force: allowFav}); } catch (e) { return alert(e.message); }
  const gone = new Set(j.deleted);
  r.shots = r.shots.filter(s => !gone.has(s));
  r.state.favorites = r.state.favorites.filter(f => !gone.has(f.shot));
  if (lb.list && $("#lightbox").classList.contains("show")) {
    lb.list = lb.fav ? r.state.favorites.map(f => f.shot) : r.shots;
    lb.i = Math.min(lb.i, lb.list.length - 1);
    if (lb.list.length) showLb(); else $("#lightbox").classList.remove("show");
  }
  refreshShots(r);
  renderList();
  toast(`Deleted ${gone.size} snapshot${gone.size===1?"":"s"}`);
}
/* snapshot choice: "auto", a maximum, or "every:N" (a snapshot every N seconds, typed in the box next to it) */
function shotsValue(){
  if ($("#maxshots").value !== "every") return $("#maxshots").value;
  const n = Math.min(600, Math.max(0.5, parseFloat($("#everyn").value) || 5));
  $("#everyn").value = n;
  return `every:${n}`;
}
function shotsText(v){
  if (v === "auto") return "Auto snapshots";
  if (v.startsWith("every:")) return `a snapshot every ${v.slice(6)} s`;
  return `up to ${v} snapshots`;
}
function saveShotsChoice(){ try { localStorage.setItem("rh-maxshots", $("#maxshots").value); localStorage.setItem("rh-everyn", $("#everyn").value); } catch (e) {} }
async function reprocess(r){
  if (!r.url) return alert("This reel has no source link, so it can't be re-processed.");
  const n = shotsValue(), nTxt = shotsText(n);
  const favNote = r.state.favorites.length ? `\n\nIts ${r.state.favorites.length} favorite${r.state.favorites.length===1?"":"s"} will be cleared when it finishes, because the snapshots are replaced (they are kept if the run fails).` : "";
  if (!confirm(`Re-process this video with ${nTxt}?\n\nThe transcript and snapshots are made again from the downloaded video (no new download). Your Obsidian note is not changed.${favNote}`)) return;
  await flushSaves();
  let j;
  try { j = await api("/api/run", {target: r.id, action: "redo", max_shots: n}); } catch (e) { return alert(e.message); }
  busyEpoch++; busyIds.add(r.id); lastRunTargets.add(r.id);
  if (j.queued) waitingIds.add(r.id);
  showBusy();
  toast(j.queued ? `Re-process queued: ${queuedText(j.queued)}` : "Re-processing... favorites are cleared only if it succeeds");
  $("#log").classList.add("show"); ensurePoll();
}
async function newVersion(r){
  const n = shotsValue(), nTxt = shotsText(n);
  if (!confirm(`Make a new version of this video with ${nTxt}?\n\nIt reuses the downloaded file (no new download). This version and its favorites stay as they are.`)) return;
  let j;
  try { j = await api("/api/run", {target: r.id, action: "new", max_shots: n}); } catch (e) { return alert(e.message); }
  if (j.queued) toast(`New version queued: ${queuedText(j.queued)}`);
  $("#log").classList.add("show"); ensurePoll();
}
const queuedText = n => n === 1 ? "it starts when the current run finishes" : `${n} ahead of it, it starts by itself`;
function chooseExisting(list, total, share){      // share: an inbox item (Add of a video you already have)
  return new Promise(res => {
    const box = $("#choice");
    $("#chtitle").textContent = share ? "You already have this video" : list.length === 1 ? "Already in your library" : `${list.length} of ${total} links are already in your library`;
    $("#chlist").innerHTML = list.slice(0, 6).map(x => `<li>${esc(x.label)}</li>`).join("") + (list.length > 6 ? `<li>and ${list.length - 6} more</li>` : "");
    $("#chtext").innerHTML = share ? `Add it as a <b>new version</b> with ${esc(share.from_name || share.from)}'s favorites and notes? Your copy and its favorites stay. The downloaded video is reused.`
      : "Run them again as a <b>new version</b>? The current one and its favorites stay. The downloaded video is reused.";
    $("#chskip").hidden = !!share; $("#chopen").textContent = share ? "Open mine" : "Open it";
    $("#chnote").textContent = !share && total > list.length ? "The other links run normally either way." : "";
    box.classList.add("show");
    if (document.activeElement) document.activeElement.blur();
    setTimeout(() => $("#chnew").focus(), 0);
    const done = v => { box.classList.remove("show"); res(v); };
    $("#chnew").onclick = () => done("new"); $("#chskip").onclick = () => done("skip");
    $("#chopen").onclick = () => done("open"); $("#chcancel").onclick = () => done(null);
    $("#chopen").hidden = list.length !== 1;
  });
}

/* ---------- delete ---------- */
function askDelete(rs){
  return new Promise(res => {
    const names = rs.slice(0, 4).map(r => "• " + r.label).join("\n") + (rs.length > 4 ? `\n• and ${rs.length - 4} more` : "");
    $("#ctitle").textContent = rs.length > 1 ? `Delete ${rs.length} reels?` : "Delete this reel?";
    $("#cmsg").textContent = names + "\n\nVideo, snapshots, transcript, favorites and notes move to Downloads\\reels\\_trash, so you can still restore them.";
    const hasNotes = rs.some(r => r.obsidian && r.obsidian.name);
    $("#cobs").checked = false; $("#cobs").closest("label").hidden = !hasNotes || !vault.ok;
    $("#cobs").closest("label").lastChild.textContent = " Also delete their Obsidian notes and images"; $("#cyes").textContent = "Delete";
    $("#confirm").classList.add("show");
    if (document.activeElement) document.activeElement.blur();
    setTimeout(() => $("#cno").focus(), 0);
    const done = v => { $("#confirm").classList.remove("show"); $("#cyes").onclick = $("#cno").onclick = null; res(v); };
    $("#cyes").onclick = () => done({ok: true, obsidian: $("#cobs").checked});
    $("#cno").onclick = () => done({ok: false});
  });
}
let deleting = false;
async function deleteReels(ids){
  if (!mayDelete()) return toast("Deleting videos is turned off for you on this laptop");
  const rs = ids.map(id => reels.find(r => r.id === id)).filter(Boolean);
  if (!rs.length || deleting || $("#confirm").classList.contains("show")) return;
  deleting = true;
  try { await deleteReelsNow(rs); } finally { deleting = false; }
}
async function deleteReelsNow(rs){
  const ans = await askDelete(rs);
  if (!ans.ok) return;
  const v = $("#vid"); if (v && rs.includes(current)) { v.pause(); v.removeAttribute("src"); v.load(); }   // release the file
  let j;
  try { j = await api("/api/delete", {ids: rs.map(r => r.id), obsidian: ans.obsidian}); }
  catch (e) { return alert("Could not delete: " + e.message); }
  const gone = new Set(j.deleted);
  const idx = filtered.findIndex(r => current && r.id === current.id);
  reels = reels.filter(r => !gone.has(r.id));
  gone.forEach(id => selected.delete(id));
  $("#count").textContent = reels.length ? `${reels.length} reel${reels.length>1?"s":""}` : "";
  if (current && gone.has(current.id)) current = null;
  renderList(); refreshSel();
  if (!current) {
    const next = filtered[Math.min(Math.max(idx, 0), filtered.length - 1)];
    if (next) select(next.id, true, true);
    else if (view !== "gallery") $("#main").innerHTML = `<div class="empty">No reels left. Paste links on the left and press Run.</div>`;
  }
  toast(`Deleted ${gone.size} reel${gone.size===1?"":"s"}` + (ans.obsidian ? " and their Obsidian notes" : ""));
  if (j.errors.length) alert("Some reels could not be deleted (close any program using them and try again):\n\n" + j.errors.join("\n"));
}

/* ---------- gallery ---------- */
function setView(v){
  view = v; try { localStorage.setItem("rh-view", v); } catch (e) {}
  $("#viewbtn").classList.toggle("on", v === "gallery");
  if (mode === "transcripts") return renderTrMain();
  if (v === "gallery") renderGallery(); else if (current) render(current);
  else $("#main").innerHTML = `<div class="empty">Pick a reel on the left.</div>`;
}
function cover(r){ const f = r.state.favorites[0]; return f ? f.shot : r.shots[0]; }
function renderGallery(){
  $("#viewbtn").classList.add("on");
  const q = $("#search").value.trim().toLowerCase();
  const groups = groupsFor();
  let html = "";
  let chHead = false;
  for (const g of groups) {
    if (g.kind === "cat" && !catShown(g.cat)) continue;
    const items = sortReels(reels.filter(r => g.test(r) && matches(r, q)));
    if (!items.length && (g.kind !== "cat" || q || fPlat.size)) continue;
    if (g.chan && !chHead) { chHead = true; html += `<h2 class="gchan">Channels</h2>`; }
    html += `<section class="gsec ${g.chan ? "gsub" : ""}" ${g.kind === "cat" ? `data-cat="${esc(g.cat)}"` : ""}><h3>${g.kind === "source" ? pbadge({platform: g.label}) + " " : ""}${esc(g.label)} <span class="gcount">${items.length}</span></h3>
      <div class="ggrid">${items.length ? items.map(r => `
        <div class="gcard ${current && current.id===r.id?"active":""} ${selected.has(r.id)?"sel":""}" data-open="${esc(r.id)}" tabindex="0" draggable="true">
          <span class="tick">&#10003;</span>
          <div class="gthumb">${cover(r) ? `<img loading="lazy" src="${shotUrl(r, cover(r))}" alt="">` : ""}
            ${r.state.favorites.length ? `<span class="gstar">&#9733; ${r.state.favorites.length}</span>` : ""}
            ${r.slides ? `<span class="gdur">${r.slides.length} slides</span>` : r.duration ? `<span class="gdur">${mmss(+r.duration)}</span>` : ""}</div>
          <div class="gtitle">${esc(r.label)}</div>
          <div class="s">${pbadge(r)}${vtag(r)} ${esc(r.author)} · ${esc(r.date)}${fromBadge(r)}</div>
        </div>`).join("") : `<div class="gdrop">Drop reels here</div>`}</div></section>`;
  }
  $("#main").innerHTML = `<div class="gallery"><div class="ghint">Click to open · <kbd>Ctrl</kbd>+click or <kbd>Shift</kbd>+click to select several · ${groupBy === "category" ? "drag onto a category to move" : "group by Category to move reels by dragging"}</div>${html || `<div class="empty">No reels match.</div>`}</div>`;
}

function select(id, scroll=true, keepView=false){
  if (current && current.id !== id) flushSaves();
  const r = reels.find(x => x.id === id); if (!r) return;
  if (mode === "transcripts") { current = r; return; }      // remembered; shown when you go back to Videos
  if (keepView && view === "gallery") {   // remember the reel without leaving the gallery
    current = r; document.querySelectorAll(".item").forEach(el => el.classList.toggle("active", el.dataset.id === id));
    return renderGallery();
  }
  current = r; history.replaceState(null, "", "#" + encodeURIComponent(id));
  document.querySelectorAll(".item").forEach(el => el.classList.toggle("active", el.dataset.id === id));
  if (scroll) { const el = document.querySelector(`.item[data-id="${CSS.escape(id)}"]`); el && el.scrollIntoView({block:"nearest"}); }
  if (view !== "detail") { view = "detail"; try { localStorage.setItem("rh-view", view); } catch (e) {} $("#viewbtn").classList.remove("on"); }
  render(r);
}

function setCategory(r, cat){
  r.state.category = cat;
  saveState(r, true);
  renderList();
  if (current && current.id === r.id && $("#catsel")) { $("#catsel").value = cat; updateNav(); }
}

async function catAction(body){
  try { const r = await api("/api/categories", body); cats = r.categories; chanCats = new Set(r.channel_cats || []); tags = r.tags || tags; }
  catch (e) { return alert(e.message); }
  if (body.action === "rename") reels.forEach(r => { if (r.state.category === body.name) r.state.category = body.new; });
  if (body.action === "rename" && fCat.delete(body.name)) { fCat.add(body.new); saveFilters(); }
  if (body.action === "delete") reels.forEach(r => { if (r.state.category === body.name) r.state.category = ""; });
  tr.items.forEach(it => { if (body.action === "rename" && it.category === body.name) it.category = body.new; if (body.action === "delete" && it.category === body.name) it.category = ""; });
  if (body.action === "rename" && tfCat.delete(body.name)) { tfCat.add(body.new); saveTrFilters(); }
  renderList();
  if (current && $("#catsel")) { $("#catsel").outerHTML = catSelect(current); updateNav(); }
  if (tr.current && $("#trcat")) $("#trcat").outerHTML = trCatSelect(tr.current);
}

/* ---------- detail ---------- */
function catSelect(r){
  return `<select id="catsel">${["", ...cats].map(c => `<option value="${esc(c)}" ${catOf(r)===c?"selected":""}>${c?esc(c):"Unassigned"}</option>`).join("")}</select>`;
}
function updateNav(){
  if (!current) return;
  const L = navList(), i = L.findIndex(x => x.id === current.id);
  $("#prev").disabled = i <= 0; $("#next").disabled = i < 0 || i >= L.length - 1;
  $("#pos").textContent = i < 0 ? "" : `${i+1} / ${L.length}`;
}

function render(r){
  setTimeout(() => { $("#main").classList.toggle("busy", busyIds.has(r.id)); $("#main").classList.toggle("waiting", waitingIds.has(r.id)); }, 0);
  const tabs = [["transcript","Transcript",r.transcript.length],["caption","Caption",""],["ocr","On-screen text",r.ocr.length],["notes","My notes",""]];
  $("#main").innerHTML = `
  <div class="detail">
    <div class="player">
      ${r.video ? `<video id="vid" controls playsinline preload="metadata" src="${media(r.id, r.video)}"></video>
      <div class="vsplit" data-split="video" role="separator" aria-orientation="horizontal" aria-label="Height of the video" tabindex="0" title="Drag to make the video taller or shorter · double-click for the standard height"></div>` : `<div class="empty">No video file</div>`}
      <div class="nav">
        <button class="btn" id="prev">&larr; Prev</button>
        <span class="pos" id="pos"></span>
        <button class="btn" id="next">Next &rarr;</button>
      </div>
      ${r.video ? `<div class="capbar"><button class="btn" id="capbtn" title="Take a snapshot of what's on screen now (S)">&#128248; Snapshot <span id="captime">00:00</span></button>
        <button class="btn" id="capstar" title="Snapshot and add it to favorites (Shift+S)">&#128248; + &#9733;</button></div>` : ""}
      ${r.video && r.slides ? `<div class="slidebar"><button class="btn" id="slprev" title="Previous slide (,)">&#9664; Slide</button>
        <span class="pos" id="slpos">Slide 1 / ${r.slides.length}</span>
        <button class="btn" id="slnext" title="Next slide (.)">Slide &#9654;</button></div>` : ""}
      <label class="catsel">Category ${catSelect(r)}</label>
      ${tagBox(r.state.tags || [], "rtags")}
      <div class="rowbtns">${shareTargets().map(p => `<button class="btn small" data-share="${esc(p.person)}" ${r.url ? "" : "disabled"} title="${r.url ? `Send the link, your favorites (times, text, edited images), notes and category to ${esc(p.name)}'s Reels Hub` : "This reel has no source link"}">&#8599; Share with ${esc(p.name)}</button>`).join("")}
        <button class="btn small" id="redo" title="Transcribe and take snapshots again, replacing this version">&#8635; Re-process</button>
        <button class="btn small" id="newver" title="Process the video again as a separate copy; this version and its favorites stay">+ New version</button>
        ${mayDelete() ? `<button class="btn small danger" id="delone">Delete this reel</button>` : ""}</div>
      <div class="meta shline" id="shline" hidden></div>
      <div class="meta">${esc(r.author)} · ${esc(r.date)}${r.slides ? ` · ${r.slides.length} slides` : r.duration?` · ${Math.round(r.duration)}s`:""}${r.url?` · <a href="${esc(r.url)}" target="_blank" rel="noopener">Open on ${esc(r.platform)}</a> · <button class="link" id="copylink" title="${esc(r.url)}">Copy link</button>`:""}</div>
      <div class="meta">${r.obsidian && r.obsidian.uri ? `Obsidian note: <a href="${esc(r.obsidian.uri)}" title="${esc(r.obsidian.path || "")}">${esc(r.obsidian.name)}</a>` : (r.obsidian && r.obsidian.error ? esc(r.obsidian.error) : "")}
        ${r.transcript.length || r.slides ? ` · <button class="link" id="copyai" title="Copies your Obsidian AI prompt with this transcript - paste it into ChatGPT or Claude">Copy AI prompt</button>` : ""}</div>
    </div>
    <div class="split" data-split="player" role="separator" aria-orientation="vertical" aria-label="Width of the video column" tabindex="0" title="Drag to change the width · double-click for the standard width"></div>
    <div class="right">
      <div class="card">
        <div class="tabs">${tabs.map(([k,l,n]) => `<div class="tab ${tab===k?"active":""}" data-tab="${k}">${l}${n!==""?`<span class="n">${n}</span>`:""}</div>`).join("")}</div>
        <div class="pane ${tab==="transcript"?"active":""}" data-pane="transcript">${
          r.transcript.length ? r.transcript.map(x => `<div class="tline" data-t="${x.t}"><span class="ts">${mmss(x.t)}</span><span>${esc(x.text)}</span></div>`).join("")
          : `<span class="meta">No speech detected</span>`}${r.lang?`<div class="meta" style="margin-top:8px">Language: ${esc(r.lang)}</div>`:""}</div>
        <div class="pane ${tab==="caption"?"active":""}" data-pane="caption">${esc(r.caption) || `<span class="meta">No caption</span>`}</div>
        <div class="pane ${tab==="ocr"?"active":""}" data-pane="ocr">${
          r.ocr.length ? r.ocr.map(x => `<div class="oline">${r.shots.includes(x.shot) ? `<img src="${shotUrl(r, x.shot)}" data-shot="${esc(x.shot)}" alt="">` : ""}<span><button class="ts tlink" data-seek="${r.times[x.shot]}" title="Play the video from here">${tlabel(r, r.times[x.shot])}</button> ${esc(x.text)}</span></div>`).join("")
          : `<span class="meta">No on-screen text found</span>`}</div>
        <div class="pane ${tab==="notes"?"active":""}" data-pane="notes">
          <textarea class="notes" id="notes" placeholder="Your notes: weight used, sets/reps, cues, how it felt">${esc(r.notesDraft !== undefined ? r.notesDraft : r.notes)}</textarea>
          <div style="margin-top:8px"><button class="btn primary" id="savenotes">Save notes</button><span class="saved" id="saved" hidden>Saved into note.md</span></div>
        </div>
      </div>
      <div id="favzone" class="favzone ${favFull?"full":""}">
      <div class="card">
        <div class="cardhead"><h2>Favorites · <span id="favcount"></span></h2>
          <span class="hint">Drag images or use &#9664; &#9654; to reorder · edit the text under each one</span>
          <button class="btn small" id="expandbtn">${favFull ? "&#10005; Close" : "&#10530; Expand"}</button>
          <button class="btn small" id="obsbtn" title="${esc(vault.ok ? "Copy the video and the starred images with their text into " + vault.path : "Obsidian vault not found: " + vault.path)}" ${vault.ok?"":"disabled"}>Send to Obsidian</button>
          <button class="btn primary small" id="printbtn">Print A4</button></div>
        <div class="obsline" id="obsline"></div>
        <div id="favs"></div>
      </div>
      <div class="card" id="shotscard">${shotsCard(r)}</div>
      </div>
    </div>
  </div>`;
  updateNav();
  renderFavs(r);
  showObsLine(r);
  showShareLine(r);
  const v = $("#vid");
  if (v) v.addEventListener("timeupdate", () => { const ct = $("#captime"); if (ct) ct.textContent = r.slides ? tlabel(r, v.currentTime) : mmss(Math.floor(v.currentTime)); });
  if (v && r.slides) { v.addEventListener("timeupdate", showSlidePos); v.addEventListener("seeked", showSlidePos); showSlidePos(); }
  if (v) v.addEventListener("timeupdate", () => {
    let best = null;
    document.querySelectorAll(".tline").forEach(el => { if (+el.dataset.t <= v.currentTime) best = el; el.classList.remove("now"); });
    best && best.classList.add("now");
  });
}

function shotsCard(r){
  const man = new Set(r.manual || []);
  return `<div class="cardhead"><h2>Snapshots · ${r.shots.length}</h2><span class="hint">&#9734; adds to favorites · &#10005; deletes · click to enlarge · <kbd>S</kbd> while watching adds one</span>
          <button class="btn small danger" id="delunstarred" ${r.shots.length > r.state.favorites.length ? "" : "disabled"}>Delete unstarred</button></div>
        <div class="shots">${r.shots.map(s => `
          <figure class="${(r.edited||[]).includes(s) ? "ed" : ""} ${man.has(s) ? "man" : ""}" data-fig="${esc(s)}"><img loading="lazy" src="${shotUrl(r, s)}" data-shot="${esc(s)}" alt="">
            <button class="star" data-star="${esc(s)}" title="Favorite">&#9734;</button>
            <button class="shotdel" data-delshot="${esc(s)}" title="Delete this snapshot">&#10005;</button>
            <button class="shotcopy" data-copyshot="${esc(s)}" title="Copy image">&#10697;</button>
            <figcaption><button class="tlink" data-seek="${r.times[s]}" title="Play the video from here">${man.has(s) ? "&#128248; " : "&#9654; "}${tlabel(r, r.times[s])}</button></figcaption></figure>`).join("")}</div>`;
}
function refreshShots(r){       // update snapshots + favorites without touching the video player
  if (!$("#shotscard")) return;
  $("#shotscard").innerHTML = shotsCard(r); renderFavs(r);
}

function renderFavs(r){
  const favs = r.state.favorites;
  $("#favcount").textContent = favs.length;
  $("#printbtn").disabled = !favs.length;
  const du = $("#delunstarred"); if (du) du.disabled = !(r.shots.length > favs.length);
  $("#favs").className = favs.length ? "favs" : "";
  $("#favs").innerHTML = favs.length ? favs.map((f, i) => `
    <div class="fav" data-i="${i}">
      <div class="favimg" draggable="true" title="Drag to reorder">
        <img src="${shotUrl(r, f.shot)}" alt="" draggable="false" data-favopen="${esc(f.shot)}" title="Click to enlarge · drag to reorder">
        <span class="num">${i+1}</span><button class="tb" data-seek="${r.times[f.shot]}" title="Play the video from here">&#9654; ${tlabel(r, r.times[f.shot])}</button>
        <button class="ico rm" data-unfav="${esc(f.shot)}" title="Remove from favorites">&#10005;</button>
        <button class="shotcopy fc2" data-copyshot="${esc(f.shot)}" title="Copy image">&#10697;</button>
        <button class="favtrash" data-delshot="${esc(f.shot)}" title="Delete this image (removes it from favorites and snapshots)">&#128465;</button>
      </div>
      <textarea class="favtext" data-i="${i}" placeholder="No speech here, type your own text">${esc(f.text)}</textarea>
      <div class="favfoot"><span class="corr" ${f.corrected?"":"hidden"}>Corrected text</span>
        <button class="link" data-reset="${i}" ${f.corrected?"":"hidden"}>Reset</button>
        <span class="mv"><button class="ico" data-mv="${i}:-1" title="Move left" ${i===0?"disabled":""}>&#9664;</button><button class="ico" data-mv="${i}:1" title="Move right" ${i===favs.length-1?"disabled":""}>&#9654;</button></span></div>
    </div>`).join("")
    : `<div class="favhint">No favorites yet. Click &#9734; on a snapshot below to add it here.</div>`;
  document.querySelectorAll("[data-star]").forEach(b => { const on = isFav(r, b.dataset.star); b.classList.toggle("on", on); b.innerHTML = on ? "&#9733;" : "&#9734;"; });
}

function toggleFav(r, shot){
  if (isBusy(r)) return;
  const favs = r.state.favorites, i = favs.findIndex(f => f.shot === shot);
  if (i >= 0) favs.splice(i, 1);
  else favs.push({shot, text: textAt(r, shot), corrected: false});
  saveState(r, true); renderFavs(r); renderList();
  if ($("#lightbox").classList.contains("show")) {
    if (lb.fav) {                             // favorites-only view: keep the list in step with your stars
      const cur = lb.list[lb.i];
      lb.list = r.state.favorites.map(f => f.shot);
      if (!lb.list.length) return $("#lightbox").classList.remove("show");
      lb.i = Math.max(0, Math.min(lb.list.includes(cur) ? lb.list.indexOf(cur) : lb.i, lb.list.length - 1));
    }
    showLb();
  }
}

/* ---------- obsidian ---------- */
function showObsLine(r, fresh){
  const o = r.obsidian || {};
  $("#obsline").innerHTML = o.sent ? `${fresh ? "&#10003; Sent" : "Last sent"} ${esc(o.sent)} to <a href="${esc(o.uri)}">${esc(o.name)}</a>` : "";
}
async function sendObsidian(){
  const r = current, b = $("#obsbtn");
  clearTimeout(saveTimers[r.id]); await api("/api/state", {id: r.id, state: r.state}).catch(() => {});
  b.disabled = true; b.textContent = "Sending...";
  try {
    const j = await api("/api/obsidian", {id: r.id});
    r.obsidian = {...(r.obsidian || {}), name: j.name, uri: j.uri, sent: j.sent};
    showObsLine(r, true);
  } catch (e) { alert("Could not send: " + e.message); }
  b.disabled = false; b.textContent = "Send to Obsidian";
}

/* ---------- expanded favorites ---------- */
function toggleFull(on){
  favFull = on === undefined ? !favFull : on;
  const z = $("#favzone"); if (!z) return;
  z.classList.toggle("full", favFull);
  document.body.style.overflow = favFull ? "hidden" : "";
  $("#expandbtn").innerHTML = favFull ? "&#10005; Close" : "&#10530; Expand";
  if (favFull) z.scrollTop = 0;
}

/* ---------- print ---------- */
function renderPaper(){
  const r = current, cols = +$("#pcols").value;
  $("#paper").innerHTML = `
    ${$("#ptitle").checked ? `<h1>${esc(r.label)}</h1><div class="psrc">${esc(r.author)} · ${esc(r.date)}${r.url?` · ${esc(r.url)}`:""}</div>` : ""}
    <div class="pgrid" style="grid-template-columns:repeat(${cols},1fr)">
      ${r.state.favorites.map((f, i) => `<figure class="pfig">
        <img src="${shotUrl(r, f.shot)}" alt="">
        <div class="pcap">${$("#pnums").checked?`<span class="pnum">${i+1}.</span>`:""}<b>${tlabel(r, r.times[f.shot])}</b>${esc(f.text)}</div>
        ${f.corrected ? `<div class="pcorr">Corrected text</div>` : ""}
      </figure>`).join("")}
    </div>`;
}

/* ---------- lightbox ---------- */
function openLightbox(shot, list){
  lb.list = list || current.shots; lb.fav = !!list;
  lb.i = Math.max(0, lb.list.indexOf(shot)); showLb(); $("#lightbox").classList.add("show");
}
/* ---------- clipboard ---------- */
async function copyText(text, msg){
  try { await navigator.clipboard.writeText(text); }
  catch (e) {                                  // fallback for browsers that block the clipboard API
    const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand("copy"); ta.remove();
  }
  toast(msg);
}
async function copyImage(shot){
  if (!shot || !current) return;
  try {
    // browsers only accept PNG on the clipboard: draw the JPG onto a canvas and convert
    const blob = await (await fetch(shotUrl(current, shot))).blob();
    const bmp = await createImageBitmap(blob);
    const cv = document.createElement("canvas"); cv.width = bmp.width; cv.height = bmp.height;
    cv.getContext("2d").drawImage(bmp, 0, 0);
    const png = await new Promise(res => cv.toBlob(res, "image/png"));
    await navigator.clipboard.write([new ClipboardItem({"image/png": png})]);
    toast("Image copied - paste it in Viber, WhatsApp... with Ctrl+V");
  } catch (e) {
    alert("Your browser blocked copying the image (" + e.message + ").\nOpen it, right-click it and choose Copy image instead.");
  }
}

/* ---------- image editor ---------- */
let ed = null;
const ED_HINT = {crop: "Drag over the part you want to keep", pen: "Draw with the mouse", arrow: "Drag from the tail to the tip",
  rect: "Drag to draw a box", text: "Click where the text should go, type, then press Enter", blur: "Drag over what you want to hide"};
const loadImage = src => new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = src; });
const cloneCv = c => { const n = document.createElement("canvas"); n.width = c.width; n.height = c.height; n.getContext("2d").drawImage(c, 0, 0); return n; };

async function openEditor(shot){
  if (!shot || !current) return;
  const img = await loadImage(shotUrl(current, shot) + (shotUrl(current, shot).includes("?") ? "&" : "?") + "e=" + Date.now());
  const base = document.createElement("canvas"); base.width = img.naturalWidth; base.height = img.naturalHeight;
  base.getContext("2d").drawImage(img, 0, 0);
  ed = {shot, base, undo: [], tool: "crop", dirty: false, drag: null};
  $("#edreset").hidden = !(current.edited || []).includes(shot);
  $("#editor").classList.add("show");
  setTool("crop"); edFit();
}
function closeEditor(){ ed = null; $("#editor").classList.remove("show"); $("#edtext").style.display = "none"; }
function setTool(t){
  ed.tool = t; $("#edhint").textContent = ED_HINT[t];
  document.querySelectorAll("#edtools button").forEach(b => b.classList.toggle("on", b.dataset.tool === t));
  $("#edov").style.cursor = t === "text" ? "text" : "crosshair";
  commitText();
}
function edFit(){
  const st = $("#edstage").getBoundingClientRect(), w = ed.base.width, h = ed.base.height;
  const k = Math.min((st.width - 24) / w, (st.height - 24) / h, 3);
  for (const id of ["#edcv", "#edov"]) { const c = $(id); c.width = w; c.height = h; c.style.width = w * k + "px"; c.style.height = h * k + "px"; }
  $("#edwrap").style.width = w * k + "px"; $("#edwrap").style.height = h * k + "px";
  ed.k = k; edDraw();
}
function edDraw(){ const c = $("#edcv").getContext("2d"); c.clearRect(0, 0, ed.base.width, ed.base.height); c.drawImage(ed.base, 0, 0); $("#edov").getContext("2d").clearRect(0, 0, ed.base.width, ed.base.height); }
function pushUndo(){ ed.undo.push(cloneCv(ed.base)); if (ed.undo.length > 30) ed.undo.shift(); ed.dirty = true; }
function edPt(e){ const r = $("#edov").getBoundingClientRect(); return {x: (e.clientX - r.left) / ed.k, y: (e.clientY - r.top) / ed.k}; }
function lw(){ return Math.max(2, ed.base.width * [0, .004, .008, .015][+$("#edsize").value]); }
function fs(){ return Math.max(12, ed.base.width * [0, .035, .05, .075][+$("#edsize").value]); }
function rectOf(a, b){ return {x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(a.x - b.x), h: Math.abs(a.y - b.y)}; }
function drawArrow(c, a, b, w){
  const ang = Math.atan2(b.y - a.y, b.x - a.x), head = w * 4.5;
  c.lineWidth = w; c.lineCap = "round"; c.beginPath(); c.moveTo(a.x, a.y);
  c.lineTo(b.x - Math.cos(ang) * head * .6, b.y - Math.sin(ang) * head * .6); c.stroke();
  c.beginPath(); c.moveTo(b.x, b.y);
  c.lineTo(b.x - head * Math.cos(ang - .45), b.y - head * Math.sin(ang - .45));
  c.lineTo(b.x - head * Math.cos(ang + .45), b.y - head * Math.sin(ang + .45)); c.closePath(); c.fill();
}
function pixelate(cv, r){
  if (r.w < 2 || r.h < 2) return;
  const blk = Math.max(6, cv.width / 60), sw = Math.max(1, Math.round(r.w / blk)), sh = Math.max(1, Math.round(r.h / blk));
  const t = document.createElement("canvas"); t.width = sw; t.height = sh;
  t.getContext("2d").drawImage(cv, r.x, r.y, r.w, r.h, 0, 0, sw, sh);
  const c = cv.getContext("2d"); c.imageSmoothingEnabled = false; c.drawImage(t, 0, 0, sw, sh, r.x, r.y, r.w, r.h); c.imageSmoothingEnabled = true;
}
function edDown(e){
  if (!ed || e.button !== 0) return;
  const p = edPt(e);
  if (ed.tool === "text") { e.preventDefault(); return startText(p); }
  commitText();
  $("#edov").setPointerCapture(e.pointerId);
  ed.drag = {a: p, b: p};
  if (ed.tool === "pen") { pushUndo(); ed.drag.last = p; }
}
function edMove(e){
  if (!ed || !ed.drag) return;
  const p = edPt(e), d = ed.drag, color = $("#edcolor").value; d.b = p;
  if (ed.tool === "pen") {
    const c = ed.base.getContext("2d"); c.strokeStyle = color; c.lineWidth = lw(); c.lineCap = "round"; c.lineJoin = "round";
    c.beginPath(); c.moveTo(d.last.x, d.last.y); c.lineTo(p.x, p.y); c.stroke(); d.last = p;
    return $("#edcv").getContext("2d").drawImage(ed.base, 0, 0);
  }
  const o = $("#edov").getContext("2d"), W = ed.base.width, H = ed.base.height, r = rectOf(d.a, p);
  o.clearRect(0, 0, W, H); o.strokeStyle = color; o.fillStyle = color;
  if (ed.tool === "crop") {
    o.fillStyle = "rgba(0,0,0,.55)"; o.fillRect(0, 0, W, H); o.clearRect(r.x, r.y, r.w, r.h);
    o.setLineDash([8, 6]); o.strokeStyle = "#fff"; o.lineWidth = Math.max(1, 1.5 / ed.k); o.strokeRect(r.x, r.y, r.w, r.h); o.setLineDash([]);
  } else if (ed.tool === "rect") { o.lineWidth = lw(); o.strokeRect(r.x, r.y, r.w, r.h); }
  else if (ed.tool === "arrow") drawArrow(o, d.a, p, lw());
  else if (ed.tool === "blur") { o.fillStyle = "rgba(128,128,128,.45)"; o.fillRect(r.x, r.y, r.w, r.h); o.setLineDash([6, 4]); o.strokeStyle = "#fff"; o.lineWidth = 1 / ed.k; o.strokeRect(r.x, r.y, r.w, r.h); o.setLineDash([]); }
}
function edUp(e){
  if (!ed || !ed.drag) return;
  const d = ed.drag; ed.drag = null;
  const r = rectOf(d.a, d.b), color = $("#edcolor").value, big = r.w > 4 && r.h > 4;
  const c = ed.base.getContext("2d"); c.strokeStyle = color; c.fillStyle = color;
  if (ed.tool === "crop" && big) {
    pushUndo();
    const n = document.createElement("canvas"); n.width = Math.round(r.w); n.height = Math.round(r.h);
    n.getContext("2d").drawImage(ed.base, r.x, r.y, r.w, r.h, 0, 0, n.width, n.height);
    ed.base = n; return edFit();
  }
  if (ed.tool === "rect" && big) { pushUndo(); c.lineWidth = lw(); c.strokeRect(r.x, r.y, r.w, r.h); }
  if (ed.tool === "arrow" && Math.hypot(d.b.x - d.a.x, d.b.y - d.a.y) > 6) { pushUndo(); drawArrow(c, d.a, d.b, lw()); }
  if (ed.tool === "blur" && big) { pushUndo(); pixelate(ed.base, r); }
  edDraw();
}
function startText(p){
  commitText();
  const inp = $("#edtext"), size = fs();
  ed.textAt = p;
  Object.assign(inp.style, {display: "block", left: p.x * ed.k + "px", top: (p.y * ed.k - size * ed.k * .8) + "px",
    fontSize: size * ed.k + "px", color: $("#edcolor").value});
  inp.value = ""; setTimeout(() => inp.focus(), 0);
}
function commitText(){
  const inp = $("#edtext");
  if (!ed || inp.style.display !== "block") return;
  const txt = inp.value.trim(); inp.style.display = "none";
  if (!txt) return;
  pushUndo();
  const c = ed.base.getContext("2d"), size = fs();
  c.font = `700 ${size}px system-ui, "Segoe UI", sans-serif`; c.textBaseline = "alphabetic";
  c.lineJoin = "round"; c.lineWidth = size / 6; c.strokeStyle = "rgba(0,0,0,.75)"; c.strokeText(txt, ed.textAt.x, ed.textAt.y);
  c.fillStyle = $("#edcolor").value; c.fillText(txt, ed.textAt.x, ed.textAt.y);
  edDraw();
}
function rotate(dir){
  commitText(); pushUndo();
  const o = ed.base, n = document.createElement("canvas"); n.width = o.height; n.height = o.width;
  const c = n.getContext("2d"); c.translate(n.width / 2, n.height / 2); c.rotate(dir * Math.PI / 2); c.drawImage(o, -o.width / 2, -o.height / 2);
  ed.base = n; edFit();
}
function edUndo(){ commitText(); if (!ed.undo.length) return; ed.base = ed.undo.pop(); ed.dirty = ed.undo.length > 0 || ed.dirty; edFit(); }
async function afterImageChange(shot, mt, edited){
  current.mt = {...(current.mt || {}), [shot]: mt};
  current.edited = edited ? [...new Set([...(current.edited || []), shot])] : (current.edited || []).filter(x => x !== shot);
  refreshShots(current);
  if ($("#lightbox").classList.contains("show")) showLb();
}
async function edSave(){
  commitText();
  if (!ed.dirty) return closeEditor();
  const shot = ed.shot, data = ed.base.toDataURL("image/jpeg", .92);
  $("#edsave").disabled = true;
  try { const j = await api("/api/shots/edit", {id: current.id, shot, data}); closeEditor(); await afterImageChange(shot, j.mt, true); toast("Image saved - the original is kept"); }
  catch (e) { alert("Could not save: " + e.message); }
  finally { $("#edsave").disabled = false; }
}
async function edReset(){
  if (!confirm("Throw away all edits and go back to the original snapshot?")) return;
  const shot = ed.shot;
  try { const j = await api("/api/shots/reset", {id: current.id, shot}); closeEditor(); await afterImageChange(shot, j.mt, false); toast("Back to the original"); }
  catch (e) { alert(e.message); }
}
async function edPaint(){
  if (ed.dirty && !confirm("Your edits here are not saved. Open the image in Paint anyway?")) return;
  const shot = ed.shot;
  try { await api("/api/shots/open", {id: current.id, shot}); }
  catch (e) { return alert(e.message); }
  closeEditor();
  if (!(current.edited || []).includes(shot)) current.edited = [...(current.edited || []), shot];
  toast("Opened in Paint - save there (Ctrl+S), then come back here");
}
function edKey(e){
  const typing = document.activeElement === $("#edtext");
  if (typing) {
    if (e.key === "Enter") { e.preventDefault(); commitText(); }
    if (e.key === "Escape") { e.preventDefault(); $("#edtext").style.display = "none"; }
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); return edUndo(); }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); return edSave(); }
  if (e.key === "Escape") { e.preventDefault(); if (!ed.dirty || confirm("Discard your edits?")) closeEditor(); }
}
// external edits (Paint): reload changed images when the window gets focus again
window.addEventListener("focus", async () => {
  if (!current || ed) return;
  try {
    const j = await api("/api/reels"), r = j.reels.find(x => x.id === current.id);
    if (!r) return;
    const changed = r.shots.some(s => (r.mt || {})[s] !== (current.mt || {})[s]);
    current.edited = r.edited;
    if (changed) { current.mt = r.mt; refreshShots(current);
      if ($("#lightbox").classList.contains("show")) showLb(); toast("Updated with your changes from Paint"); }
  } catch (e) {}
});
document.addEventListener("pointerdown", e => { if (e.target.id === "edov") edDown(e); });
document.addEventListener("pointermove", e => { if (ed && ed.drag) edMove(e); });
document.addEventListener("pointerup", e => { if (ed && ed.drag) edUp(e); });
window.addEventListener("resize", () => { if (ed) edFit(); });

let lbBusy = false;
async function lbDelete(){
  const s = lb.list[lb.i];
  if (lbBusy || !s) return;
  lbBusy = true;
  try { await deleteShots([s], false, true); }      // same position then shows the next snapshot
  finally { lbBusy = false; }
}
function showLb(){
  const s = lb.list[lb.i], on = isFav(current, s);
  $("#lbimg").src = shotUrl(current, s);
  $("#lbstar").classList.toggle("on", on); $("#lbstar").innerHTML = on ? "&#9733;" : "&#9734;";
  $("#lbdel").disabled = false;
  $("#lbdel").title = on ? "Delete this favorite image (asks first)" : "Delete this snapshot and show the next one (Delete key)";
  const t = textAt(current, s);
  $("#lbcap").textContent = `${lb.fav ? "★ Favorite " : ""}${lb.i+1} / ${lb.list.length} · ${tlabel(current, current.times[s])}`
    + ((current.edited || []).includes(s) ? " · edited" : "") + (t ? `  ·  ${t}` : "");
}

function move(d){
  if (!current) return;
  const L = navList(), i = L.findIndex(x => x.id === current.id) + d;
  if (i >= 0 && i < L.length) select(L[i].id);
}

/* ---------- resizable panels: drag the edge between list, video and text, or the bar under the video ---------- */
const SPLIT = {                      // handle -> CSS variable on <html>, direction, smallest size in px
  side:   {v: "--side-w",   x: true,  min: 240},
  filt:   {v: "--filt-w",   x: true,  min: 180},
  player: {v: "--player-w", x: true,  min: 240},
  video:  {v: "--video-h",  x: false, min: 180},
  top:    {v: "--top-h",    x: false, min: 60}
};
let panelPx = {}, splitDrag = null;
try { panelPx = JSON.parse(localStorage.getItem("rh-panels") || "{}"); } catch (e) {}
if (!panelPx || typeof panelPx !== "object" || Array.isArray(panelPx)) panelPx = {};
function setPanel(name, px){         // px null: back to the standard size
  if (px == null || !Number.isFinite(px)) { delete panelPx[name]; document.documentElement.style.removeProperty(SPLIT[name].v); }
  else { panelPx[name] = Math.round(px); document.documentElement.style.setProperty(SPLIT[name].v, panelPx[name] + "px"); }
}
function savePanels(){ try { localStorage.setItem("rh-panels", JSON.stringify(panelPx)); } catch (e) {} }
Object.keys(panelPx).forEach(n => { if (!SPLIT[n]) delete panelPx[n]; });
Object.keys(SPLIT).forEach(n => setPanel(n, panelPx[n]));
function panelBox(name){             // the element a handle resizes and the most room it may take (same limits as the CSS)
  if (name === "side") return {el: $("aside"), max: $(".layout").clientWidth - 560 - $("#fpane").offsetWidth};
  if (name === "filt") return {el: $("#fpane"), max: Math.min(480, $(".layout").clientWidth - 560 - $("aside").offsetWidth)};
  if (name === "player") {
    const d = $(".detail"); if (!d) return null;
    const s = getComputedStyle(d);
    return {el: $(".player"), max: d.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight) - 296};
  }
  if (name === "video") return $("#vid") ? {el: $("#vid"), max: innerHeight - 120} : null;
  if (name === "top") return {el: $("#sidetop"), max: $("aside").clientHeight - 150};
  return null;
}
function resizePanel(name, px, box){ const m = SPLIT[name].min; setPanel(name, Math.max(m, Math.min(Math.max(m, box.max), px))); }
document.addEventListener("pointerdown", e => {
  const h = e.target.closest && e.target.closest("[data-split]");
  if (!h || e.button !== 0 || !SPLIT[h.dataset.split]) return;
  const box = panelBox(h.dataset.split); if (!box || !box.el) return;
  e.preventDefault();
  const x = SPLIT[h.dataset.split].x, r = box.el.getBoundingClientRect();
  splitDrag = {name: h.dataset.split, h, box, x, from: x ? e.clientX : e.clientY, size: x ? r.width : r.height};
  try { h.setPointerCapture(e.pointerId); } catch (err) {}
  h.classList.add("on"); document.body.classList.add(x ? "resizing-x" : "resizing-y");
});
window.addEventListener("pointermove", e => {
  const d = splitDrag; if (!d) return;
  resizePanel(d.name, d.size + (d.x ? e.clientX : e.clientY) - d.from, d.box);
});
function endSplit(){
  const d = splitDrag; if (!d) return;
  splitDrag = null; d.h.classList.remove("on"); d.h.blur();         // so the arrow keys switch reels again
  document.body.classList.remove("resizing-x", "resizing-y"); savePanels();
}
window.addEventListener("pointerup", endSplit);
window.addEventListener("pointercancel", endSplit);
document.addEventListener("dblclick", e => {
  const h = e.target.closest && e.target.closest("[data-split]");
  if (h && SPLIT[h.dataset.split]) { setPanel(h.dataset.split, null); savePanels(); }
});
function splitKey(e, h){             // a handle reached with Tab: arrow keys resize, 20 px a step (Shift: 80)
  const name = h.dataset.split; if (!SPLIT[name]) return false;
  const x = SPLIT[name].x, d = (x ? {ArrowLeft: -1, ArrowRight: 1} : {ArrowUp: -1, ArrowDown: 1})[e.key];
  if (!d || e.ctrlKey || e.metaKey || e.altKey) return false;
  e.preventDefault();
  const box = panelBox(name);
  if (box && box.el) { const r = box.el.getBoundingClientRect(); resizePanel(name, (x ? r.width : r.height) + d * (e.shiftKey ? 80 : 20), box); savePanels(); }
  return true;
}

/* ---------- events ---------- */
document.addEventListener("click", async e => {
  const t = e.target, c = sel => t.closest(sel);
  let el;
  if ((el = c("[data-seek]"))) { e.preventDefault(); e.stopPropagation(); return seekTo(+el.dataset.seek); }
  if ((el = c("[data-fdtoggle]"))) { openDrop = openDrop === el.dataset.fdtoggle ? "" : el.dataset.fdtoggle; renderList();
    const qi = openDrop && document.querySelector(`[data-fdq="${openDrop}"]`); if (qi) qi.focus(); return; }
  if ((el = c("[data-fdclear]"))) { const k = el.dataset.fdclear;
    if (k === "cat") { fCat.clear(); saveFilters(); } else if (k === "tcat") { tfCat.clear(); saveTrFilters(); } else if (k === "tag") { fTag.clear(); saveFilters(); } else if (k === "ttag") { tfTag.clear(); saveTrFilters(); } else { tfChan.clear(); saveTrFilters(); }
    return renderList(); }
  if (c(".fdq")) return;
  if (openDrop && !c(".fdrop")) { openDrop = ""; renderList(); }
  if (c("#fpOpen") || (c(".fpane .rail-label"))) { panes.fp = true; return applyPanes(); }
  if (c("#fpFold")) { panes.fp = false; return applyPanes(); }
  if (c("#lsOpen") || c("#lsRail")) { panes.ls = true; return applyPanes(); }
  if (c("#lsFold")) { panes.ls = false; return applyPanes(); }
  if (c("[data-chtoggle]")) { const set = mode === "transcripts" ? trCollapsed : collapsed; set.has(CH_KEY) ? set.delete(CH_KEY) : set.add(CH_KEY);
    try { localStorage.setItem("rh-collapsed", JSON.stringify([...collapsed])); localStorage.setItem("rh-trcollapsed", JSON.stringify([...trCollapsed])); } catch (err) {}
    return renderList(); }
  if ((el = c("[data-tagren]"))) { const old = el.dataset.tagren; const n = prompt("Rename tag", old); if (n && n.trim() && n.trim() !== old) tagAction({action: "rename", name: old, new: n.trim()}); return; }
  if ((el = c("[data-tagdel]"))) { const name = el.dataset.tagdel, n = reels.filter(r => hasTag(r, name)).length + tr.items.filter(it => !it.reel && hasTag(it, name)).length;
    if (confirm(`Delete the tag #${name}? It is taken off ${n} video${n === 1 ? "" : "s"} and their notes.`)) tagAction({action: "delete", name}); return; }
  if (c("[data-tagnew]")) { const n = prompt("New tag (e.g. Kettlebell)"); if (n && n.trim()) tagAction({action: "add", name: n.trim()}); return; }
  if ((el = c("[data-untag]"))) { const box = el.closest(".tagbox"), t = tagTarget(box.id); if (t) saveTags(t, targetTags(t).filter(x => x !== el.dataset.untag)); return; }
  if ((el = c("[data-ftag]"))) { const v = el.dataset.ftag; fTag.has(v) ? fTag.delete(v) : fTag.add(v); saveFilters(); return renderList(); }
  if ((el = c("[data-tftag]"))) { const v = el.dataset.tftag; tfTag.has(v) ? tfTag.delete(v) : tfTag.add(v); saveTrFilters(); return renderTrList(); }
  if (t.id === "sortnotes") return sortNotes();
  if (t.id === "bulktag") { const n = prompt(`Tag to add to the ${selected.size} selected video${selected.size === 1 ? "" : "s"}`); if (!n || !n.trim()) return;
    (async () => { for (const id of [...selected]) { const r = reels.find(x => x.id === id); if (r && !hasTag(r, n.trim())) await saveTags({reel: id}, [...(r.state.tags || []), n.trim()], true); } redrawTags(); })(); return; }
  if ((el = c("[data-mode]"))) return setMode(el.dataset.mode);
  if ((el = c("[data-tid]"))) { const [ch, id] = splitTid(el.dataset.tid); const it = tr.items.find(x => x.ch === ch && x.id === id);
    if (it && it.reel && reels.some(r => r.id === it.reel)) { tr.current = it; setMode("reels"); return select(it.reel); }   // a downloaded video: open it (remembered for the way back)
    return selectTr(ch, id, false); }
  if ((el = c("[data-trtoggle]"))) { const k = el.closest(".group").dataset.trkey; trCollapsed.has(k) ? trCollapsed.delete(k) : trCollapsed.add(k);
    try { localStorage.setItem("rh-trcollapsed", JSON.stringify([...trCollapsed])); } catch (err) {} return renderTrList(); }
  if ((el = c("[data-tfchan]"))) { const v = el.dataset.tfchan; tfChan.has(v) ? tfChan.delete(v) : tfChan.add(v); saveTrFilters(); return renderTrList(); }
  if ((el = c("[data-tfplat]"))) { const v = el.dataset.tfplat; tfPlat.has(v) ? tfPlat.delete(v) : tfPlat.add(v); saveTrFilters(); return renderTrList(); }
  if ((el = c("[data-tfcat]"))) { const v = el.dataset.tfcat; tfCat.has(v) ? tfCat.delete(v) : tfCat.add(v); saveTrFilters(); return renderTrList(); }
  if (t.id === "tfclear") { tfChan.clear(); tfCat.clear(); tfPlat.clear(); tfTag.clear(); saveTrFilters(); return renderTrList(); }
  if ((el = c("[data-trrm]"))) return trRemoveChannel(el.dataset.trrm);
  if ((el = c("[data-trresume]"))) return trResume(el.dataset.trresume);
  if ((el = c("[data-trredo]"))) return trRedo(el.dataset.trredo);
  if (c("[data-sortch]")) return sortByChannel();
  if ((el = c("[data-ttab]"))) { document.querySelectorAll("[data-ttab]").forEach(x => x.classList.toggle("active", x === el));
    document.querySelectorAll("[data-tpane]").forEach(x => x.classList.toggle("active", x.dataset.tpane === el.dataset.ttab)); return; }
  if (c("#inboxbtn")) return openInbox();
  if (c("#capbtn")) return capture(false);
  if ((el = c("[data-share]"))) return current && shareReels([current.id], el.dataset.share);
  if ((el = c("[data-bulkshare]"))) return shareReels([...selected], el.dataset.bulkshare);
  if ((el = c("[data-qrm]"))) return removeQueued(el);
  if ((el = c("[data-iadd]"))) return inboxAdd(el.dataset.iadd);
  if ((el = c("[data-idismiss]"))) return inboxDismiss(el.dataset.idismiss);
  if ((el = c("[data-star]"))) return toggleFav(current, el.dataset.star);
  if ((el = c("[data-unfav]"))) return toggleFav(current, el.dataset.unfav);
  if ((el = c("[data-open]"))) { if (e.ctrlKey || e.metaKey || e.shiftKey) { e.preventDefault(); return pick(el.dataset.open, e); } clearSel(); return select(el.dataset.open); }
  if ((el = c("[data-fplat]"))) { const v = el.dataset.fplat; fPlat.has(v) ? fPlat.delete(v) : fPlat.add(v); saveFilters(); return renderList(); }
  if ((el = c("[data-fcat]"))) { const v = el.dataset.fcat; fCat.has(v) ? fCat.delete(v) : fCat.add(v); saveFilters(); return renderList(); }
  if (t.id === "fclear") { fPlat.clear(); fCat.clear(); fTag.clear(); saveFilters(); return renderList(); }
  if ((el = c("[data-mv]"))) { const [i, d] = el.dataset.mv.split(":").map(Number), favs = current.state.favorites, j = i + d;
    if (j >= 0 && j < favs.length) { favs.splice(j, 0, favs.splice(i, 1)[0]); saveState(current, true); renderFavs(current);
      const card = document.querySelector(`.fav[data-i="${j}"]`); card && card.scrollIntoView({block:"nearest", inline:"nearest"}); } return; }
  if ((el = c("[data-reset]"))) { const f = current.state.favorites[+el.dataset.reset]; f.text = textAt(current, f.shot); f.corrected = false; saveState(current, true); return renderFavs(current); }
  if ((el = c("[data-rename]"))) { const old = el.closest(".group").dataset.cat; const n = prompt("Rename category", old); if (n && n.trim() && n.trim() !== old) catAction({action:"rename", name: old, new: n.trim()}); return; }
  if ((el = c("[data-delcat]"))) { const n = el.closest(".group").dataset.cat; if (confirm(`Delete category "${n}"? Its reels move to Unassigned.`)) catAction({action:"delete", name: n}); return; }
  if ((el = c("[data-catmove]"))) return catAction({action:"move", name: el.closest(".group").dataset.cat, dir: +el.dataset.catmove});
  if ((el = c("[data-toggle]"))) { const g = el.closest(".group").dataset.label; collapsed.has(g) ? collapsed.delete(g) : collapsed.add(g);
    try { localStorage.setItem("rh-collapsed", JSON.stringify([...collapsed])); } catch (err) {} return renderList(); }
  if ((el = c(".item"))) { if (e.ctrlKey || e.metaKey || e.shiftKey) { e.preventDefault(); return pick(el.dataset.id, e); } clearSel(); return select(el.dataset.id, false); }
  if (t.id === "bulkclear") return clearSel();
  if (ed && (el = c("[data-tool]"))) return setTool(el.dataset.tool);
  if ((el = c("[data-copyshot]"))) return copyImage(el.dataset.copyshot);
  if ((el = c("[data-delshot]"))) return deleteShots([el.dataset.delshot], false, true);
  if (t.id === "delunstarred") return deleteShots(current.shots.filter(s => !isFav(current, s)), true);
  if (t.id === "redo") return reprocess(current);
  if (t.id === "newver") return newVersion(current);
  if (t.id === "bulkdel") return deleteReels([...selected]);
  if (t.id === "delone") return deleteReels([current.id]);
  if (t.id === "bulkall") { navList().forEach(r => selected.add(r.id)); return refreshSel(); }
  if ((el = c(".tab"))) { tab = el.dataset.tab;
    document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x===el));
    document.querySelectorAll(".pane").forEach(x => x.classList.toggle("active", x.dataset.pane===tab)); return; }
  if ((el = c(".tline"))) { const v = $("#vid"); if (v) { v.currentTime = +el.dataset.t; v.play(); } return; }
  if (t.dataset && t.dataset.favopen) return openLightbox(t.dataset.favopen, current.state.favorites.map(f => f.shot));
  if (t.dataset && t.dataset.shot && !c("#favs")) return openLightbox(t.dataset.shot);
  switch (t.id) {
    case "prev": return move(-1);
    case "next": return move(1);
    case "lightbox": case "lbimg": return $("#lightbox").classList.remove("show");
    case "lbprev": lb.i = (lb.i - 1 + lb.list.length) % lb.list.length; return showLb();
    case "lbnext": lb.i = (lb.i + 1) % lb.list.length; return showLb();
    case "lbstar": return toggleFav(current, lb.list[lb.i]);
    case "lbdel": return lbDelete();
    case "lbcopy": return copyImage(lb.list[lb.i]);
    case "lbedit": return openEditor(lb.list[lb.i]);
    case "home": return goHome();
    case "inboxclose": case "inbox": return closeInbox();
    case "inboxcheck": return inboxCheck();
    case "sttest": return testCloud();
    case "setbtn": return openSetup(false);
    case "stcancel": return $("#setup").classList.remove("show");
    case "stsave": return saveSetup();
    case "slprev": return stepSlide(-1);
    case "slnext": return stepSlide(1);
    case "copyai": { try { const j = await api("/api/aiprompt", {id: current.id}); copyText(j.text, "AI prompt + transcript copied - paste it into ChatGPT or Claude"); } catch (err) { alert(err.message); } return; }
    case "trbtn": return runTranscripts();
    case "ppause": return trControl($("#ppause").textContent === "Resume" ? "resume" : "pause");
    case "pstop": { if (confirm("Stop after the current video? What is done stays; Resume on the channel goes on from there later.")) trControl("stop"); return; }
    case "trget": return tr.current && trGet(tr.current);
    case "tropen": { const it = tr.current; if (!it || !it.full) return; setMode("reels"); return select(it.full); }
    case "trcopylink": return tr.current && copyText(tr.current.url, "Link copied - paste it anywhere with Ctrl+V");
    case "trcopytext": return tr.text && copyText(tr.text.lines.map(x => `${mmss(x.t)} ${x.text}`).join("\n"), "Transcript copied");
    case "trai": { if (!tr.current) return; try { const j = await api("/api/transcripts/aiprompt", {channel: tr.current.ch, id: tr.current.id}); copyText(j.text, "AI prompt + transcript copied - paste it into ChatGPT or Claude"); } catch (err) { alert(err.message); } return; }
    case "trrm1": return tr.current && trRemove(tr.current);
    case "lbplay": return seekTo(current.times[lb.list[lb.i]]);
    case "capstar": return capture(true);
    case "edrotl": return rotate(-1);
    case "edrotr": return rotate(1);
    case "edundo": return edUndo();
    case "edsave": return edSave();
    case "edreset": return edReset();
    case "edpaint": return edPaint();
    case "edcancel": if (!ed.dirty || confirm("Discard your edits?")) closeEditor(); return;
    case "copylink": return copyText(current.url, "Link copied - paste it anywhere with Ctrl+V");
    case "addcat": { const n = prompt("New category name (e.g. Training)"); if (n && n.trim()) catAction({action:"add", name: n.trim()}); return; }
    case "obsbtn": return sendObsidian();
    case "printbtn": renderPaper(); return $("#printsheet").classList.add("show");
    case "pclose": return $("#printsheet").classList.remove("show");
    case "pprint": return window.print();
    case "showlog": return $("#log").classList.add("show");
    case "viewbtn": if (mode === "transcripts") setMode("reels"); return setView(view === "gallery" ? "detail" : "gallery");
    case "expandbtn": return toggleFull();
    case "closelog": return $("#log").classList.remove("show");
    case "savenotes": {
      clearTimeout(notesTimers[current.id]); delete pending["n:" + current.id];
      if (isBusy(current)) return;
      try { await api("/api/notes", {id: current.id, notes: $("#notes").value, sb: sbAt(current)}); current.notes = $("#notes").value; delete current.notesDraft; $("#saved").textContent = "Saved into note.md"; $("#saved").hidden = false; }
      catch (err) { saveFailed(current, err, "notes"); } return;
    }
    case "run": {
      if (runBusy) return;
      runBusy = true; $("#run").disabled = true;
      try {
        saveShotsChoice();
        const body = {urls: $("#urls").value, max_shots: shotsValue()};
        let j;
        try { j = await api("/api/run", body); } catch (err) { return alert(err.message); }
        if (j.existing) {
          const pick = await chooseExisting(j.existing, j.total);
          if (!pick) return;
          if (pick === "open") { $("#urls").value = ""; return select(j.existing[0].id); }
          if (pick === "skip" && j.existing.length === j.total) { $("#urls").value = ""; return toast("Nothing to run: all links are already done"); }
          try { j = await api("/api/run", {...body, existing: pick}); } catch (err) { return alert(err.message); }
        }
        const left = j.skipped ? ` (${j.skipped} link${j.skipped > 1 ? "s were" : " was"} already running or waiting, left out)` : "";
        $("#urls").value = "";
        if (j.queued) toast(`Queued: ${queuedText(j.queued)}${left}`);           // waits in line: see Waiting
        else { if (left) toast("Running" + left); $("#log").classList.add("show"); }
        ensurePoll();
      } finally { runBusy = false; $("#run").disabled = false; }   // Run is off only while a press is being sent
      return;
    }
  }
});

document.addEventListener("input", e => {
  if (e.target.id === "notes" && current) { $("#saved").hidden = true; return notesChanged(current, e.target.value); }
  if (e.target.classList && e.target.classList.contains("fdq")) {     // filter the drop-down's list without redrawing it
    const k = e.target.dataset.fdq, q = e.target.value.toLowerCase(); dropQ[k] = e.target.value;
    e.target.closest(".fdpanel").querySelectorAll(".fdopt").forEach(o => o.hidden = !!q && !o.querySelector(".fdl").textContent.toLowerCase().includes(q));
    return;
  }
  if (e.target.id === "search") return renderList();   // in Transcripts: the catalogue (renderList hands over)
  if (e.target.classList.contains("favtext")) {
    const f = current.state.favorites[+e.target.dataset.i];
    f.text = e.target.value;
    f.corrected = f.text.trim() !== textAt(current, f.shot).trim();
    const foot = e.target.nextElementSibling;
    foot.querySelector(".corr").hidden = !f.corrected; foot.querySelector("[data-reset]").hidden = !f.corrected;
    saveState(current);
  }
});
document.addEventListener("change", async e => {
  if (e.target.tagName === "SELECT" && e.target.closest("#editor") === null) setTimeout(() => e.target.blur(), 0);
  if ((e.target.id === "groupby" || e.target.id === "sortby") && mode === "transcripts") {
    trGroupBy = $("#groupby").value; sortBy = $("#sortby").value;
    try { localStorage.setItem("rh-trgroupby", trGroupBy); localStorage.setItem("rh-sortby", sortBy); } catch (err) {}
    return renderTrList();
  }
  if (e.target.id === "groupby" || e.target.id === "sortby") {
    groupBy = $("#groupby").value; sortBy = $("#sortby").value;
    try { localStorage.setItem("rh-groupby", groupBy); localStorage.setItem("rh-sortby", sortBy); } catch (err) {}
    renderList(); if (current) updateNavSafe(); return;
  }
  if (e.target.id === "bulkcat") {
    const v = e.target.value; let cat;
    if (v === "new") {
      const n = prompt("New category name"); if (!n || !n.trim()) return refreshSel();
      await catAction({action: "add", name: n.trim()}); if (!cats.includes(n.trim())) return refreshSel(); cat = n.trim();
    } else cat = v.slice(4);
    moveReels([...selected], cat); return clearSel();
  }
  if (e.target.matches && e.target.matches("[data-tagin]")) {
    const v = e.target.value.replace(/#/g, "").trim(), box = e.target.dataset.tagin, t = tagTarget(box); e.target.value = "";
    if (v && t && !targetTags(t).some(x => x.toLowerCase() === v.toLowerCase())) saveTags(t, [...targetTags(t), v]).then(() => redrawTags(box));
    return;
  }
  if (e.target.id === "catsel") setCategory(current, e.target.value);
  if (e.target.id === "trcat" && tr.current) trSetCategory(tr.current, e.target.value);
  if (e.target.matches && e.target.matches("[data-icat]")) { const it = e.target.closest("[data-iid]"); if (it) inboxCat[it.dataset.iid] = e.target.value; }
  if (["pcols","ptitle","pnums"].includes(e.target.id)) renderPaper();
});

/* drag: favorites reorder + reels onto categories */
document.addEventListener("dragstart", e => {
  const h = e.target.closest && e.target.closest(".favimg");
  if (h) { dragFav = +h.closest(".fav").dataset.i; h.closest(".fav").classList.add("dragging"); e.dataTransfer.setData("text/plain", "fav"); e.dataTransfer.effectAllowed = "move"; return; }
  const it = e.target.closest && (e.target.closest(".item") || e.target.closest(".gcard"));
  if (it && it.dataset.tid) {                                // a transcript: onto a category group
    e.dataTransfer.setData("text/plain", "tr:" + JSON.stringify([it.dataset.tid])); e.dataTransfer.effectAllowed = "move";
    it.classList.add("dragging"); return;
  }
  if (it) {
    const id = it.dataset.id || it.dataset.open;
    const ids = selected.has(id) ? [...selected] : [id];
    e.dataTransfer.setData("text/plain", "reels:" + JSON.stringify(ids)); e.dataTransfer.effectAllowed = "move";
    if (ids.length > 1) {   // custom drag image: "N reels"
      const g = $("#dragghost"); g.textContent = `${ids.length} reels`; e.dataTransfer.setDragImage(g, 20, 14);
    }
    ids.forEach(x => document.querySelectorAll(`[data-id="${CSS.escape(x)}"],[data-open="${CSS.escape(x)}"]`).forEach(el => el.classList.add("dragging")));
  }
});
document.addEventListener("dragend", () => { dragFav = null; document.querySelectorAll(".dragging,.over,.drop").forEach(x => x.classList.remove("dragging","over","drop")); });
document.addEventListener("dragover", e => {
  const fav = e.target.closest && e.target.closest(".fav");
  if (fav && dragFav !== null) { e.preventDefault(); document.querySelectorAll(".fav.over").forEach(x => x !== fav && x.classList.remove("over")); fav.classList.add("over"); return; }
  if (dragFav !== null) return;
  const sec = e.target.closest && e.target.closest(".gsec");
  const g = e.target.closest && (e.target.closest(".group") || e.target.closest(".item") || e.target.closest(".gempty"));
  let target = sec || (g && groupFor(g));
  if (target && !target.hasAttribute("data-cat")) target = null;   // only category groups accept drops
  if (target) { e.preventDefault(); document.querySelectorAll(".drop").forEach(x => x !== target && x.classList.remove("drop")); target.classList.add("drop"); }
});
function groupFor(el){ // the category header that a list element sits under
  if (el.classList.contains("group")) return el;
  let p = el.previousElementSibling; while (p && !p.classList.contains("group")) p = p.previousElementSibling; return p;
}
document.addEventListener("drop", e => {
  const fav = e.target.closest && e.target.closest(".fav");
  if (fav && dragFav !== null) {
    e.preventDefault();
    const to = +fav.dataset.i, favs = current.state.favorites;
    if (to !== dragFav) { favs.splice(to, 0, favs.splice(dragFav, 1)[0]); saveState(current, true); renderFavs(current); }
    dragFav = null; return;
  }
  const data = e.dataTransfer.getData("text/plain");
  const sec = e.target.closest && e.target.closest(".gsec");
  const g = e.target.closest && (e.target.closest(".group") || e.target.closest(".item") || e.target.closest(".gempty"));
  const target = sec || (g && groupFor(g));
  if (target && target.hasAttribute("data-cat") && data.startsWith("reels:")) {
    e.preventDefault();
    let ids = []; try { ids = JSON.parse(data.slice(6)); } catch (err) {}
    moveReels(ids, target.dataset.cat);
    if (ids.length > 1) clearSel();
  }
  if (target && target.hasAttribute("data-cat") && data.startsWith("tr:")) {
    e.preventDefault();
    let tids = []; try { tids = JSON.parse(data.slice(3)); } catch (err) {}
    tids.forEach(tid => { const [ch, id] = splitTid(tid); const it = tr.items.find(x => x.ch === ch && x.id === id); if (it && trCatOf(it) !== target.dataset.cat) trSetCategory(it, target.dataset.cat); });
  }
  document.querySelectorAll(".drop").forEach(x => x.classList.remove("drop"));
});

document.addEventListener("keydown", e => {
  if (ed) return edKey(e);
  if (openDrop && e.key === "Escape") { e.preventDefault(); openDrop = ""; return renderList(); }
  if ($("#setup").classList.contains("show")) {
    if (e.key === "Enter" && document.activeElement && document.activeElement.closest("#setup") && document.activeElement.tagName === "INPUT") { e.preventDefault(); saveSetup(); }
    if (e.key === "Escape" && !setupFirst) $("#setup").classList.remove("show");
    return;
  }
  if ($("#choice").classList.contains("show")) {           // the "already in your library" dialog owns the keyboard
    if (e.key === "Escape") { e.preventDefault(); $("#chcancel").click(); }
    else if (e.key === "Enter") { e.preventDefault(); if (document.activeElement && document.activeElement.closest("#choice")) document.activeElement.click(); }
    else if (e.key !== "Tab") e.preventDefault();
    return;
  }
  if ($("#inbox").classList.contains("show") && !$("#confirm").classList.contains("show")) {
    if (e.key === "Escape") { e.preventDefault(); closeInbox(); }
    return;
  }
  if ($("#confirm").classList.contains("show") && e.key !== "Enter" && e.key !== "Escape" && e.key !== "Tab") { e.preventDefault(); return; }
  const sp = document.activeElement && document.activeElement.closest && document.activeElement.closest("[data-split]");
  if (sp && splitKey(e, sp)) return;
  if (e.key === "Enter" && document.activeElement && document.activeElement.classList.contains("gcard")) { e.preventDefault(); return select(document.activeElement.dataset.open); }
  const mod = e.ctrlKey || e.metaKey || e.altKey;
  if ($("#lightbox").classList.contains("show") && !mod && (e.key === "e" || e.key === "E") && !/TEXTAREA|INPUT|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); return openEditor(lb.list[lb.i]); }
  if (/TEXTAREA|INPUT|SELECT/.test(document.activeElement.tagName)) return;
  if ($("#printsheet").classList.contains("show")) { if (e.key === "Escape") $("#printsheet").classList.remove("show"); return; }
  if (mode === "transcripts" && !$("#lightbox").classList.contains("show") && !$("#confirm").classList.contains("show")) {
    if (!mod && (e.key === "ArrowLeft" || e.key === "ArrowRight")) { e.preventDefault(); trMove(e.key === "ArrowLeft" ? -1 : 1); }
    return;                                                // the catalogue: no snapshot keys, no reel deleting
  }
  const open = $("#lightbox").classList.contains("show");
  if (e.key === "Escape" && !open && favFull) return toggleFull(false);
  if ($("#confirm").classList.contains("show")) {
    if (e.key === "Escape") { e.preventDefault(); $("#cno").click(); }
    if (e.key === "Enter") { e.preventDefault(); $("#cyes").click(); }
    return;
  }
  if (e.key === "Escape" && !open && selected.size) return clearSel();
  if (!open && view !== "gallery" && (e.key === "s" || e.key === "S") && !e.ctrlKey && !e.metaKey && !e.altKey && $("#vid")) { e.preventDefault(); return capture(e.shiftKey); }
  if (!open && view !== "gallery" && !mod && (e.key === "," || e.key === ".") && current && current.slides && $("#vid")) { e.preventDefault(); return stepSlide(e.key === "," ? -1 : 1); }
  if (e.key === "Delete" && !open && selected.size) return deleteReels([...selected]);
  if (e.key === "Escape") $("#lightbox").classList.remove("show");
  if (view === "gallery" && !open) return;
  if (open && !mod && (e.key === "f" || e.key === "F")) toggleFav(current, lb.list[lb.i]);
  if (open && e.key === "Delete") { e.preventDefault(); return lbDelete(); }
  if (open && !mod && (e.key === "p" || e.key === "P")) { e.preventDefault(); return seekTo(current.times[lb.list[lb.i]]); }
  if (open && (e.key === "c" || e.key === "C") && !e.altKey) { e.preventDefault(); return copyImage(lb.list[lb.i]); }
  if (!open && document.activeElement && document.activeElement.tagName === "VIDEO" && (e.key === "ArrowLeft" || e.key === "ArrowRight")) return;   // let the player seek
  if (e.key === "ArrowLeft") open ? (lb.i = (lb.i-1+lb.list.length)%lb.list.length, showLb()) : move(-1);
  if (e.key === "ArrowRight") open ? (lb.i = (lb.i+1)%lb.list.length, showLb()) : move(1);
});

/* ---------- sharing: Share with ..., the inbox ---------- */
let inbox = {on: false, items: [], checked: "", error: "", busy: false, last: null, as: "", changed: []};
let inboxCat = {}, inboxTimer = null, lastAddAt = null, sharing = false;
function applyMe(){
  $("#who").textContent = me.name || "";
  $("#bulkdel").hidden = !mayDelete();
  refreshInboxButton();
}
function refreshInboxButton(){
  const n = inbox.items.length, b = $("#inboxbtn");
  b.hidden = !(inbox.on || n || (me.share && me.share.on));
  $("#inboxcount").hidden = !n; $("#inboxcount").textContent = n;
  b.title = n ? `${n} video${n > 1 ? "s" : ""} shared with you` : "Videos shared with you";
}
function showShareLine(r){
  const el = $("#shline"); if (!el || !r) return;
  const st = r.state || {}, parts = [], last = {};
  if (st.shared_by) parts.push(`&#8600; Shared with you by <b>${esc(fromName(r))}</b>${st.shared_by.at ? " · " + esc(st.shared_by.at) : ""}`);
  (st.shared || []).forEach(x => { last[x.to] = x; });
  Object.values(last).forEach(x => parts.push(`&#8599; Shared with ${esc(x.name || x.to)} · ${esc(x.at || "")}`));
  el.innerHTML = parts.join("<br>"); el.hidden = !parts.length;
}
async function shareReels(ids, person){
  const p = shareTargets().find(x => x.person === person);
  if (!p || sharing) return;
  const rs = ids.map(id => reels.find(r => r.id === id)).filter(Boolean);
  if (!rs.length) return;
  if (rs.some(r => busyIds.has(r.id))) return toast("A video is being re-processed - share it when it finishes");
  if (rs.length > 1 && !confirm(`Share ${rs.length} reels with ${p.name}?\n\n${p.name}'s Reels Hub gets the links, your favorites (times, text and the images you edited), your notes and categories, through your Cloudflare inbox. ${p.name}'s laptop downloads the videos itself.`)) return;
  sharing = true;
  toast(`Sharing with ${p.name}...`);
  try {
    await flushSaves();
    const j = await api("/api/share", {ids: rs.map(r => r.id), to: person});
    j.shared.forEach(x => { const r = reels.find(y => y.id === x.id); if (r) r.state.shared = x.shared; });
    if (current && j.shared.some(x => x.id === current.id)) showShareLine(current);
    if (j.shared.length) toast(j.shared.length === 1 ? `Shared with ${p.name}` : `Shared ${j.shared.length} reels with ${p.name}`);
    const msg = [...j.errors, ...j.notes];
    if (msg.length) alert((j.errors.length ? (j.shared.length ? "Some were not shared:\n\n" : "Not shared:\n\n") : "") + msg.join("\n"));
  } catch (e) { alert("Could not share: " + e.message); }
  finally { sharing = false; }
}
function fmtWhen(v){
  const d = v ? new Date(v) : null;
  return d && !isNaN(d) ? d.toLocaleString(undefined, {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"}) : String(v || "");
}
function inboxCatOptions(it){
  const sug = String(it.category || "").trim(), same = cats.find(c => c.toLowerCase() === sug.toLowerCase());
  const pick = inboxCat[it.id] !== undefined ? inboxCat[it.id] : (same || sug);
  const opts = [["", "Unassigned"], ...cats.map(c => [c, c + (c === same ? " (suggested)" : "")])];
  if (sug && !same) opts.push([sug, `${sug} (new, suggested)`]);
  return opts.map(([v, l]) => `<option value="${esc(v)}" ${v === pick ? "selected" : ""}>${esc(l)}</option>`).join("");
}
function renderInbox(){
  const a = document.activeElement;
  if (a && a.tagName === "SELECT" && a.closest("#inboxlist")) return;     // don't close a list you have open
  const L = inbox.items;
  $("#inboxsub").textContent = L.length ? `${L.length} waiting` : "";
  $("#inboxlist").innerHTML = L.length ? L.map(it => {
    const n = it.favorites.length, ed = it.favorites.filter(f => f.image).length, adding = it.status === "adding", who = esc(it.from_name || it.from);
    return `<div class="iitem" data-iid="${esc(it.id)}">
      <div class="ititle">${pbadge({platform: it.platform})} ${esc(it.title || it.url)}</div>
      <div class="imeta">From <b>${who}</b> · ${esc(fmtWhen(it.sent) || it.received)} · ${n} favorite${n === 1 ? "" : "s"}${ed ? ` (${ed} edited)` : ""}${it.notes ? " · notes" : ""} · <a href="${esc(it.url)}" target="_blank" rel="noopener">open link</a></div>
      ${it.have && it.have.length ? `<div class="istate">You already have this video: Add asks whether to make a new version with ${who}'s favorites</div>` : ""}
      ${it.notes ? `<div class="inotes">${esc(it.notes)}</div>` : ""}
      <div class="irow"><label class="meta">Category <select data-icat ${it.status ? "disabled" : ""}>${inboxCatOptions(it)}</select></label><span class="spacer"></span>
        ${it.status ? `<span class="istate">${adding ? "Adding: downloading and processing..." : "Waiting for the current run..."}</span>` : ""}
        <button class="btn small" data-idismiss="${esc(it.id)}" ${adding ? "disabled" : ""}>Dismiss</button>
        <button class="btn small primary" data-iadd="${esc(it.id)}" ${it.status ? "disabled" : ""} title="Download and process the video on this laptop, then fill in ${who}'s favorites, notes and category">Add</button></div>
      ${it.error ? `<div class="ierr">${esc(it.error)}</div>` : ""}
    </div>`; }).join("")
    : `<div class="iempty">Nothing waiting. When someone shares a video with you, it shows up here.</div>`;
  $("#inboxfoot").innerHTML = !inbox.on ? "Sharing is not set up on this laptop: add the Cloudflare address and this laptop's token in Settings."
    : `${inbox.busy ? "Checking..." : inbox.checked ? `Last checked ${esc(inbox.checked)}` : "Not checked yet"}${inbox.as ? ` · this laptop is <b>${esc(inbox.as)}</b> in the inbox` : ""} · checked every 90 seconds while Reels Hub runs`
      + (inbox.error ? `<span class="ierr">${esc(inbox.error)}</span>` : "");
}
async function refreshInbox(soon){
  clearTimeout(inboxTimer);
  let j = null;
  try { j = await api("/api/inbox"); } catch (e) {}
  if (j) {
    inbox = j;
    refreshInboxButton();
    if ($("#inbox").classList.contains("show")) renderInbox();
    if (j.last && lastAddAt !== null && j.last.at > lastAddAt) toast(`Added "${j.last.title || "the video"}" from ${j.last.from_name || "the inbox"}`);
    lastAddAt = Math.max(lastAddAt || 0, j.last ? j.last.at : 0);
    if (j.running) ensurePoll();
    else if (!polling) { const ch = takeChanged(j.changed, j.boot); if (ch.size) loadReels(current && current.id, ch).catch(() => {}); }
  }
  const active = j && (j.busy || j.running || j.items.some(it => it.status));
  inboxTimer = setTimeout(() => refreshInbox(), active || soon ? 1500 : 20000);
}
function openInbox(){
  if (document.activeElement) document.activeElement.blur();
  renderInbox(); $("#inbox").classList.add("show");
  if (inbox.on) api("/api/inbox/check", {}).catch(() => {});     // look for new shares now, not in 90 s
  refreshInbox(true);
}
function closeInbox(){ $("#inbox").classList.remove("show"); }
async function inboxCheck(){
  try { await api("/api/inbox/check", {}); } catch (e) { return alert(e.message); }
  inbox.busy = true; renderInbox(); refreshInbox(true);
}
async function inboxAdd(id, existing){
  const it = inbox.items.find(x => x.id === id); if (!it) return;
  const sel = document.querySelector(`[data-iid="${CSS.escape(id)}"] [data-icat]`);
  const category = sel ? sel.value : (inboxCat[id] !== undefined ? inboxCat[id] : "");
  let j;
  try { j = await api("/api/inbox/add", {id, category, max_shots: shotsValue(), ...(existing ? {existing} : {})}); }
  catch (e) { return alert("Could not add: " + e.message); }
  if (j.existing) {
    const pick = await chooseExisting(j.existing, 1, it);
    if (pick === "open") { closeInbox(); return select(j.existing[0].id); }
    if (pick === "new") return inboxAdd(id, "new");
    return;
  }
  it.status = "queued"; it.error = ""; renderInbox();
  toast(j.queued > 1 ? `Queued (${j.queued - 1} ahead of it): it is downloaded and processed on this laptop` : "Adding: the video is downloaded and processed on this laptop");
  refreshInbox(true);
}
async function inboxDismiss(id){
  const it = inbox.items.find(x => x.id === id); if (!it) return;
  if (!confirm(`Dismiss "${it.title || it.url}" from ${it.from_name || it.from}?\n\nIt is not added to your library.`)) return;
  try { await api("/api/inbox/dismiss", {id}); } catch (e) { return alert(e.message); }
  inbox.items = inbox.items.filter(x => x.id !== id); delete inboxCat[id];
  renderInbox(); refreshInboxButton(); toast("Dismissed");
}
async function testCloud(){
  const out = $("#sttestres"), b = $("#sttest");
  out.className = "meta"; out.textContent = "Connecting..."; b.disabled = true;
  try {
    const j = await api("/api/cloud/test", {cloud_url: $("#stcurl").value, cloud_id: $("#stcid").value, cloud_secret: $("#stcsec").value});
    const others = (j.people || []).map(p => p.name).join(", "), typed = $("#stname").value.trim();
    out.className = "ok";
    out.textContent = `✓ Connected as ${j.name}` + (others ? ` · can share with ${others}` : " · no one else in PEOPLE yet")
      + (j.can_delete ? "" : ` · deleting videos is off for ${j.name}`)
      + (typed && typed.toLowerCase() !== String(j.name).toLowerCase() ? ` · note: this token belongs to ${j.name}, not ${typed}` : "");
  } catch (e) { out.className = "bad"; out.textContent = e.message; }
  finally { b.disabled = false; }
}

/* ---------- progress / ETA ---------- */
const hms = sec => { sec = Math.max(0, Math.round(sec)); const h = Math.floor(sec/3600), m = Math.floor(sec%3600/60), x = sec%60;
  return h ? `${h}:${String(m).padStart(2,"0")}:${String(x).padStart(2,"0")}` : `${m}:${String(x).padStart(2,"0")}`; };
function showProgress(s){
  const p = s.progress, box = $("#prog");
  $("#pbtns").hidden = !(s.running && s.transcripts);
  $("#ppause").textContent = s.paused ? "Resume" : "Pause";
  $("#ppause").disabled = !!s.stopping; $("#pstop").disabled = !!s.stopping;
  if (!s.running || !p) { box.hidden = !s.running; if (s.running) { $("#pstage").textContent = "Starting..."; $("#peta").textContent = ""; $("#pfill").style.width = "0%"; $("#pvid").textContent = ""; $("#pel").textContent = ""; } return; }
  box.hidden = false;
  $("#pstage").textContent = `${p.label} ${p.stage_pct ? Math.round(p.stage_pct) + "%" : ""}`;
  $("#peta").textContent = p.stage === "paused" ? "paused" : s.stopping ? "stopping after this video" : p.eta == null ? "estimating..." : (p.eta_partial ? `download ~${hms(p.eta)} left` : `~${hms(p.eta)} left`);
  $("#pfill").style.width = `${Math.min(100, p.stage_pct || 0)}%`;
  $("#pfill").dataset.stage = p.stage;
  $("#pvid").textContent = p.n > 1 ? `Video ${p.i} of ${p.n}` : "This video";
  $("#pel").textContent = `${hms(p.elapsed)} elapsed`;
}

/* ---------- transcripts: the catalogue of transcript-only videos (by channel) ---------- */
const splitTid = s => { const i = s.indexOf("/"); return [s.slice(0, i), s.slice(i + 1)]; };
const srcBadge = it => it.source === "video" ? `<span class="srcb w" title="From the downloaded video (Whisper)">VID</span>`
  : /whisper/i.test(it.source) ? `<span class="srcb w" title="Transcribed with Whisper (no captions on the site)">W</span>`
  : /automatic/i.test(it.source) ? `<span class="srcb" title="The site's automatic captions">AUTO</span>`
  : /caption/i.test(it.source) ? `<span class="srcb cc" title="The site's captions (the creator's on YouTube)">CC</span>` : "";
const SITE = {youtube: "YouTube", tiktok: "TikTok", instagram: "Instagram", x: "X"};
const srcText = it => { const s = String(it.source || ""); if (s === "video") return "From the downloaded video (Whisper)"; if (/whisper/i.test(s)) return "Whisper (no captions on " + (SITE[(it.platform || "").toLowerCase()] || it.platform || "the site") + ")";
  const site = SITE[s.split(" ")[0]] || s.split(" ")[0]; return s ? `${site} captions${/automatic/i.test(s) ? " (automatic)" : site === "YouTube" ? " (the creator's)" : ""}` : ""; };
const atTime = (url, t) => /youtube\.com|youtu\.be/.test(url || "") ? url.replace(/([?&])t=\d+s?&?/, "$1").replace(/[?&]$/, "") + (url.includes("?") ? "&" : "?") + `t=${Math.floor(t)}s` : url;
function setMode(m){
  mode = m; openDrop = ""; try { localStorage.setItem("rh-mode", m); } catch (e) {}
  const trm = m === "transcripts";
  document.querySelectorAll(".mode").forEach(b => { const on = b.dataset.mode === m; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); });
  $("#lsRail").textContent = trm ? "Transcripts" : "Videos";
  document.querySelector("aside").classList.toggle("trmode", trm);
  setGroupOptions(m);
  $("#search").placeholder = trm ? "Search transcripts" : "Search";
  if (trm) { clearSel(); renderTrList(); renderTrMain(); loadTr().catch(() => {}); }
  else { renderList(); setView(view); }
}
async function loadTr(){
  const j = await api("/api/transcripts");
  if (j.channel_cats) chanCats = new Set(j.channel_cats);
  if (j.tags) tags = j.tags;
  tr.channels = j.channels || []; tr.items = (j.items || []).map(it => ({...it, label: it.title, version: 1}));
  const n = tr.items.length; $("#trcount").textContent = n; $("#trcount").hidden = !n;
  if (tr.current) tr.current = tr.items.find(it => it.ch === tr.current.ch && it.id === tr.current.id) || null;
  if (mode !== "transcripts") return;
  renderTrList();
  if (!tr.current) { if (tr.items[0]) selectTr(tr.items[0].ch, tr.items[0].id, false); else renderTrMain(); }
  else if (JSON.stringify(tr.current) !== tr.shown) renderTrMain();     // its note or video appeared: redraw
}
const trCatOf = it => cats.includes(it.category) ? it.category : "";
const chanName = key => { const c = tr.channels.find(x => x.key === key); return c ? c.name : key; };
function saveTrFilters(){ try { localStorage.setItem("rh-trfilters", JSON.stringify({c: [...tfCat], p: [...tfPlat], t: [...tfTag]})); } catch (e) {} }
function trMatches(it, q){
  if (tfChan.size && !tfChan.has(it.ch)) return false;
  if (tfPlat.size && !tfPlat.has(it.platform)) return false;
  if (tfCat.size && !tfCat.has(trCatOf(it))) return false;
  if (tfTag.size && ![...tfTag].some(t => hasTag(it, t))) return false;
  if (!q) return true;
  const ch = tr.channels.find(c => c.key === it.ch);
  return `${it.title} ${it.author} ${it.date} ${ch ? ch.name + " " + ch.handle : ""} ${it.category} ${tagsOf(it).join(" ")}`.toLowerCase().includes(q);
}
function renderTrFilters(){
  // Source chips as under Videos; the channels are in the category list (under Channels), so no separate Channel list
  tfChan.clear();
  const plats = [...new Set(tr.items.map(it => it.platform))].sort();
  [...tfPlat].forEach(p => plats.includes(p) || tfPlat.delete(p));
  [...tfCat].forEach(c => (c === "" || cats.includes(c)) || tfCat.delete(c));
  const count = pred => tr.items.filter(pred).length;
  const chip = (attr, val, label, on, n) => `<button class="chip ${on?"on":""}" ${attr}="${esc(val)}">${label}<span class="cn">${n}</span></button>`;
  [...tfTag].forEach(t => tags.includes(t) || tfTag.delete(t));
  const active = tfPlat.size + tfCat.size + tfTag.size;
  $("#filters").innerHTML = (plats.length > 1 || tfPlat.size ? `<div class="frow"><span class="fl">Source</span>${plats.map(p =>
      chip("data-tfplat", p, pbadge({platform: p}) + " " + esc(p), tfPlat.has(p), count(it => it.platform === p))).join("")}</div>` : "")
    + flist("tcat", "Category", "data-tfcat", [...orderCats(cats).filter(c => !chanCats.has(c)), "", ...orderCats(cats).filter(c => chanCats.has(c))].map(c => ({val: c, label: c || "Unassigned", chan: chanCats.has(c), n: count(it => trCatOf(it) === c && (!tfPlat.size || tfPlat.has(it.platform)))}))
        .filter(o => !tfPlat.size || o.n || tfCat.has(o.val)), tfCat)
    + tagList("ttag", "data-tftag", tfTag, tr.items, it => !tfPlat.size || tfPlat.has(it.platform))
    + (active ? `<div class="frow"><button class="link" id="tfclear">Clear filters (${active})</button></div>` : "");
}
function trGroups(){
  if (trGroupBy === "category")
    return [...orderCats(cats).map(c => ({key: "cat:" + c, label: c, cat: c, kind: "cat", chan: chanCats.has(c), test: it => trCatOf(it) === c})),
            {key: "cat:", label: "Unassigned", cat: "", kind: "cat", unas: true, test: it => trCatOf(it) === ""}];
  if (trGroupBy === "none") return [{key: "all", label: "All transcripts", kind: "none", test: () => true}];
  return tr.channels.map(ch => ({key: "ch:" + ch.key, label: ch.name, kind: "channel", ch, test: it => it.ch === ch.key}));
}
function renderTrList(){
  const q = $("#search").value.trim().toLowerCase();
  renderTrFilters();
  const filtering = q || tfChan.size || tfCat.size || tfPlat.size || tfTag.size;
  const parts = [];
  const groups = trGroups();
  groups.forEach((g, gi) => {
    if (g.kind === "cat" && tfCat.size && !tfCat.has(g.cat)) return;
    const items = sortReels(tr.items.filter(it => g.test(it) && trMatches(it, q)));
    if (g.kind === "channel" && !items.length && filtering) return;
    if (g.kind === "cat" && !items.length && filtering) return;
    const closed = trCollapsed.has(g.key);
    const ch = g.ch;
    let html = `<div class="group ${closed?"closed":""}" data-trkey="${esc(g.key)}" ${g.kind === "cat" ? `data-cat="${esc(g.cat)}"` : ""} ${ch ? `data-trch="${esc(ch.key)}"` : ""}>
      <span class="gname" data-trtoggle title="${esc(ch ? (ch.handle || ch.name) : g.label)}"><span class="caret">&#9662;</span>${ch && ch.platform ? pbadge({platform: ch.platform}) + " " : ""}${esc(g.label)}</span>
      <span class="gcount">${ch && items.length !== ch.count ? `${items.length} / ${ch.count}` : items.length}</span>
      ${ch && ch.note ? `<a class="ico" href="${esc(ch.note.uri)}" title="Open the channel note in Obsidian (${esc(ch.note.path)})">&#128196;</a>` : ""}
      ${ch && ch.url ? `<a class="ico" href="${esc(ch.url)}" target="_blank" rel="noopener" title="Open the channel on ${esc(ch.platform || "YouTube")}">&#8599;</a>` : ""}
      ${ch && !ch.videos_only ? `<button class="ico" data-trrm="${esc(ch.key)}" title="Remove this channel's transcripts (downloaded videos stay)">&#10005;</button>` : ""}
      ${g.kind === "cat" && g.cat ? `<button class="ico" data-catmove="-1" title="Move up" ${gi===0?"disabled":""}>&#8593;</button>
                  <button class="ico" data-catmove="1" title="Move down" ${gi===cats.length-1?"disabled":""}>&#8595;</button>
                  <button class="ico" data-rename title="Rename">&#9998;</button>
                  <button class="ico" data-delcat title="Delete category">&#10005;</button>` : ""}
    </div>${ch ? runLine(ch) : ""}`;
    if (!closed) html += items.length ? items.map(it => `
      <div class="item tr ${tr.current && tr.current.ch === it.ch && tr.current.id === it.id ? "active" : ""}" data-tid="${esc(it.ch)}/${esc(it.id)}" draggable="true">
        <div><div class="t">${esc(it.title)}</div>
        <div class="s">${g.kind !== "channel" ? esc(chanName(it.ch)) + " · " : ""}${esc(it.date)}${it.duration ? ` · ${mmss(+it.duration)}` : ""} · ${srcBadge(it)}${g.kind !== "cat" && trCatOf(it) ? ` · <span class="tcat">${esc(trCatOf(it))}</span>` : ""}${it.full ? ` · <span class="fc" title="This video is in Videos, in full">&#9654; video</span>` : ""}${it.note || !vault.ok ? "" : ` · <span title="No Obsidian note yet">no note</span>`}${tagsOf(it).length ? ` · <span class="ttg">${tagsOf(it).map(t => "#" + esc(t)).join(" ")}</span>` : ""}</div></div>
      </div>`).join("") : `<div class="gempty">${g.kind === "cat" && g.cat ? "Drag transcripts here or pick this category on a transcript" : "Nothing here"}</div>`;
    parts.push({html, chan: !!g.chan || g.kind === "channel", unas: !!g.unas, n: items.length});
  });
  const html = assemble(parts, trCollapsed);
  $("#list").innerHTML = html || `<div class="empty">${tr.items.length ? "No matches" : "No transcripts yet.<br><br>Paste a YouTube channel link, a playlist link or video links above and press <b>Transcripts</b>."}</div>`;
}
const trCatSelect = it => `<select id="trcat">${["", ...cats].map(c => `<option value="${esc(c)}" ${trCatOf(it)===c?"selected":""}>${c?esc(c):"Unassigned"}</option>`).join("")}</select>`;
async function trSetCategory(it, cat){
  if (it.reel) { const r = reels.find(x => x.id === it.reel); if (r) { setCategory(r, cat); it.category = cat; renderTrList(); } return; }
  try { await api("/api/transcripts/state", {channel: it.ch, id: it.id, category: cat}); } catch (err) { return alert(err.message); }
  it.category = cat;
  renderTrList();
  const sel = $("#trcat"); if (sel && tr.current === it) sel.value = cat;
}
function runLine(ch){                       // under a channel: how far its last transcripts run got, and Resume
  const wl = ch.wrong_lang ? `<div class="gstat"><span class="warn">${ch.wrong_lang} from captions in another language</span> <button class="btn small" data-trredo="${esc(ch.key)}" title="List them, then redo them with the language set in Settings">Redo</button></div>` : "";
  return wl + runLine0(ch);
}
async function sortByChannel(){
  let j;
  try { j = await api("/api/sortchannels", {dry: true}); } catch (err) { return alert(err.message); }
  if (!j.count) return toast("No unassigned video has a known channel");
  const names = j.categories.slice(0, 10).join(", ") + (j.categories.length > 10 ? ` and ${j.categories.length - 10} more` : "");
  const ans = await askConfirm(`Sort ${j.count} unassigned video${j.count > 1 ? "s" : ""} by channel?`,
    `Each goes into a category named after its channel or creator: ${names}. Missing categories are made. Videos that already have a category are not touched.`, null, "Sort");
  if (!ans.ok) return;
  try { j = await api("/api/sortchannels", {}); } catch (err) { return alert(err.message); }
  await loadReels(current && current.id, new Set(reels.map(r => r.id)));
  toast(`Sorted ${j.count} video${j.count === 1 ? "" : "s"}`);
}
async function trRedo(key){
  let j;
  try { j = await api("/api/transcripts/wronglang", {channel: key}); } catch (err) { return alert(err.message); }
  if (!j.items.length) return toast("Nothing to redo");
  const list = j.items.slice(0, 12).map(x => `• [${x.lang}] ${x.title}`).join("\n") + (j.items.length > 12 ? `\n• and ${j.items.length - 12} more` : "");
  const ans = await askConfirm(`Redo ${j.items.length} transcript${j.items.length > 1 ? "s" : ""} in "${j.lang}"?`,
    `${list}\n\nTheir folders move to _trash\\_transcripts and their Obsidian notes to the vault's trash (notes you edited too: copy what you wrote first). Then the channel runs again and makes them new, with the same category. Videos with no ${j.lang} captions come back in their own language.`, null, "Redo");
  if (!ans.ok) return;
  let r;
  try { r = await api("/api/transcripts/redo", {channel: key, ids: j.items.map(x => x.id)}); } catch (err) { return alert(err.message); }
  if (r.errors && r.errors.length) alert(r.errors.join("\n"));
  toast(r.queued ? `Queued: ${queuedText(r.queued)}` : "Redoing - see the Log"); ensurePoll(); loadTr().catch(() => {});
}
function runLine0(ch){
  const r = ch.run; if (!r) return "";
  const nxt = r.next ? ` · next: ${esc(r.next)}` : "";
  const resume = (k, label) => `<button class="btn small" data-trresume="${esc(k)}" title="Lists the channel again and does what is not here yet">${label}</button>`;
  if (r.state === "running") return `<div class="gstat">Running · ${r.done} of ${r.todo} done${r.failed ? `, ${r.failed} failed` : ""}${nxt}</div>`;
  if (r.state === "stopped" || r.state === "interrupted")
    return `<div class="gstat"><span class="warn">${r.state === "stopped" ? "Stopped" : "Interrupted"} after ${r.done + r.failed} of ${r.todo}</span>${nxt}${r.left ? ` · ${r.left} left ${resume(ch.key, "Resume")}` : ""}</div>`;
  if (r.state === "finished") return `<div class="gstat">Last run ${esc(r.at || "")}: ${r.listed} on the channel, ${ch.count} here${r.failed ? `, <span class="warn">${r.failed} failed</span> ${resume(ch.key, "Retry")}` : ""}</div>`;
  return "";
}
async function trResume(key){
  const ch = tr.channels.find(c => c.key === key); if (!ch || !ch.run || !ch.run.url) return;
  let j;
  try { j = await api("/api/run", {urls: ch.run.url, transcripts: true}); } catch (err) { return alert(err.message); }
  toast(j.queued ? `Queued: ${queuedText(j.queued)}` : "Resuming - see the Log"); ensurePoll();
}
async function trControl(action){
  try { await api("/api/transcripts/control", {action}); } catch (err) { return toast(err.message); }
  toast(action === "pause" ? "Pausing after the current video" : action === "resume" ? "Resumed" : "Stopping after the current video");
  ensurePoll();
}
function selectTr(ch, id, scroll=true){
  const it = tr.items.find(x => x.ch === ch && x.id === id); if (!it) return;
  tr.current = it;
  document.querySelectorAll(".item.tr").forEach(el => el.classList.toggle("active", el.dataset.tid === `${ch}/${id}`));
  if (scroll) { const el = document.querySelector(`.item.tr[data-tid="${CSS.escape(ch + "/" + id)}"]`); el && el.scrollIntoView({block: "nearest"}); }
  renderTrMain();
}
function trMove(d){
  const order = [...document.querySelectorAll(".item.tr")].map(el => el.dataset.tid);
  if (!order.length) return;
  const i = tr.current ? order.indexOf(`${tr.current.ch}/${tr.current.id}`) : -1, j = i < 0 ? 0 : i + d;
  if (j >= 0 && j < order.length) { const [ch, id] = splitTid(order[j]); selectTr(ch, id); }
}
function renderTrMain(){
  tr.text = null;
  const it = tr.current;
  tr.shown = it ? JSON.stringify(it) : "";
  if (!it) {
    $("#main").innerHTML = `<div class="empty">${tr.items.length ? "Pick a video on the left." : "No transcripts yet.<br><br>Paste a YouTube channel link, a playlist link or video links on the left and press <b>Transcripts</b>: each video gets its transcript as a note in Obsidian, without downloading the video."}</div>`;
    return;
  }
  const ch = tr.channels.find(c => c.key === it.ch);
  $("#main").innerHTML = `
  <div class="trview">
    <h2>${esc(it.title)}</h2>
    <div class="meta">${esc(ch ? ch.name : it.author)} · ${esc(it.date)}${it.duration ? ` · ${mmss(+it.duration)}` : ""}${it.lang ? ` · ${esc(it.lang)}` : ""} · ${srcBadge(it)} ${esc(srcText(it))}${it.url ? ` · <a href="${esc(it.url)}" target="_blank" rel="noopener">Open on ${esc(it.platform)}</a> · <button class="link" id="trcopylink" title="${esc(it.url)}">Copy link</button>` : ""}</div>
    <div class="meta">${it.note ? `Obsidian note: <a href="${esc(it.note.uri)}" title="${esc(it.note.path)}">${esc(it.note.name)}</a>` : (vault.ok ? "No Obsidian note yet (it is made when the run finishes this video)" : "Obsidian vault not set: see Settings")}${it.full ? ` · <span class="from" title="Processed in full: video, snapshots, on-screen text">&#9654; in your videos</span>` : ""}</div>
    <label class="catsel">Category ${trCatSelect(it)}</label>
    ${tagBox(it.tags || [], "ttags")}
    <div class="rowbtns">
      ${it.full ? `<button class="btn small" id="tropen" title="Open this video under Videos">&#9654; Open the video</button>`
                : `<button class="btn small" id="trget" title="Download the video and process it in full (Whisper transcript, snapshots, on-screen text). Its Obsidian note is kept: the caption text is replaced by Whisper's transcript.">&#11015; Get the video</button>`}
      <button class="btn small" id="trcopytext" title="Copy the transcript as text">Copy transcript</button>
      <button class="btn small" id="trai" title="Copies your Obsidian AI prompt with this transcript - paste it into ChatGPT or Claude">Copy AI prompt</button>
      ${it.reel && it.id === it.reel ? "" : `<button class="btn small danger" id="trrm1" title="Take this transcript out of the catalogue">Remove</button>`}
    </div>
    <div class="card">
      <div class="tabs"><div class="tab active" data-ttab="text">Transcript<span class="n">${it.lines || ""}</span></div><div class="tab" data-ttab="desc">Description</div></div>
      <div class="pane active" data-tpane="text" id="trtext"><span class="meta">Loading...</span></div>
      <div class="pane trdesc" data-tpane="desc" id="trdesc"></div>
    </div>
  </div>`;
  loadTrText(it);
}
async function loadTrText(it){
  let j;
  const r = it.reel && reels.find(x => x.id === it.reel);
  if (r) j = {lines: r.transcript, caption: r.caption, lang: r.lang, title: r.title, url: r.url};   // the video's own transcript
  else try { j = await api("/api/transcripts/text", {channel: it.ch, id: it.id}); }
  catch (e) { const box = $("#trtext"); if (box) box.innerHTML = `<span class="meta">${esc(e.message)}</span>`; return; }
  if (!tr.current || tr.current.id !== it.id || tr.current.ch !== it.ch || !$("#trtext")) return;
  tr.text = j;
  $("#trtext").innerHTML = j.lines.length ? j.lines.map(x => `<div class="trline"><a class="ts" href="${esc(atTime(it.url, x.t))}" target="_blank" rel="noopener" title="Open the video at this moment">${mmss(x.t)}</a><span>${esc(x.text)}</span></div>`).join("")
    : `<span class="meta">No transcript</span>`;
  $("#trdesc").textContent = j.caption || "No description";
}
async function runTranscripts(){
  if (runBusy) return;
  runBusy = true; $("#trbtn").disabled = true;
  try {
    let j;
    try { j = await api("/api/run", {urls: $("#urls").value, transcripts: true}); } catch (err) { return alert(err.message); }
    const left = j.skipped ? ` (${j.skipped} link${j.skipped > 1 ? "s were" : " was"} already running or waiting, left out)` : "";
    $("#urls").value = "";
    if (j.queued) toast(`Queued: ${queuedText(j.queued)}${left}`);
    else { toast("Getting transcripts - see the Log" + left); $("#log").classList.add("show"); }
    ensurePoll();
    if (mode !== "transcripts") setMode("transcripts");
  } finally { runBusy = false; $("#trbtn").disabled = false; }
}
async function trGet(it){
  let j;
  try { j = await api("/api/run", {urls: it.url, max_shots: shotsValue(), existing: "skip"}); } catch (err) { return alert(err.message); }
  if (j.queued) toast(`Queued: ${queuedText(j.queued)}`);
  else { toast("Downloading the video - it shows under Videos when it is done"); $("#log").classList.add("show"); }
  ensurePoll();
}
function askConfirm(title, msg, obsLabel, yes){
  return new Promise(res => {
    $("#ctitle").textContent = title; $("#cmsg").textContent = msg;
    const lab = $("#cobs").closest("label");
    $("#cobs").checked = false; lab.hidden = !obsLabel; lab.lastChild.textContent = " " + (obsLabel || "");
    $("#cyes").textContent = yes || "Remove";
    $("#confirm").classList.add("show");
    if (document.activeElement) document.activeElement.blur();
    setTimeout(() => $("#cno").focus(), 0);
    const done = v => { $("#confirm").classList.remove("show"); $("#cyes").onclick = $("#cno").onclick = null; res(v); };
    $("#cyes").onclick = () => done({ok: true, obsidian: $("#cobs").checked});
    $("#cno").onclick = () => done({ok: false});
  });
}
async function trRemove(it){
  if ($("#confirm").classList.contains("show")) return;
  const ans = await askConfirm("Remove this transcript?", `${it.title}\n\nIts folder moves to _trash\\_transcripts in the reels folder, so you can still restore it.`,
    it.note && !it.full && vault.ok ? "Also move its Obsidian note to the vault's trash" : null);
  if (!ans.ok) return;
  let j;
  try { j = await api("/api/transcripts/remove", {channel: it.ch, ids: [it.id], obsidian: ans.obsidian}); } catch (err) { return alert(err.message); }
  if (j.errors && j.errors.length) alert(j.errors.join("\n"));
  tr.current = null;
  try { await loadTr(); } catch (err) {}
  toast("Removed");
}
async function trRemoveChannel(key){
  const ch = tr.channels.find(c => c.key === key); if (!ch || $("#confirm").classList.contains("show")) return;
  const ans = await askConfirm(`Remove all ${ch.count} transcripts of ${ch.name}?`, `Their folders move to _trash\\_transcripts in the reels folder, so you can still restore them. Videos you processed in full stay under Videos.`,
    vault.ok ? "Also move their Obsidian notes (and the channel note) to the vault's trash" : null);
  if (!ans.ok) return;
  let j;
  try { j = await api("/api/transcripts/remove", {channel: key, all: true, obsidian: ans.obsidian}); } catch (err) { return alert(err.message); }
  if (j.errors && j.errors.length) alert(j.errors.join("\n"));
  if (tr.current && tr.current.ch === key) tr.current = null;
  try { await loadTr(); } catch (err) {}
  toast(`Removed ${j.removed.length} transcript${j.removed.length === 1 ? "" : "s"}`);
}

/* ---------- run polling ---------- */
let lastRunTargets = new Set(), runBusy = false, polling = false, changedSeq = null, bootId = null;
function takeChanged(list, boot){          // reels an inbox Add filled in since we last looked: reload them
  const L = Array.isArray(list) ? list : [], ids = new Set();
  if (changedSeq === null) return ids;     // nothing loaded yet: the first list sets the starting point
  if (boot && boot !== bootId) { bootId = boot; changedSeq = 0; }   // the app was restarted: its count starts again
  L.forEach(c => { if (c.seq > changedSeq) ids.add(c.folder); });
  changedSeq = L.reduce((m, c) => Math.max(m, c.seq || 0), changedSeq);
  return ids;
}
function ensurePoll(){ if (!polling) poll(); }
async function poll(){
  polling = true;
  const ep = busyEpoch;
  let s;
  try { s = await api("/api/status"); }
  catch (e) { setTimeout(poll, 3000); return; }          // server hiccup: keep trying
  const q = s.queue || [];
  $("#logtext").textContent = s.log.join("\n") || "(waiting for output)";
  $("#logtext").scrollTop = 1e9;
  $("#status").textContent = s.running ? `Running · ${s.done || 0} / ${s.total != null ? s.total : s.urls.length} done` : (s.finished ? "Finished" : "Idle");
  showProgress(s); showQueue(q);
  $("#status").classList.toggle("on", s.running);
  $("#logtitle").textContent = s.running ? "Run log · working (first run downloads models)" : "Run log";
  const fresh = takeChanged(s.changed, s.boot);
  const ended = syncBusy(s, q, ep);                       // a re-process that finished, even with more in line
  if (s.running || q.length) {
    wasRunning = true; refreshQuiet().catch(() => {});
    if (s.transcripts && ++trTick % 5 === 0) loadTr().catch(() => {});   // the catalogue fills up as it runs
    if (fresh.size || ended.length) reloadChanged(new Set([...fresh, ...ended]));
    setTimeout(poll, 1000); return;
  }
  polling = false;
  if (wasRunning || fresh.size || ended.length) {
    wasRunning = false;
    const changed = new Set([...lastRunTargets, ...fresh, ...ended]);
    busyIds = new Set(); waitingIds = new Set(); showBusy();
    await reloadChanged(changed);
    lastRunTargets = new Set();
    refreshInbox();
    loadTr().catch(() => {});                              // a transcripts run adds videos; a full Run marks one
  }
}
async function reloadChanged(ids){       // reels a run changed: take the server's copy (others keep your edits)
  const redone = [...ids].filter(id => lastRunTargets.has(id));
  redone.forEach(id => lastRunTargets.delete(id));
  try { await loadReels(current && current.id, ids); } catch (e) {}
  if (current && redone.includes(current.id)) toast("Re-process finished");
}
function syncBusy(s, q, ep){
  // Paused reels come from the server: the one being re-processed (or filled in from the inbox) and the
  // Re-processes still in line. Returns the ones that stopped being paused. An answer to a request sent before
  // a Re-process was pressed or taken out of the line (ep is old) is skipped: the next answer decides.
  if (ep !== busyEpoch) return [];
  const wait = new Set(q.filter(x => x.redo && x.target).map(x => x.target));
  const now = new Set([...(s.running ? s.busy || [] : []), ...wait]);
  const ended = [...busyIds].filter(id => !now.has(id));
  busyIds = now; waitingIds = wait; showBusy();
  return ended;
}
let lastQueueHtml = null;
function showQueue(q){                    // the Waiting list under the progress bar
  $("#queue").hidden = !q.length;
  $("#qhead").textContent = q.length ? `Waiting · ${q.length}` : "";
  const html = q.map((x, i) => `<li><span class="qn">${i + 1}.</span>${x.platform ? pbadge({platform: x.platform}) : ""}`
    + `<span class="ql" title="${esc(x.label)}">${esc(x.label)}</span>`
    + `<button class="ico" data-qrm="${x.seq}" data-qkind="${esc(x.kind)}" data-qtarget="${esc(x.redo ? x.target : "")}" aria-label="Take it out of the line" `
    + `title="${x.kind === "add" ? "Take it out of the line (the share stays in your Inbox)" : "Take it out of the line"}">&times;</button></li>`).join("");
  if (html !== lastQueueHtml) { $("#qlist").innerHTML = html; lastQueueHtml = html; }   // unchanged: leave hover and focus alone
}
async function removeQueued(el){
  const target = el.dataset.qtarget, kind = el.dataset.qkind;
  let j;
  try { j = await api("/api/queue/remove", {seq: +el.dataset.qrm}); }
  catch (e) { toast(e.message); return ensurePoll(); }
  if (target) { busyEpoch++; busyIds.delete(target); waitingIds.delete(target); lastRunTargets.delete(target); showBusy(); }
  const back = (j.urls || []).filter(u => !$("#urls").value.includes(u));
  if (back.length) $("#urls").value = [$("#urls").value.trim(), ...back].filter(Boolean).join("\n");
  toast(kind === "add" ? "Taken out of the line: the share is still in your Inbox"
        : back.length ? `Taken out of the line: the link${back.length > 1 ? "s are" : " is"} back in the box above` : "Taken out of the line");
  lastQueueHtml = null; ensurePoll();
  if (kind === "add") refreshInbox(true);
}
async function refreshQuiet(){
  const j = await api("/api/reels");
  if (j.reels.length !== reels.length) {
    const known = new Map(reels.map(r => [r.id, r]));
    reels = j.reels.map(r => known.get(r.id) || r);
    $("#count").textContent = `${reels.length} reels`; renderList();
    if (!current && reels[0]) select(reels[0].id, true, true);
  }
}

setGroupOptions("reels"); $("#sortby").value = sortBy;
try { if (innerWidth <= 900 && localStorage.getItem("rh-panes") === null) panes.fp = false; } catch (e) {}   // small screens start with the filters folded
applyPanes();
if (!$("#sortby").value) { sortBy = "posted-new"; $("#sortby").value = sortBy; }
try { const m = localStorage.getItem("rh-maxshots"); if (m && [...$("#maxshots").options].some(o => o.value === m)) $("#maxshots").value = m;
  const e = parseFloat(localStorage.getItem("rh-everyn")); if (e >= 0.5 && e <= 600) $("#everyn").value = e; } catch (e) {}
$("#everyn").hidden = $("#maxshots").value !== "every";
$("#maxshots").addEventListener("change", () => { $("#everyn").hidden = $("#maxshots").value !== "every"; if (!$("#everyn").hidden) $("#everyn").focus(); saveShotsChoice(); });
$("#everyn").addEventListener("change", () => { shotsValue(); saveShotsChoice(); });
let me = {name: "", person: "", can_delete: true, share: {on: false, people: [], as: ""}}, setupFirst = false;
async function openSetup(first){
  const c = await api("/api/config");
  setupFirst = first;
  const cfg = c.configured ? c.config : c.defaults;
  $("#sttitle").textContent = first ? "Welcome to Reels Hub" : "Settings";
  $("#stintro").textContent = first ? "Tell the app who you are and where to keep things. You can change this later under Settings."
                                    : "Your name and folders on this computer.";
  $("#stsortrow").hidden = !cfg.name || !(cfg.vault || "").trim();
  $("#stname").value = cfg.name || ""; $("#stout").value = cfg.out || ""; $("#stvault").value = cfg.vault || "";
  $("#stlang").value = cfg.sub_lang !== undefined ? cfg.sub_lang : "en";
  const cl = (c.configured && c.config.cloud) || {};
  $("#stcurl").value = cl.url || ""; $("#stcid").value = cl.id || ""; $("#stcsec").value = "";
  $("#stcsec").placeholder = cl.has_secret ? "Saved - leave empty to keep it" : "";
  $("#sttestres").className = "meta";
  $("#sttestres").textContent = cl.name ? `This laptop is ${cl.name} in the inbox` + (cl.checked ? ` (checked ${cl.checked})` : "") : "";
  $("#stcancel").hidden = first; $("#stcancel").textContent = "Cancel"; $("#stsave").textContent = first ? "Save and start" : "Save";
  $("#sterr").hidden = true; $("#sterr").className = "sterr";
  $("#stpath").textContent = "Saved on this computer in " + c.path + (c.overrides.length ? " · started with --" + c.overrides.join(" --") + ", which overrides these" : "");
  $("#setup").classList.add("show");
  setTimeout(() => (first && !cfg.name ? $("#stname") : $("#stsave")).focus(), 0);
}
async function saveSetup(){
  const b = $("#stsave"); b.disabled = true;
  try {
    const j = await api("/api/config", {name: $("#stname").value, out: $("#stout").value, vault: $("#stvault").value, sub_lang: $("#stlang").value,
      cloud_url: $("#stcurl").value, cloud_id: $("#stcid").value, cloud_secret: $("#stcsec").value});
    if (j.warning) { $("#sterr").textContent = j.warning; $("#sterr").className = "sterr stwarn"; $("#sterr").hidden = false; }
    const bad = j.cloud_test && !j.cloud_test.ok;
    if (bad) {                                     // saved, but the token or address needs a look
      $("#sterr").textContent = "Saved. But the Cloudflare inbox could not be reached: " + j.cloud_test.error;
      $("#sterr").className = "sterr"; $("#sterr").hidden = false;
      $("#stcsec").value = ""; $("#stcsec").placeholder = "Saved - leave empty to keep it";
      setupFirst = false; $("#stcancel").hidden = false; $("#stcancel").textContent = "Close";
    } else $("#setup").classList.remove("show");
    current = null; reels = [];
    await loadReels(); setView(view); refreshInbox();
    if (!bad) toast(setupFirst ? `Welcome, ${j.config.name}!` : "Settings saved");
  } catch (e) { $("#sterr").textContent = e.message; $("#sterr").className = "sterr"; $("#sterr").hidden = false; }
  finally { b.disabled = false; }
}
(async () => {
  pingSoon(5000);                            // status at the top right: is the server on this PC answering?
  try { const c = await api("/api/config"); if (!c.configured) await openSetup(true); } catch (e) {}
  await loadReels(); setView(view); poll();
  try { await loadTr(); } catch (e) {}
  let m = ""; try { m = localStorage.getItem("rh-mode") || ""; } catch (e) {}
  if (m === "transcripts" && tr.items.length) setMode("transcripts");
  if (me.share && me.share.on) await api("/api/inbox/check", {}).catch(() => {});   // opening the app looks for shares
  refreshInbox(true);
})();
</script>
</body>
</html>
"""


def main():
    global PORT
    ap = argparse.ArgumentParser(description="Local web app for reels2notes.py")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--out", default=None, help="reels folder (overrides Settings)")
    ap.add_argument("--vault", default=None, help="Obsidian vault folder (overrides Settings; '' turns Obsidian off)")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    PORT = args.port
    if args.out is not None:
        OVERRIDES["out"] = args.out
    if args.vault is not None:
        OVERRIDES["vault"] = args.vault
    cfg = load_config()
    apply_config(cfg or default_config())
    CONFIG.clear(); CONFIG.update(cfg)          # empty until the setup screen has been filled in
    if not cfg:
        print(f"First start: the app will ask who you are and where your folders are (saved in {config_path()}).")
    elif not vault_ok():
        print(f"No Obsidian vault set (or not found) - Obsidian notes are off. Change it under Settings.")
    if not SCRIPT.exists():
        print(f"reels2notes.py not found next to this file ({HERE}) - Run will not work.")
    url = f"http://127.0.0.1:{args.port}"
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        print(f"Port {args.port} is busy - Reels Hub is probably already running. Opening it.")
        webbrowser.open(url)
        return
    print(f"Reels Hub running at {url}  (reels folder: {OUT})\nPress Ctrl+C to stop.")
    if cloud_settings():
        print(f"Sharing: inbox at {CONFIG['cloud']['url']} (checked every {PICKUP_EVERY} s while this runs)")
    threading.Thread(target=inbox_worker, daemon=True).start()
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
