#!/usr/bin/env python3
"""
reels_hub.py - local web app for reels2notes.py

Keep this file next to reels2notes.py (e.g. in GitHub/reels-hub) and run:
    python reels_hub.py
Downloaded reels are stored in Downloads/reels (change with --out).
It opens http://127.0.0.1:8765 in your browser.

- Paste Instagram, TikTok, YouTube (incl. Shorts) or X links, one per line, and press Run: reels2notes.py
  processes them, with a live log
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
(caption, transcript); "Send to Obsidian" copies starred images to 910_Attachments/ReelsHub and
writes them with their text. Notes can be moved or renamed in the vault; the app finds them by their id line.

Options:  --port 8765   --out PATH   --vault PATH   --no-browser
Uses only the Python standard library. Listens on 127.0.0.1 only (not reachable from other devices).
"""
import argparse, json, mimetypes, os, re, subprocess, sys, threading, time, webbrowser
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
    if JOB["running"]:
        raise ValueError("Wait for the current run to finish")
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
    cfg = {"name": name, "person": person_id(name), "out": str(out_p.resolve()), "vault": vault,
           "saved": time.strftime("%Y-%m-%d %H:%M")}
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps(cfg, indent=1, ensure_ascii=False))
    apply_config(cfg)
    MISS.clear()
    return {"config": cfg, "warning": warn}


def config_info():
    return {"configured": bool(CONFIG.get("name")), "config": CONFIG, "defaults": default_config(),
            "path": str(config_path()), "effective": {"out": str(OUT), "vault": str(VAULT) if vault_ok() else ""},
            "overrides": sorted(OVERRIDES)}
PLATFORM_DIRS = ("Instagram", "TikTok", "YouTube", "X")             # one vault folder per platform
FAV_START, FAV_END = "<!-- reels-hub:favorites start -->", "<!-- reels-hub:favorites end -->"

JOB = {"running": False, "log": [], "started": None, "finished": None, "urls": [], "progress": None, "busy": set()}
JOB_LOCK = threading.Lock()
DATA_LOCK = threading.RLock()            # re-entrant: helpers that already hold it can call each other
PORT = 8765
NOTES_S, NOTES_E = "<!-- reels-hub:notes start -->", "<!-- reels-hub:notes end -->"
LFAV_S, LFAV_E = "<!-- reels-hub:local-favorites start -->", "<!-- reels-hub:local-favorites end -->"


def busy_check(rid):
    """Refuse changes to a reel while a run is replacing its transcript and snapshots."""
    if JOB["running"] and rid in JOB.get("busy", ()):
        raise ValueError("This video is being re-processed - wait until the run finishes")


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
        "transcript": transcript, "ocr": ocr, "notes": notes,
        "times": times,
        "manual": manual_shots(folder),
        "mt": {s: int((folder / "shots" / s).stat().st_mtime) for s in shots},
        "edited": sorted(p.name for p in (folder / "shots" / "_orig").glob("*.jpg")) if (folder / "shots" / "_orig").exists() else [],
        "video": videos[0] if videos else None,
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
    vids = [p for p in folder.glob("video.*") if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov", ".m4v")]
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
           "note_name": s.get("note_name", "") or "", "note_path": s.get("note_path", "") or "", "sent": s.get("sent", "") or ""}
    if bad:
        out["_bad"] = True
    return out


def write_state(folder, state):
    state = {k: v for k, v in state.items() if not k.startswith("_")}
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
        dname = vault_dir(r["platform"])
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


def vault_note_text(folder, r, state):
    info = _info(folder)
    plat = r["platform"]
    creator_url = info.get("uploader_url") or info.get("channel_url") or ""
    tag_cat = ("#" + re.sub(r"[^0-9A-Za-z]+", "", state["category"].title())) if state["category"] else ""
    full_text = " ".join(x["text"] for x in r["transcript"])
    lines = [time.strftime("%Y-%m-%d"), time.strftime("%H:%M"),
             f"**Title:** {r['title'] or r['label']}",
             f"**Source**: {r['url']}",
             f"**Tags**: #{plat} {tag_cat}".rstrip(),
             f"**Related**: {NOTE_RELATED.get(plat, f'[[{plat}_MOC]]')}",
             f"**Creator:** {r['author']}" + (f" - {creator_url}" if creator_url else ""),
             f"**Posted:** {r['date']}" + (f" · {_dur(r['duration'])}" if _dur(r["duration"]) else ""),
             f"**Category**: {state['category'] or '-'}",
             _marker(r["id"]),
             "",
             "Formula to remove timestamp: =RIGHT(A1,LEN(A1)-13)", ""]
    for sec in AI_SECTIONS:
        lines += [f"## {sec}", "", AI_TODO, "", "---", ""]
    lines += ["## Favorites (Reels Hub)", FAV_START,
              '_Star snapshots in Reels Hub and press "Send to Obsidian"._', FAV_END, "",
              "## My notes", "", r["notes"] or "", "",
              "# **Description**:", "", r["caption"] or "_none_", ""]
    if r["ocr"]:
        lines += ["## On-screen text", ""] + [f"- **{mmss(r['times'].get(x['shot'], 0))}** {x['text']}" for x in r["ocr"]] + [""]
    lines += ["# **Content**:", ""]
    lines += [p + "\n" for p in _paragraphs(full_text)] or ["_No speech detected._", ""]
    lines += ["# **Original text:**", ""]
    lines += [f"{mmss(x['t'])} {x['text']}" for x in r["transcript"]] or ["_No speech detected._"]
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
    if "error" in info:
        raise ValueError(info["error"])
    state = read_state(folder)
    favs = state["favorites"]
    att_rel = f"{ATTACH_DIR}/{reel_id}"
    att = VAULT / att_rel
    att.mkdir(parents=True, exist_ok=True)
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
                  f"**{i}. [{mmss(r['times'].get(f['shot'], 0))}]({_at(r['url'], r['times'].get(f['shot'], 0))})** {f['text']}"]
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
    folder = reel_dir(reel_id)
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
    return head + prompt.strip() + "\n\n---\nTranscript:\n\n" + " ".join(x["text"] for x in r["transcript"]) + \
        ("\n\nDescription:\n" + r["caption"] if r["caption"] else "")


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


# ---------------------------------------------------------------- saving
def save_notes(reel_id, notes):
    folder = reel_dir(reel_id)
    busy_check(folder.name)
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
        old = read_state(folder)                     # keep server-owned fields (vault note name, last send)
        clean = {"category": str(state.get("category", ""))[:100], "favorites": favs,
                 "note_name": old["note_name"], "note_path": old["note_path"], "sent": old["sent"]}
        write_state(folder, clean)
        write_note_favorites(folder, favs)
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
        lines = ["", "## Favorites"]
        for f in favs:
            lines.append(f"![[{folder.name}/shots/{f['shot']}]]")
            mark = " _(corrected text)_" if f["corrected"] else ""
            lines.append(f"**{mmss(times.get(f['shot'], 0))}** {f['text']}{mark}")
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


def write_cats(cats):
    atomic_write(cats_file(), json.dumps({"categories": cats}, indent=1, ensure_ascii=False))


def recategorize(old, new):
    for folder in OUT.iterdir():
        f = folder / "hub.json"
        if folder.is_dir() and f.exists():
            s = read_state(folder)
            if s["category"] == old:
                s["category"] = new
                write_state(folder, s)


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
    import shutil
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


def run_job(urls, max_shots="auto", redo=False, flags=(), target=None):
    # JOB["running"] was already claimed under JOB_LOCK by the request handler
    JOB.update(log=[], started=time.time(), finished=None, urls=urls, progress=None, busy={target} if target else set())
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    cmd = [sys.executable, "-u", str(SCRIPT), "--out", str(OUT), "--max-shots", str(max_shots)]
    if CONFIG.get("person"):
        cmd += ["--owner", CONFIG["person"]]
    if redo:
        cmd.append("--redo")
    cmd += list(flags)
    cookies = HERE / "cookies.txt"          # optional: login cookies next to the app (git-ignored)
    if cookies.exists():
        cmd += ["--cookies", str(cookies)]
        JOB["log"].append("-- using cookies.txt for sites that need login")
    cmd += urls
    try:
        proc = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", env=env)
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("@@PROGRESS "):
                try:
                    JOB["progress"] = {**json.loads(line[11:]), "at": time.time()}
                except ValueError:
                    pass
                continue
            if line and "warnings.warn" not in line:
                JOB["log"].append(line)
        proc.wait()
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
    JOB.update(running=False, finished=time.time(), progress=None, busy=set())


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
        elif path == "/api/config":
            self.send_json(config_info())
        elif path == "/api/reels":
            self.send_json({"reels": list_reels() if CONFIG.get("name") else [], "categories": read_cats(),
                            "me": {"name": CONFIG.get("name", ""), "person": CONFIG.get("person", "")},
                            "vault": {"path": str(VAULT), "ok": vault_ok()}})
        elif path == "/api/status":
            prog = JOB["progress"]
            if prog:   # age the ETA by the time since the last update
                prog = {**prog, "eta": None if prog.get("eta") is None else max(0, prog["eta"] - (time.time() - prog["at"])),
                        "elapsed": prog["elapsed"] + (time.time() - prog["at"])}
            self.send_json({"running": JOB["running"], "log": JOB["log"][-300:], "urls": JOB["urls"],
                            "finished": JOB["finished"], "progress": prog,
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
            if path == "/api/run":
                if JOB["running"]:
                    return self.send_json({"error": "A run is already in progress"}, 409)
                urls = [u.strip() for u in re.split(r"[\s,]+", str(body.get("urls") or "")) if u.strip().startswith("http")]
                urls = list(dict.fromkeys(urls))
                if not urls and not body.get("target"):
                    return self.send_json({"error": "No links found"}, 400)
                ms = str(body.get("max_shots") or "auto").lower()
                try:
                    max_shots = "auto" if ms == "auto" else str(max(10, min(1000, int(ms))))
                except ValueError:
                    max_shots = "auto"
                flags, redo = [], False
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
                    else:
                        flags.append("--new-version")
                else:
                    mode = body.get("existing")              # what to do with links that are already done
                    if mode is None:
                        done = [(u, f) for u in urls for f in find_done(u)[:1]]
                        if done:
                            return self.send_json({"existing": [
                                {"url": u, "id": f.name, "label": (parse_note(f) or {}).get("label", f.name)} for u, f in done],
                                "total": len(urls)})
                    elif mode == "new":
                        flags.append("--new-version")
                with JOB_LOCK:                               # claim the single run slot atomically
                    if JOB["running"]:
                        return self.send_json({"error": "A run is already in progress"}, 409)
                    JOB["running"] = True
                    JOB["busy"] = {target} if (target and redo) else set()
                threading.Thread(target=run_job, args=(urls, max_shots, redo, flags, target if redo else None), daemon=True).start()
                self.send_json({"ok": True, "count": len(urls)})
            elif path == "/api/config":
                self.send_json({"ok": True, **save_config(body), **config_info()})
            elif path == "/api/notes":
                save_notes(body["id"], body.get("notes", ""))
                self.send_json({"ok": True})
            elif path == "/api/state":
                self.send_json({"ok": True, "state": save_state(body["id"], body.get("state", {}))})
            elif path == "/api/obsidian":
                self.send_json({"ok": True, **send_to_obsidian(body["id"])})
            elif path == "/api/aiprompt":
                self.send_json({"ok": True, "text": ai_prompt(body["id"])})
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
                self.send_json({"ok": True, "categories": change_cats(body)})
            else:
                self.send_error(404)
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
  --accent:#5fd0c4; --accent-ink:#06201d; --warn:#f0b35a; --star:#f5c451; --radius:10px;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--text);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;display:flex;flex-direction:column}
button,textarea,input,select{font:inherit;color:inherit}
[hidden]{display:none!important}
header{display:flex;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--panel)}
header h1{font-size:16px;margin:0;font-weight:600;letter-spacing:.2px;cursor:pointer}
header h1:hover{color:var(--accent)}
header .count{color:var(--muted);font-size:13px}
header .spacer{flex:1}
.btn{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:6px 12px;cursor:pointer}
.btn:hover{border-color:var(--accent)}
.btn.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);font-weight:600}
.btn.small{padding:3px 9px;font-size:12px}
.btn:disabled{opacity:.5;cursor:default}
.ico{background:none;border:0;color:var(--muted);cursor:pointer;padding:2px 5px;border-radius:5px;font-size:13px;line-height:1}
.ico:hover{color:var(--text);background:var(--panel2)}
.link{background:none;border:0;color:var(--accent);cursor:pointer;padding:0;font-size:12px;text-decoration:underline}
.layout{flex:1;display:grid;grid-template-columns:clamp(240px,var(--side-w,310px),100% - 560px) 0 minmax(0,1fr);min-height:0}
aside{border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0;background:var(--panel)}
/* drag handles: list | video column | text, and the bar under the video (sizes in --side-w, --player-w, --video-h) */
.split{position:relative;z-index:3;align-self:stretch;cursor:col-resize;touch-action:none;outline:none}
.split::before{content:"";position:absolute;top:0;bottom:0;left:50%;width:2px;margin-left:-1px;transition:background .15s}
.split::after{content:"";display:block;width:4px;height:40px;border-radius:2px;background:var(--line);transition:background .15s}
.split[data-split="side"]{margin:0 -6px 0 -2px}
.split[data-split="side"]::before{left:1px;margin-left:0}
.split[data-split="side"]::after{position:absolute;top:50%;left:0;margin-top:-20px}
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
.listtools{display:flex;gap:8px;padding:10px 12px 6px;align-items:center}
.sortbar{display:flex;gap:10px;padding:0 12px 8px}
.sortbar label{display:flex;gap:5px;align-items:center;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;flex:1;min-width:0}
.sortbar select{flex:1;min-width:0;background:var(--panel2);border:1px solid var(--line);border-radius:7px;padding:3px 5px;font-size:12px;text-transform:none;letter-spacing:0;color:var(--text)}
#filters{padding:0 12px 6px;display:flex;flex-direction:column;gap:5px;border-bottom:1px solid var(--line)}
#filters:empty{display:none}
.frow{display:flex;flex-wrap:wrap;gap:5px;align-items:center}
.fl{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;width:100%}
.chip{display:inline-flex;align-items:center;gap:5px;font-size:12px;padding:2px 9px;border-radius:14px;border:1px solid var(--line);background:var(--panel2);cursor:pointer;color:var(--muted)}
.chip:hover{border-color:var(--accent)}
.chip.on{background:#1f2c2b;border-color:var(--accent);color:var(--text)}
.chip .cn{font-size:10.5px;opacity:.7}
#search{flex:1;min-width:0;padding:6px 10px;background:var(--bg);border:1px solid var(--line);border-radius:8px}
#list{overflow:auto;flex:1;padding:0 6px 12px}
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
#toast{position:fixed;left:50%;top:70px;transform:translateX(-50%);background:var(--accent);color:var(--accent-ink);font-weight:600;padding:7px 14px;border-radius:10px;opacity:0;pointer-events:none;transition:opacity .2s;z-index:40}
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
@media (max-width:900px){
  .layout{grid-template-columns:1fr}
  .split{display:none}
  aside{max-height:45vh;border-right:0;border-bottom:1px solid var(--line)}
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
</style>
</head>
<body>
<header>
  <h1 id="home" title="Home: back to the start">Reels Hub</h1><span class="count" id="count"></span>
  <span class="who" id="who" title="Whose Reels Hub this is"></span>
  <span class="spacer"></span>
  <span class="status"><kbd>&larr;</kbd> <kbd>&rarr;</kbd> switch reels</span>
  <button class="btn" id="viewbtn" title="Show all reels as a gallery">&#9638; Gallery</button>
  <button class="btn" id="setbtn" title="Your name and folders">&#9881; Settings</button>
  <button class="btn" id="showlog">Log</button>
</header>
<div class="layout">
  <aside>
    <div class="runbox">
      <textarea id="urls" placeholder="Paste Instagram, TikTok, YouTube or X links, one per line"></textarea>
      <div class="row">
        <button class="btn primary" id="run">Run</button>
        <select id="maxshots" title="Auto: works out how many snapshots from the video's length and how fast it is edited. Or pick a maximum.">
          <option value="auto" selected>Auto shots</option>
          <option value="60">Max 60</option><option value="120">Max 120</option>
          <option value="200">Max 200</option><option value="400">Max 400</option></select>
        <span class="status" id="status">Idle</span>
      </div>
      <div id="prog" hidden>
        <div class="pline"><span id="pstage"></span><span id="peta"></span></div>
        <div class="pbar"><div id="pfill"></div></div>
        <div class="pline sub"><span id="pvid"></span><span id="pel"></span></div>
      </div>
    </div>
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
    <div id="list"></div>
  </aside>
  <div class="split" data-split="side" role="separator" aria-orientation="vertical" aria-label="Width of the list" tabindex="0" title="Drag to change the width · double-click for the standard width"></div>
  <main id="main"><div class="empty">No reels yet. Paste links on the left and press Run.</div></main>
</div>

<div id="bulkbar">
  <span id="bulkcount"></span>
  <select id="bulkcat"></select>
  <button class="btn small" id="bulkall">Select all shown</button>
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
  <label class="fld">Your name<input id="stname" autocomplete="off" placeholder="e.g. Stela"></label>
  <label class="fld">Reels folder <span class="meta">- videos, snapshots and notes are saved here</span>
    <input id="stout" autocomplete="off" spellcheck="false"></label>
  <label class="fld">Obsidian vault <span class="meta">- optional; leave empty if you don't use Obsidian</span>
    <input id="stvault" autocomplete="off" spellcheck="false" placeholder="C:\Users\you\Documents\Obsidian Vault"></label>
  <p class="meta sthelp">Tip: in File Explorer, open the folder, click the address bar and copy the path.</p>
  <p class="sterr" id="sterr" hidden></p>
  <div class="cbtns"><button class="btn" id="stcancel" hidden>Cancel</button><button class="btn primary" id="stsave">Save and start</button></div>
  <p class="meta stpath" id="stpath"></p>
</div></div>
<div id="choice" class="modal"><div class="cbox card">
  <h3 id="chtitle"></h3>
  <ul id="chlist" class="chlist"></ul>
  <p>Run them again as a <b>new version</b>? The current one and its favorites stay. The downloaded video is reused.</p>
  <p id="chnote" class="meta"></p>
  <div class="cbtns"><button class="btn" id="chcancel">Cancel</button><button class="btn" id="chopen">Open it</button>
    <button class="btn" id="chskip">Skip</button><button class="btn primary" id="chnew">New version</button></div>
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
try { view = localStorage.getItem("rh-view") || "detail"; } catch (e) {}
try { collapsed = new Set(JSON.parse(localStorage.getItem("rh-collapsed") || "[]")); } catch (e) {}
const shotUrl = (r, shot) => media(r.id, "shots/" + shot) + (r.mt && r.mt[shot] ? `?v=${r.mt[shot]}` : "");
const media = (id, f) => `/media/${encodeURIComponent(id)}/${f.split("/").map(encodeURIComponent).join("/")}`;
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const mmss = t => { t = Math.round(t || 0); return `${String(Math.floor(t/60)).padStart(2,"0")}:${String(t%60).padStart(2,"0")}`; };
const PB = {Instagram: ["IG", "#e1306c"], TikTok: ["TT", "#25f4ee"], YouTube: ["YT", "#ff4e45"], X: ["X", "#e7e9ea"]};
const pbadge = r => { const [t, c] = PB[r.platform] || [String(r.platform||"").slice(0,3), "#8b949b"]; return `<span class="pb" style="--pc:${c}" title="${esc(r.platform)}">${esc(t)}</span>`; };
const vtag = r => r.version > 1 ? ` <span class="vtag" title="Version ${r.version}">v${r.version}</span>` : "";
const catOf = r => cats.includes(r.state.category) ? r.state.category : "";
const isFav = (r, shot) => r.state.favorites.some(f => f.shot === shot);

async function api(path, body){
  const res = await fetch(path, body ? {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)} : {});
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
  cats = j.categories; vault = j.vault || vault; me = j.me || me;
  $("#who").textContent = me.name || "";
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
let busyIds = new Set();                  // reels being re-processed: edits are paused
function isBusy(r, quiet){
  if (!r || !busyIds.has(r.id)) return false;
  if (!quiet) toast("This video is being re-processed - changes are paused until it finishes");
  return true;
}
function saveState(r, now){
  if (isBusy(r)) return;
  clearTimeout(saveTimers[r.id]);
  const go = () => { delete pending[r.id]; return api("/api/state", {id: r.id, state: r.state}).catch(e => alert("Could not save: " + e.message)); };
  pending[r.id] = {url: "/api/state", body: () => ({id: r.id, state: r.state}), go};
  if (now) go(); else saveTimers[r.id] = setTimeout(go, 500);
}
const notesTimers = {};
function notesChanged(r, text){
  if (isBusy(r)) return;
  r.notesDraft = text;
  clearTimeout(notesTimers[r.id]);
  const go = () => { delete pending["n:" + r.id];
    return api("/api/notes", {id: r.id, notes: text}).then(() => { r.notes = text; delete r.notesDraft; const sv = $("#saved"); if (sv && current === r) { sv.textContent = "Saved"; sv.hidden = false; } })
      .catch(e => alert("Could not save notes: " + e.message)); };
  pending["n:" + r.id] = {url: "/api/notes", body: () => ({id: r.id, notes: text}), go};
  notesTimers[r.id] = setTimeout(go, 800);
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
let fPlat = new Set(), fCat = new Set();
try { const f = JSON.parse(localStorage.getItem("rh-filters") || "{}"); fPlat = new Set(f.p || []); fCat = new Set(f.c || []); } catch (e) {}
function saveFilters(){ try { localStorage.setItem("rh-filters", JSON.stringify({p: [...fPlat], c: [...fCat]})); } catch (e) {} }
const catShown = name => !fCat.size || fCat.has(name);
function renderFilters(){
  const plats = [...new Set(reels.map(r => r.platform))].sort();
  [...fPlat].forEach(p => plats.includes(p) || fPlat.delete(p));
  [...fCat].forEach(c => (c === "" || cats.includes(c)) || fCat.delete(c));
  const count = (pred) => reels.filter(pred).length;
  const chip = (attr, val, label, on, n, extra="") => `<button class="chip ${on?"on":""}" ${attr}="${esc(val)}" ${extra}>${label}<span class="cn">${n}</span></button>`;
  const active = fPlat.size + fCat.size;
  $("#filters").innerHTML = (plats.length > 1 || fPlat.size ? `<div class="frow"><span class="fl">Source</span>${plats.map(p =>
      chip("data-fplat", p, pbadge({platform: p}) + " " + esc(p), fPlat.has(p), count(r => r.platform === p))).join("")}</div>` : "")
    + `<div class="frow"><span class="fl">Category</span>${[...cats, ""].map(c =>
      chip("data-fcat", c, c ? esc(c) : "Unassigned", fCat.has(c), count(r => catOf(r) === c))).join("")}</div>`
    + (active ? `<div class="frow"><button class="link" id="fclear">Clear filters (${active})</button></div>` : "");
}

function matches(r, q){
  if (fPlat.size && !fPlat.has(r.platform)) return false;
  if (fCat.size && !fCat.has(catOf(r))) return false;
  if (!q) return true;
  return [r.label, r.platform, r.caption, r.author, r.notes, ...r.transcript.map(x=>x.text), ...r.ocr.map(x=>x.text),
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
  return [...cats.map(c => ({key: c, label: c, cat: c, kind: "cat", test: r => catOf(r) === c})),
          {key: "", label: "Unassigned", cat: "", kind: "cat", test: r => catOf(r) === ""}];
}
function renderList(){
  const q = $("#search").value.trim().toLowerCase();
  const groups = groupsFor();
  filtered = []; shown = [];
  let html = "";
  renderFilters();
  groups.forEach((g, gi) => {
    if (g.kind === "cat" && !catShown(g.cat)) return;
    const items = sortReels(reels.filter(r => g.test(r) && matches(r, q)));
    filtered.push(...items);
    if (!collapsed.has(g.label)) shown.push(...items);
    if (g.kind === "cat" && !g.cat && !items.length && cats.length && (q || fPlat.size)) return;
    if (g.kind === "source" && !items.length) return;
    const closed = collapsed.has(g.label);
    const name = g.cat || "";
    html += `<div class="group ${closed?"closed":""}" ${g.kind === "cat" ? `data-cat="${esc(g.cat)}"` : ""} data-label="${esc(g.label)}">
      <span class="gname" data-toggle><span class="caret">&#9662;</span>${g.kind === "source" ? pbadge({platform: g.label}) + " " : ""}${esc(g.label)}</span>
      <span class="gcount">${items.length}</span>
      ${name ? `<button class="ico" data-catmove="-1" title="Move up" ${gi===0?"disabled":""}>&#8593;</button>
                  <button class="ico" data-catmove="1" title="Move down" ${gi===cats.length-1?"disabled":""}>&#8595;</button>
                  <button class="ico" data-rename title="Rename">&#9998;</button>
                  <button class="ico" data-delcat title="Delete category">&#10005;</button>` : ""}
    </div>`;
    if (!closed) html += items.length ? items.map(r => `
      <div class="item ${current && current.id===r.id?"active":""} ${selected.has(r.id)?"sel":""}" data-id="${esc(r.id)}" draggable="true">
        ${r.shots[0] ? `<img loading="lazy" src="${shotUrl(r, r.shots[0])}" alt="">` : `<img alt="">`}
        <div><div class="t">${esc(r.label)}</div>
        <div class="s">${pbadge(r)}${vtag(r)} ${esc(r.author)} · ${esc(r.date)}${r.state.favorites.length?` · <span class="fc">&#9733; ${r.state.favorites.length}</span>`:""}</div></div>
      </div>`).join("") : `<div class="gempty">${name ? "Drag reels here or pick this category on a reel" : "Nothing here"}</div>`;
  });
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
  v.play().catch(() => {});
  v.scrollIntoView({block: "nearest", behavior: "smooth"});
  v.classList.remove("seekflash"); void v.offsetWidth; v.classList.add("seekflash");
}

function goHome(){
  if (ed) { if (ed.dirty && !confirm("Discard your image edits?")) return; closeEditor(); }
  $("#lightbox").classList.remove("show"); $("#printsheet").classList.remove("show"); $("#log").classList.remove("show");
  if (favFull) toggleFull(false);
  clearSel();
  view = "detail"; try { localStorage.setItem("rh-view", view); } catch (e) {} $("#viewbtn").classList.remove("on");
  history.replaceState(null, "", location.pathname);
  $("#list").scrollTop = 0;
  if (filtered[0]) select(filtered[0].id); else $("#main").innerHTML = `<div class="empty">No reels yet. Paste links on the left and press Run.</div>`;
  $("#main").scrollTop = 0;
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
async function reprocess(r){
  if (!r.url) return alert("This reel has no source link, so it can't be re-processed.");
  const n = $("#maxshots").value, nTxt = n === "auto" ? "Auto snapshots" : `up to ${n} snapshots`;
  const favNote = r.state.favorites.length ? `\n\nIts ${r.state.favorites.length} favorite${r.state.favorites.length===1?"":"s"} will be cleared when it finishes, because the snapshots are replaced (they are kept if the run fails).` : "";
  if (!confirm(`Re-process this video with ${nTxt}?\n\nThe transcript and snapshots are made again from the downloaded video (no new download). Your Obsidian note is not changed.${favNote}`)) return;
  await flushSaves();
  try { await api("/api/run", {target: r.id, action: "redo", max_shots: n}); } catch (e) { return alert(e.message); }
  busyIds = new Set([r.id]); lastRunTargets = new Set([r.id]);
  if (current === r) { $("#main").classList.add("busy"); }
  toast("Re-processing... favorites are cleared only if it succeeds"); $("#log").classList.add("show"); poll();
}
async function newVersion(r){
  const n = $("#maxshots").value, nTxt = n === "auto" ? "Auto snapshots" : `up to ${n} snapshots`;
  if (!confirm(`Make a new version of this video with ${nTxt}?\n\nIt reuses the downloaded file (no new download). This version and its favorites stay as they are.`)) return;
  try { await api("/api/run", {target: r.id, action: "new", max_shots: n}); } catch (e) { return alert(e.message); }
  lastRunTargets = new Set(); $("#log").classList.add("show"); poll();
}
function chooseExisting(list, total){
  return new Promise(res => {
    const box = $("#choice");
    $("#chtitle").textContent = list.length === 1 ? "Already in your library" : `${list.length} of ${total} links are already in your library`;
    $("#chlist").innerHTML = list.slice(0, 6).map(x => `<li>${esc(x.label)}</li>`).join("") + (list.length > 6 ? `<li>and ${list.length - 6} more</li>` : "");
    $("#chnote").textContent = total > list.length ? "The other links run normally either way." : "";
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
  if (v === "gallery") renderGallery(); else if (current) render(current);
  else $("#main").innerHTML = `<div class="empty">Pick a reel on the left.</div>`;
}
function cover(r){ const f = r.state.favorites[0]; return f ? f.shot : r.shots[0]; }
function renderGallery(){
  $("#viewbtn").classList.add("on");
  const q = $("#search").value.trim().toLowerCase();
  const groups = groupsFor();
  let html = "";
  for (const g of groups) {
    if (g.kind === "cat" && !catShown(g.cat)) continue;
    const items = sortReels(reels.filter(r => g.test(r) && matches(r, q)));
    if (g.kind !== "cat" && !items.length) continue;
    html += `<section class="gsec" ${g.kind === "cat" ? `data-cat="${esc(g.cat)}"` : ""}><h3>${g.kind === "source" ? pbadge({platform: g.label}) + " " : ""}${esc(g.label)} <span class="gcount">${items.length}</span></h3>
      <div class="ggrid">${items.length ? items.map(r => `
        <div class="gcard ${current && current.id===r.id?"active":""} ${selected.has(r.id)?"sel":""}" data-open="${esc(r.id)}" tabindex="0" draggable="true">
          <span class="tick">&#10003;</span>
          <div class="gthumb">${cover(r) ? `<img loading="lazy" src="${shotUrl(r, cover(r))}" alt="">` : ""}
            ${r.state.favorites.length ? `<span class="gstar">&#9733; ${r.state.favorites.length}</span>` : ""}
            ${r.duration ? `<span class="gdur">${mmss(+r.duration)}</span>` : ""}</div>
          <div class="gtitle">${esc(r.label)}</div>
          <div class="s">${pbadge(r)}${vtag(r)} ${esc(r.author)} · ${esc(r.date)}</div>
        </div>`).join("") : `<div class="gdrop">Drop reels here</div>`}</div></section>`;
  }
  $("#main").innerHTML = `<div class="gallery"><div class="ghint">Click to open · <kbd>Ctrl</kbd>+click or <kbd>Shift</kbd>+click to select several · ${groupBy === "category" ? "drag onto a category to move" : "group by Category to move reels by dragging"}</div>${html || `<div class="empty">No reels match.</div>`}</div>`;
}

function select(id, scroll=true, keepView=false){
  if (current && current.id !== id) flushSaves();
  const r = reels.find(x => x.id === id); if (!r) return;
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
  try { cats = (await api("/api/categories", body)).categories; }
  catch (e) { return alert(e.message); }
  if (body.action === "rename") reels.forEach(r => { if (r.state.category === body.name) r.state.category = body.new; });
  if (body.action === "rename" && fCat.delete(body.name)) { fCat.add(body.new); saveFilters(); }
  if (body.action === "delete") reels.forEach(r => { if (r.state.category === body.name) r.state.category = ""; });
  renderList();
  if (current && $("#catsel")) { $("#catsel").outerHTML = catSelect(current); updateNav(); }
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
  setTimeout(() => $("#main").classList.toggle("busy", busyIds.has(r.id)), 0);
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
      <label class="catsel">Category ${catSelect(r)}</label>
      <div class="rowbtns"><button class="btn small" id="redo" title="Transcribe and take snapshots again, replacing this version">&#8635; Re-process</button>
        <button class="btn small" id="newver" title="Process the video again as a separate copy; this version and its favorites stay">+ New version</button>
        <button class="btn small danger" id="delone">Delete this reel</button></div>
      <div class="meta">${esc(r.author)} · ${esc(r.date)}${r.duration?` · ${Math.round(r.duration)}s`:""}${r.url?` · <a href="${esc(r.url)}" target="_blank" rel="noopener">Open on ${esc(r.platform)}</a> · <button class="link" id="copylink" title="${esc(r.url)}">Copy link</button>`:""}</div>
      <div class="meta">${r.obsidian && r.obsidian.uri ? `Obsidian note: <a href="${esc(r.obsidian.uri)}" title="${esc(r.obsidian.path || "")}">${esc(r.obsidian.name)}</a>` : (r.obsidian && r.obsidian.error ? esc(r.obsidian.error) : "")}
        ${r.transcript.length ? ` · <button class="link" id="copyai" title="Copies your Obsidian AI prompt with this transcript - paste it into ChatGPT or Claude">Copy AI prompt</button>` : ""}</div>
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
          r.ocr.length ? r.ocr.map(x => `<div class="oline">${r.shots.includes(x.shot) ? `<img src="${shotUrl(r, x.shot)}" data-shot="${esc(x.shot)}" alt="">` : ""}<span><button class="ts tlink" data-seek="${r.times[x.shot]}" title="Play the video from here">${mmss(r.times[x.shot])}</button> ${esc(x.text)}</span></div>`).join("")
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
          <button class="btn small" id="obsbtn" title="${esc(vault.ok ? "Copy starred images and their text into " + vault.path : "Obsidian vault not found: " + vault.path)}" ${vault.ok?"":"disabled"}>Send to Obsidian</button>
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
  const v = $("#vid");
  if (v) v.addEventListener("timeupdate", () => { const ct = $("#captime"); if (ct) ct.textContent = mmss(Math.floor(v.currentTime)); });
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
            <figcaption><button class="tlink" data-seek="${r.times[s]}" title="Play the video from here">${man.has(s) ? "&#128248; " : "&#9654; "}${mmss(r.times[s])}</button></figcaption></figure>`).join("")}</div>`;
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
        <span class="num">${i+1}</span><button class="tb" data-seek="${r.times[f.shot]}" title="Play the video from here">&#9654; ${mmss(r.times[f.shot])}</button>
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
        <div class="pcap">${$("#pnums").checked?`<span class="pnum">${i+1}.</span>`:""}<b>${mmss(r.times[f.shot])}</b>${esc(f.text)}</div>
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
  $("#lbcap").textContent = `${lb.fav ? "★ Favorite " : ""}${lb.i+1} / ${lb.list.length} · ${mmss(current.times[s])}`
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
  player: {v: "--player-w", x: true,  min: 240},
  video:  {v: "--video-h",  x: false, min: 180}
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
  if (name === "side") return {el: $("aside"), max: $(".layout").clientWidth - 560};
  if (name === "player") {
    const d = $(".detail"); if (!d) return null;
    const s = getComputedStyle(d);
    return {el: $(".player"), max: d.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight) - 296};
  }
  if (name === "video") return $("#vid") ? {el: $("#vid"), max: innerHeight - 120} : null;
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
  if ((el = c("[data-star]"))) return toggleFav(current, el.dataset.star);
  if ((el = c("[data-unfav]"))) return toggleFav(current, el.dataset.unfav);
  if ((el = c("[data-open]"))) { if (e.ctrlKey || e.metaKey || e.shiftKey) { e.preventDefault(); return pick(el.dataset.open, e); } clearSel(); return select(el.dataset.open); }
  if ((el = c("[data-fplat]"))) { const v = el.dataset.fplat; fPlat.has(v) ? fPlat.delete(v) : fPlat.add(v); saveFilters(); return renderList(); }
  if ((el = c("[data-fcat]"))) { const v = el.dataset.fcat; fCat.has(v) ? fCat.delete(v) : fCat.add(v); saveFilters(); return renderList(); }
  if (t.id === "fclear") { fPlat.clear(); fCat.clear(); saveFilters(); return renderList(); }
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
    case "setbtn": return openSetup(false);
    case "stcancel": return $("#setup").classList.remove("show");
    case "stsave": return saveSetup();
    case "copyai": { try { const j = await api("/api/aiprompt", {id: current.id}); copyText(j.text, "AI prompt + transcript copied - paste it into ChatGPT or Claude"); } catch (err) { alert(err.message); } return; }
    case "lbplay": return seekTo(current.times[lb.list[lb.i]]);
    case "capbtn": return capture(false);
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
    case "viewbtn": return setView(view === "gallery" ? "detail" : "gallery");
    case "expandbtn": return toggleFull();
    case "closelog": return $("#log").classList.remove("show");
    case "savenotes": {
      clearTimeout(notesTimers[current.id]); delete pending["n:" + current.id];
      if (isBusy(current)) return;
      try { await api("/api/notes", {id: current.id, notes: $("#notes").value}); current.notes = $("#notes").value; delete current.notesDraft; $("#saved").textContent = "Saved into note.md"; $("#saved").hidden = false; }
      catch (err) { alert(err.message); } return;
    }
    case "run": {
      if (runBusy) return;
      runBusy = true; $("#run").disabled = true;
      try {
        try { localStorage.setItem("rh-maxshots", $("#maxshots").value); } catch (e2) {}
        const body = {urls: $("#urls").value, max_shots: $("#maxshots").value};
        let j;
        try { j = await api("/api/run", body); } catch (err) { return alert(err.message); }
        if (j.existing) {
          const pick = await chooseExisting(j.existing, j.total);
          if (!pick) return;
          if (pick === "open") { $("#urls").value = ""; return select(j.existing[0].id); }
          if (pick === "skip" && j.existing.length === j.total) { $("#urls").value = ""; return toast("Nothing to run: all links are already done"); }
          try { await api("/api/run", {...body, existing: pick}); } catch (err) { return alert(err.message); }
        }
        lastRunTargets = new Set();
        $("#urls").value = ""; $("#log").classList.add("show"); poll();
      } finally { runBusy = false; if (!wasRunning) $("#run").disabled = false; }
      return;
    }
  }
});

document.addEventListener("input", e => {
  if (e.target.id === "notes" && current) { $("#saved").hidden = true; return notesChanged(current, e.target.value); }
  if (e.target.id === "search") return renderList();
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
  if (e.target.id === "catsel") setCategory(current, e.target.value);
  if (["pcols","ptitle","pnums"].includes(e.target.id)) renderPaper();
});

/* drag: favorites reorder + reels onto categories */
document.addEventListener("dragstart", e => {
  const h = e.target.closest && e.target.closest(".favimg");
  if (h) { dragFav = +h.closest(".fav").dataset.i; h.closest(".fav").classList.add("dragging"); e.dataTransfer.setData("text/plain", "fav"); e.dataTransfer.effectAllowed = "move"; return; }
  const it = e.target.closest && (e.target.closest(".item") || e.target.closest(".gcard"));
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
  document.querySelectorAll(".drop").forEach(x => x.classList.remove("drop"));
});

document.addEventListener("keydown", e => {
  if (ed) return edKey(e);
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
  if ($("#confirm").classList.contains("show") && e.key !== "Enter" && e.key !== "Escape" && e.key !== "Tab") { e.preventDefault(); return; }
  const sp = document.activeElement && document.activeElement.closest && document.activeElement.closest("[data-split]");
  if (sp && splitKey(e, sp)) return;
  if (e.key === "Enter" && document.activeElement && document.activeElement.classList.contains("gcard")) { e.preventDefault(); return select(document.activeElement.dataset.open); }
  const mod = e.ctrlKey || e.metaKey || e.altKey;
  if ($("#lightbox").classList.contains("show") && !mod && (e.key === "e" || e.key === "E") && !/TEXTAREA|INPUT|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); return openEditor(lb.list[lb.i]); }
  if (/TEXTAREA|INPUT|SELECT/.test(document.activeElement.tagName)) return;
  if ($("#printsheet").classList.contains("show")) { if (e.key === "Escape") $("#printsheet").classList.remove("show"); return; }
  const open = $("#lightbox").classList.contains("show");
  if (e.key === "Escape" && !open && favFull) return toggleFull(false);
  if ($("#confirm").classList.contains("show")) {
    if (e.key === "Escape") { e.preventDefault(); $("#cno").click(); }
    if (e.key === "Enter") { e.preventDefault(); $("#cyes").click(); }
    return;
  }
  if (e.key === "Escape" && !open && selected.size) return clearSel();
  if (!open && view !== "gallery" && (e.key === "s" || e.key === "S") && !e.ctrlKey && !e.metaKey && !e.altKey && $("#vid")) { e.preventDefault(); return capture(e.shiftKey); }
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

/* ---------- progress / ETA ---------- */
const hms = sec => { sec = Math.max(0, Math.round(sec)); const h = Math.floor(sec/3600), m = Math.floor(sec%3600/60), x = sec%60;
  return h ? `${h}:${String(m).padStart(2,"0")}:${String(x).padStart(2,"0")}` : `${m}:${String(x).padStart(2,"0")}`; };
function showProgress(s){
  const p = s.progress, box = $("#prog");
  if (!s.running || !p) { box.hidden = !s.running; if (s.running) { $("#pstage").textContent = "Starting..."; $("#peta").textContent = ""; $("#pfill").style.width = "0%"; $("#pvid").textContent = ""; $("#pel").textContent = ""; } return; }
  box.hidden = false;
  $("#pstage").textContent = `${p.label} ${p.stage_pct ? Math.round(p.stage_pct) + "%" : ""}`;
  $("#peta").textContent = p.eta == null ? "estimating..." : (p.eta_partial ? `download ~${hms(p.eta)} left` : `~${hms(p.eta)} left`);
  $("#pfill").style.width = `${Math.min(100, p.stage_pct || 0)}%`;
  $("#pfill").dataset.stage = p.stage;
  $("#pvid").textContent = p.n > 1 ? `Video ${p.i} of ${p.n}` : "This video";
  $("#pel").textContent = `${hms(p.elapsed)} elapsed`;
}

/* ---------- run polling ---------- */
let lastRunTargets = new Set(), runBusy = false;
async function poll(){
  let s;
  try { s = await api("/api/status"); }
  catch (e) { setTimeout(poll, 3000); return; }          // server hiccup: keep trying
  $("#logtext").textContent = s.log.join("\n") || "(waiting for output)";
  $("#logtext").scrollTop = 1e9;
  $("#run").disabled = s.running;
  const done = s.log.filter(l => /^\s*done( in [^>]*)? ->/.test(l) || l.includes("already done")).length;
  $("#status").textContent = s.running ? `Running · ${done} / ${s.urls.length} done` : (s.finished ? "Finished" : "Idle");
  showProgress(s);
  $("#status").classList.toggle("on", s.running);
  $("#logtitle").textContent = s.running ? "Run log · working (first run downloads models)" : "Run log";
  if (s.running) { wasRunning = true; refreshQuiet().catch(() => {}); setTimeout(poll, 1000); }
  else if (wasRunning) {
    wasRunning = false;
    const changed = lastRunTargets; lastRunTargets = new Set(); busyIds = new Set();
    try { await loadReels(current && current.id, changed); } catch (e) {}
    if (current && changed.has(current.id)) toast("Re-process finished");
  }
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

$("#groupby").value = groupBy; $("#sortby").value = sortBy;
if (!$("#groupby").value) { groupBy = "category"; $("#groupby").value = groupBy; }
if (!$("#sortby").value) { sortBy = "posted-new"; $("#sortby").value = sortBy; }
try { const m = localStorage.getItem("rh-maxshots"); if (m && [...$("#maxshots").options].some(o => o.value === m)) $("#maxshots").value = m; } catch (e) {}
let me = {name: "", person: ""}, setupFirst = false;
async function openSetup(first){
  const c = await api("/api/config");
  setupFirst = first;
  const cfg = c.configured ? c.config : c.defaults;
  $("#sttitle").textContent = first ? "Welcome to Reels Hub" : "Settings";
  $("#stintro").textContent = first ? "Tell the app who you are and where to keep things. You can change this later under Settings."
                                    : "Your name and folders on this computer.";
  $("#stname").value = cfg.name || ""; $("#stout").value = cfg.out || ""; $("#stvault").value = cfg.vault || "";
  $("#stcancel").hidden = first; $("#stsave").textContent = first ? "Save and start" : "Save";
  $("#sterr").hidden = true; $("#sterr").className = "sterr";
  $("#stpath").textContent = "Saved on this computer in " + c.path + (c.overrides.length ? " · started with --" + c.overrides.join(" --") + ", which overrides these" : "");
  $("#setup").classList.add("show");
  setTimeout(() => (first && !cfg.name ? $("#stname") : $("#stsave")).focus(), 0);
}
async function saveSetup(){
  const b = $("#stsave"); b.disabled = true;
  try {
    const j = await api("/api/config", {name: $("#stname").value, out: $("#stout").value, vault: $("#stvault").value});
    if (j.warning) { $("#sterr").textContent = j.warning; $("#sterr").className = "sterr stwarn"; $("#sterr").hidden = false; }
    $("#setup").classList.remove("show");
    current = null; reels = [];
    await loadReels(); setView(view);
    toast(setupFirst ? `Welcome, ${j.config.name}!` : "Settings saved");
  } catch (e) { $("#sterr").textContent = e.message; $("#sterr").className = "sterr"; $("#sterr").hidden = false; }
  finally { b.disabled = false; }
}
(async () => {
  try { const c = await api("/api/config"); if (!c.configured) await openSetup(true); } catch (e) {}
  await loadReels(); setView(view); poll();
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
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
