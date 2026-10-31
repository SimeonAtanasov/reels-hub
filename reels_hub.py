#!/usr/bin/env python3
"""
reels_hub.py - local web app for reels2notes.py

Keep this file next to reels2notes.py (e.g. in GitHub/reels-hub) and run:
    python reels_hub.py
Downloaded reels are stored in Downloads/reels (change with --out).
It opens http://127.0.0.1:8765 in your browser.

- Paste Instagram, TikTok or YouTube (incl. Shorts) links, one per line, and press Run: reels2notes.py
  processes them, with a live log
- Categories in the sidebar: create your own (e.g. Training), assign reels by dropdown or drag;
  reels without a category stay under Unassigned at the bottom
- Per reel: video, caption, transcript (click a line to jump there), on-screen text, snapshots, notes
- Star snapshots as favorites, drag to reorder, edit the text under each one (marked "Corrected text"),
  and print them on A4 left to right, top to bottom
- Prev / Next buttons or the arrow keys move between reels; the search box filters the list

Data: favorites and category live in reels/<reel>/hub.json, categories in reels/_categories.json,
and favorites are also written into each note.md (## Favorites) for Obsidian.

Obsidian: each reel gets a note in <vault>/Instagram, /TikTok or /YouTube named after the reel
(caption, transcript); "Send to Obsidian" copies starred images to <Platform>/attachments and
writes them with their text.

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
PLATFORM_DIRS = ("Instagram", "TikTok", "YouTube")                  # one vault folder per platform
FAV_START, FAV_END = "<!-- reels-hub:favorites start -->", "<!-- reels-hub:favorites end -->"

JOB = {"running": False, "log": [], "started": None, "finished": None, "urls": []}
JOB_LOCK = threading.Lock()
DATA_LOCK = threading.Lock()


# ---------------------------------------------------------------- note.md parsing
def parse_note(folder):
    note = folder / "note.md"
    if not note.exists():
        return None
    text = note.read_text(encoding="utf-8")
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
    if notes.strip() == "-":
        notes = ""

    shots = sorted(p.name for p in (folder / "shots").glob("*.jpg")) if (folder / "shots").exists() else []
    videos = [p.name for p in folder.glob("video.*") if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov", ".m4v")]
    url = meta.get("source", "")
    platform = meta.get("platform") or platform_of(url)
    if caption == "_none_":
        caption = ""
    generic = not title or title.startswith("Video by") or title.startswith("Post by") or title in folder.name
    label = ((caption.splitlines()[0] if caption else title) if generic else title)[:80]
    return {
        "id": folder.name, "title": title, "label": label or folder.name, "platform": platform,
        "url": meta.get("source", ""), "author": meta.get("author", ""),
        "date": meta.get("date", ""), "duration": meta.get("duration_s", ""),
        "caption": caption, "lang": lang.group(1) if lang else "",
        "transcript": transcript, "ocr": ocr, "notes": notes,
        "shots": shots, "times": shot_times(folder, shots),
        "video": videos[0] if videos else None,
        "state": read_state(folder),
    }


def shot_times(folder, shots):
    """Seconds into the video for each snapshot (shots.json from reels2notes, else every 2 s)."""
    saved = {}
    f = folder / "shots.json"
    if f.exists():
        try:
            saved = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            saved = {}
    return {s: float(saved.get(s, i * 2)) for i, s in enumerate(shots)}


def read_state(folder):
    s = {}
    f = folder / "hub.json"
    if f.exists():
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            s = {}
    return {"category": s.get("category", ""), "favorites": s.get("favorites", []),
            "note_name": s.get("note_name", ""), "sent": s.get("sent", "")}


def write_state(folder, state):
    (folder / "hub.json").write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")


def list_reels():
    if not OUT.exists():
        return []
    items = [r for f in OUT.iterdir() if f.is_dir() and not f.name.startswith("_")
             for r in [parse_note(f)] if r]
    for r in items:
        try:
            r["obsidian"] = ensure_vault_note(OUT / r["id"], r)
        except OSError as e:
            r["obsidian"] = {"error": str(e)}
    return sorted(items, key=lambda r: (r["date"], r["id"]), reverse=True)


def platform_of(url):
    u = (url or "").lower()
    if "tiktok.com" in u:
        return "TikTok"
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    if "instagram.com" in u:
        return "Instagram"
    return "Web"


# ---------------------------------------------------------------- Obsidian
def vault_dir(platform):
    return platform if platform in PLATFORM_DIRS else "Videos"


def vault_ok():
    return VAULT.is_dir()


def obsidian_uri(folder_name, name):
    from urllib.parse import quote
    return f"obsidian://open?vault={quote(VAULT.name)}&file={quote(folder_name + '/' + name)}"


def clean_name(text, fallback):
    name = re.sub(r'[\\/:*?"<>|#^\[\]\r\n\t]+', " ", text or "")
    name = re.sub(r"\s+", " ", name).strip(" .")[:80].strip(" .")
    return name or fallback


def ensure_vault_note(folder, r):
    """Create <Platform>/<reel name>.md in the vault once (caption + transcript); never overwrite it."""
    if not vault_ok():
        return {"error": f"Vault not found: {VAULT}"}
    state = read_state(folder)
    dname = vault_dir(r["platform"])
    vdir = VAULT / dname
    name = state["note_name"]
    if not name:
        name = clean_name(r["label"], r["id"])
        if (vdir / f"{name}.md").exists():          # name taken by another reel
            name = f"{name} ({r['id'].split('_')[-1]})"
        with DATA_LOCK:
            st = read_state(folder); st["note_name"] = name; write_state(folder, st)
    path = vdir / f"{name}.md"
    if not path.exists():
        vdir.mkdir(parents=True, exist_ok=True)
        path.write_text(vault_note_text(r, state), encoding="utf-8")
    return {"name": name, "folder": dname, "uri": obsidian_uri(dname, name), "sent": state["sent"]}


def vault_note_text(r, state):
    yaml = lambda s: '"' + str(s).replace('"', "'") + '"'
    lines = ["---",
             f"source: {r['url']}", f"author: {yaml(r['author'])}", f"date: {r['date']}",
             f"category: {yaml(state['category'])}", f"reel_id: {r['id']}",
             f"platform: {r['platform']}", f"tags: [{r['platform'].lower()}, reel]", "---",
             f"# {r['label']}", "",
             f"[Open on {r['platform']}]({r['url']})" if r["url"] else "", "",
             "## Caption", r["caption"] or "_none_", "",
             f"## Transcript{' (' + r['lang'] + ')' if r['lang'] else ''}"]
    lines += [f"- **{mmss(x['t'])}** {x['text']}" for x in r["transcript"]] or ["_no speech detected_"]
    if r["ocr"]:
        lines += ["", "## On-screen text"] + [f"- **{mmss(r['times'].get(x['shot'], 0))}** {x['text']}" for x in r["ocr"]]
    lines += ["", "## Favorites", FAV_START,
              '_Star snapshots in Reels Hub and press "Send to Obsidian"._', FAV_END,
              "", "## My notes", r["notes"] or "- ", ""]
    return "\n".join(lines)


def send_to_obsidian(reel_id):
    """Copy starred images into the vault and write them, with their text, into the reel's note."""
    import shutil
    if not vault_ok():
        raise ValueError(f"Obsidian vault not found: {VAULT}")
    folder = safe_path(reel_id)
    r = parse_note(folder)
    if not r:
        raise ValueError("unknown reel")
    info = ensure_vault_note(folder, r)
    if "error" in info:
        raise ValueError(info["error"])
    state = read_state(folder)
    favs = state["favorites"]
    att_rel = f"{info['folder']}/attachments/{reel_id}"
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

    block = []
    for i, f in enumerate(favs, 1):
        block += [f"![[{att_rel}/{f['shot']}|320]]",
                  f"**{i}. {mmss(r['times'].get(f['shot'], 0))}** {f['text']}"]
        if f.get("corrected"):
            block.append("*Corrected text*")
        block.append("")
    if not favs:
        block = ["_No starred images._"]
    block = "\n".join(block).rstrip("\n")

    path = VAULT / info["folder"] / f"{info['name']}.md"
    with DATA_LOCK:
        text = path.read_text(encoding="utf-8")
        if FAV_START in text and FAV_END in text:
            pre, rest = text.split(FAV_START, 1)
            text = pre + FAV_START + "\n" + block + "\n" + FAV_END + rest.split(FAV_END, 1)[1]
        else:                                          # markers removed by hand: append a fresh section
            text = text.rstrip() + f"\n\n## Favorites\n{FAV_START}\n{block}\n{FAV_END}\n"
        cat = state["category"].replace('"', "'")
        text = re.sub(r'^category:.*$', f'category: "{cat}"', text, count=1, flags=re.M)
        path.write_text(text, encoding="utf-8")
        st = read_state(folder); st["sent"] = time.strftime("%Y-%m-%d %H:%M"); write_state(folder, st)
    return {"name": info["name"], "uri": info["uri"], "count": len(favs), "sent": st["sent"]}


def safe_path(rel):
    p = (OUT / rel).resolve()
    if OUT.resolve() not in p.parents and p != OUT.resolve():
        raise PermissionError(rel)
    return p


def mmss(t):
    t = int(round(t))
    return f"{t // 60:02d}:{t % 60:02d}"


# ---------------------------------------------------------------- saving
def save_notes(reel_id, notes):
    folder = safe_path(reel_id)
    with DATA_LOCK:
        note = folder / "note.md"
        text = note.read_text(encoding="utf-8")
        body = notes.strip() or "- "
        if "\n## My notes" in text:
            text = text.split("\n## My notes")[0] + "\n## My notes\n" + body + "\n"
        else:
            text = text.rstrip() + "\n\n## My notes\n" + body + "\n"
        note.write_text(text, encoding="utf-8")


def save_state(reel_id, state):
    folder = safe_path(reel_id)
    if not (folder / "note.md").exists():
        raise ValueError("unknown reel")
    shots = {p.name for p in (folder / "shots").glob("*.jpg")}
    favs, seen = [], set()
    for f in state.get("favorites", []):
        shot = str(f.get("shot", ""))
        if shot in shots and shot not in seen:
            seen.add(shot)
            favs.append({"shot": shot, "text": str(f.get("text", ""))[:2000],
                         "corrected": bool(f.get("corrected"))})
    with DATA_LOCK:
        old = read_state(folder)                     # keep server-owned fields (vault note name, last send)
        clean = {"category": str(state.get("category", ""))[:100], "favorites": favs,
                 "note_name": old["note_name"], "sent": old["sent"]}
        write_state(folder, clean)
        write_note_favorites(folder, favs)
    return clean


def write_note_favorites(folder, favs):
    """Keep a ## Favorites section in note.md in sync (for Obsidian)."""
    note = folder / "note.md"
    text = note.read_text(encoding="utf-8")
    text = re.sub(r"\n## Favorites\n.*?(?=\n## |\Z)", "", text, flags=re.S)
    if favs:
        times = shot_times(folder, sorted(p.name for p in (folder / "shots").glob("*.jpg")))
        lines = ["", "## Favorites"]
        for f in favs:
            lines.append(f"![[{folder.name}/shots/{f['shot']}]]")
            mark = " _(corrected text)_" if f["corrected"] else ""
            lines.append(f"**{mmss(times.get(f['shot'], 0))}** {f['text']}{mark}")
            lines.append("")
        block = "\n".join(lines).rstrip("\n") + "\n"
        if "\n## Snapshots" in text:
            text = text.replace("\n## Snapshots", block + "\n## Snapshots", 1)
        else:
            text = text.rstrip() + "\n" + block
    note.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- categories
def cats_file():
    return OUT / "_categories.json"


def read_cats():
    f = cats_file()
    if f.exists():
        try:
            return [c for c in json.loads(f.read_text(encoding="utf-8")).get("categories", []) if c]
        except ValueError:
            pass
    return []


def write_cats(cats):
    cats_file().write_text(json.dumps({"categories": cats}, indent=1, ensure_ascii=False), encoding="utf-8")


def recategorize(old, new):
    for folder in OUT.iterdir():
        f = folder / "hub.json"
        if folder.is_dir() and f.exists():
            s = read_state(folder)
            if s["category"] == old:
                s["category"] = new
                f.write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")


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
            folder = safe_path(str(rid))
            if folder.parent != OUT.resolve() or folder.name.startswith("_") or not folder.is_dir():
                raise ValueError("not a reel folder")
            if with_obsidian and vault_ok():
                r = parse_note(folder)
                st = read_state(folder)
                if r and st["note_name"]:
                    d = vault_dir(r["platform"])
                    vtrash = VAULT / ".trash"
                    note = VAULT / d / f"{st['note_name']}.md"
                    att = VAULT / d / "attachments" / folder.name
                    if note.exists():
                        vtrash.mkdir(exist_ok=True)
                        shutil.move(str(note), str(_unique(vtrash / note.name)))
                    if att.exists():
                        vtrash.mkdir(exist_ok=True)
                        shutil.move(str(att), str(_unique(vtrash / f"attachments {folder.name}")))
            trash.mkdir(exist_ok=True)
            with DATA_LOCK:
                shutil.move(str(folder), str(_unique(trash / folder.name)))
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
def run_job(urls):
    with JOB_LOCK:
        JOB.update(running=True, log=[], started=time.time(), finished=None, urls=urls)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    cmd = [sys.executable, "-u", str(SCRIPT), "--out", str(OUT), *urls]
    try:
        proc = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", env=env)
        for line in proc.stdout:
            line = line.rstrip()
            if line and "warnings.warn" not in line:
                JOB["log"].append(line)
        proc.wait()
        JOB["log"].append(f"-- finished (exit code {proc.returncode})")
    except Exception as e:
        JOB["log"].append(f"-- could not start: {e}")
    JOB.update(running=False, finished=time.time())


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == "/":
            data = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif path == "/api/reels":
            self.send_json({"reels": list_reels(), "categories": read_cats(),
                            "vault": {"path": str(VAULT), "ok": vault_ok()}})
        elif path == "/api/status":
            self.send_json({"running": JOB["running"], "log": JOB["log"][-300:], "urls": JOB["urls"],
                            "finished": JOB["finished"]})
        elif path.startswith("/media/"):
            self.serve_file(path[len("/media/"):])
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.send_json({"error": "Bad JSON"}, 400)
        try:
            if path == "/api/run":
                if JOB["running"]:
                    return self.send_json({"error": "A run is already in progress"}, 409)
                urls = [u.strip() for u in re.split(r"[\s,]+", body.get("urls", "")) if u.strip().startswith("http")]
                urls = list(dict.fromkeys(urls))
                if not urls:
                    return self.send_json({"error": "No links found"}, 400)
                threading.Thread(target=run_job, args=(urls,), daemon=True).start()
                self.send_json({"ok": True, "count": len(urls)})
            elif path == "/api/notes":
                save_notes(body["id"], body.get("notes", ""))
                self.send_json({"ok": True})
            elif path == "/api/state":
                self.send_json({"ok": True, "state": save_state(body["id"], body.get("state", {}))})
            elif path == "/api/obsidian":
                self.send_json({"ok": True, **send_to_obsidian(body["id"])})
            elif path == "/api/delete":
                ids = body.get("ids") or []
                if not isinstance(ids, list) or not ids:
                    raise ValueError("Nothing to delete")
                self.send_json({"ok": True, **delete_reels(ids, bool(body.get("obsidian")))})
            elif path == "/api/categories":
                self.send_json({"ok": True, "categories": change_cats(body)})
            else:
                self.send_error(404)
        except (ValueError, KeyError, OSError) as e:
            self.send_json({"error": str(e)}, 400)

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
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
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
header h1{font-size:16px;margin:0;font-weight:600;letter-spacing:.2px}
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
.layout{flex:1;display:grid;grid-template-columns:310px 1fr;min-height:0}
aside{border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0;background:var(--panel)}
.runbox{padding:12px;border-bottom:1px solid var(--line);display:flex;flex-direction:column;gap:8px}
.runbox textarea{width:100%;height:70px;resize:vertical;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px;font-size:12px}
.runbox .row{display:flex;gap:8px;align-items:center}
.status{font-size:12px;color:var(--muted)}
.status.on{color:var(--warn)}
.listtools{display:flex;gap:8px;padding:10px 12px 6px;align-items:center}
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
.detail{padding:16px;display:grid;grid-template-columns:minmax(260px,360px) 1fr;gap:16px;align-items:start}
.player{position:sticky;top:16px;display:flex;flex-direction:column;gap:10px}
video{width:100%;max-height:68vh;background:#000;border-radius:var(--radius)}
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
.favimg .tb{position:absolute;bottom:6px;left:6px;background:rgba(0,0,0,.7);color:var(--accent);border-radius:6px;padding:0 6px;font-size:11px;font-variant-numeric:tabular-nums}
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
.right{display:flex;flex-direction:column;gap:16px;min-width:0}
#log{position:fixed;right:16px;bottom:16px;width:min(560px,calc(100vw - 32px));max-height:40vh;display:none;flex-direction:column;z-index:5}
#log.show{display:flex}
#log pre{margin:0;padding:10px 12px;overflow:auto;font:12px/1.45 ui-monospace,Consolas,monospace;white-space:pre-wrap;flex:1}
#log .head{display:flex;align-items:center;padding:6px 10px;border-bottom:1px solid var(--line);gap:8px}
#log .head span{flex:1;font-size:12px;color:var(--muted)}
#lightbox{position:fixed;inset:0;background:rgba(0,0,0,.9);display:none;align-items:center;justify-content:center;z-index:30}
#lightbox.show{display:flex}
#lightbox img{max-width:92vw;max-height:84vh;border-radius:8px}
#lightbox .lb-nav{position:absolute;top:50%;transform:translateY(-50%);font-size:34px;background:none;border:0;color:#fff;cursor:pointer;padding:20px}
#lightbox .prev{left:8px}#lightbox .next{right:8px}
#lightbox .cap{position:absolute;bottom:14px;left:0;right:0;text-align:center;color:#ccc;font-size:13px;padding:0 60px}
#lbstar{position:absolute;top:16px;right:16px;width:44px;height:44px;font-size:22px;line-height:44px}
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
  aside{max-height:45vh;border-right:0;border-bottom:1px solid var(--line)}
  .detail{grid-template-columns:1fr}
  .player{position:static}
  .paper{width:100%;min-height:0}
}
</style>
</head>
<body>
<header>
  <h1>Reels Hub</h1><span class="count" id="count"></span>
  <span class="spacer"></span>
  <span class="status"><kbd>&larr;</kbd> <kbd>&rarr;</kbd> switch reels</span>
  <button class="btn" id="viewbtn" title="Show all reels as a gallery">&#9638; Gallery</button>
  <button class="btn" id="showlog">Log</button>
</header>
<div class="layout">
  <aside>
    <div class="runbox">
      <textarea id="urls" placeholder="Paste Instagram, TikTok or YouTube links, one per line"></textarea>
      <div class="row">
        <button class="btn primary" id="run">Run</button>
        <span class="status" id="status">Idle</span>
      </div>
    </div>
    <div class="listtools">
      <input id="search" placeholder="Search">
      <button class="btn small" id="addcat">+ Category</button>
    </div>
    <div id="filters"></div>
    <div id="list"></div>
  </aside>
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
  <button class="star" id="lbstar" title="Favorite (F)">&#9734;</button>
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
const media = (id, f) => `/media/${encodeURIComponent(id)}/${f.split("/").map(encodeURIComponent).join("/")}`;
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const mmss = t => { t = Math.round(t || 0); return `${String(Math.floor(t/60)).padStart(2,"0")}:${String(t%60).padStart(2,"0")}`; };
const PB = {Instagram: ["IG", "#e1306c"], TikTok: ["TT", "#25f4ee"], YouTube: ["YT", "#ff4e45"]};
const pbadge = r => { const [t, c] = PB[r.platform] || [String(r.platform||"").slice(0,3), "#8b949b"]; return `<span class="pb" style="--pc:${c}" title="${esc(r.platform)}">${t}</span>`; };
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
async function loadReels(keepId){
  const j = await api("/api/reels");
  reels = j.reels; cats = j.categories; vault = j.vault || vault;
  $("#count").textContent = reels.length ? `${reels.length} reel${reels.length>1?"s":""}` : "";
  renderList();
  const id = keepId || decodeURIComponent(location.hash.slice(1)) || (filtered[0] && filtered[0].id);
  if (id && reels.some(r => r.id === id)) select(id, false, true);
  else if (filtered[0]) select(filtered[0].id, false, true);
}

const saveTimers = {};
function saveState(r, now){
  clearTimeout(saveTimers[r.id]);
  const go = () => api("/api/state", {id: r.id, state: r.state}).catch(e => alert("Could not save: " + e.message));
  if (now) go(); else saveTimers[r.id] = setTimeout(go, 500);
}

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
function renderList(){
  const q = $("#search").value.trim().toLowerCase();
  const groups = [...cats.map(c => ({name: c, label: c})), {name: "", label: "Unassigned"}];
  filtered = [];
  let html = "";
  renderFilters();
  groups.forEach((g, gi) => {
    if (!catShown(g.name)) return;
    const items = reels.filter(r => catOf(r) === g.name && matches(r, q));
    filtered.push(...items);
    if (!g.name && !items.length && cats.length && (q || fPlat.size)) return;
    const closed = collapsed.has(g.label);
    html += `<div class="group ${closed?"closed":""}" data-cat="${esc(g.name)}" data-label="${esc(g.label)}">
      <span class="gname" data-toggle><span class="caret">&#9662;</span>${esc(g.label)}</span>
      <span class="gcount">${items.length}</span>
      ${g.name ? `<button class="ico" data-catmove="-1" title="Move up" ${gi===0?"disabled":""}>&#8593;</button>
                  <button class="ico" data-catmove="1" title="Move down" ${gi===cats.length-1?"disabled":""}>&#8595;</button>
                  <button class="ico" data-rename title="Rename">&#9998;</button>
                  <button class="ico" data-delcat title="Delete category">&#10005;</button>` : ""}
    </div>`;
    if (!closed) html += items.length ? items.map(r => `
      <div class="item ${current && current.id===r.id?"active":""} ${selected.has(r.id)?"sel":""}" data-id="${esc(r.id)}" draggable="true">
        ${r.shots[0] ? `<img loading="lazy" src="${media(r.id,"shots/"+r.shots[0])}" alt="">` : `<img alt="">`}
        <div><div class="t">${esc(r.label)}</div>
        <div class="s">${pbadge(r)} ${esc(r.author)} · ${esc(r.date)}${r.state.favorites.length?` · <span class="fc">&#9733; ${r.state.favorites.length}</span>`:""}</div></div>
      </div>`).join("") : `<div class="gempty">${g.name ? "Drag reels here or pick this category on a reel" : "Nothing here"}</div>`;
  });
  $("#list").innerHTML = html || `<div class="empty">No matches</div>`;
  if (view === "gallery") renderGallery();
}

/* ---------- multi-select + bulk move ---------- */
let selected = new Set(), anchor = null;
function pick(id, e){
  if (e.shiftKey && anchor) {
    const ids = filtered.map(r => r.id), a = ids.indexOf(anchor), b = ids.indexOf(id);
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

/* ---------- delete ---------- */
function askDelete(rs){
  return new Promise(res => {
    const names = rs.slice(0, 4).map(r => "• " + r.label).join("\n") + (rs.length > 4 ? `\n• and ${rs.length - 4} more` : "");
    $("#ctitle").textContent = rs.length > 1 ? `Delete ${rs.length} reels?` : "Delete this reel?";
    $("#cmsg").textContent = names + "\n\nVideo, snapshots, transcript, favorites and notes move to Downloads\\reels\\_trash, so you can still restore them.";
    const hasNotes = rs.some(r => r.obsidian && r.obsidian.name);
    $("#cobs").checked = false; $("#cobs").closest("label").hidden = !hasNotes || !vault.ok;
    $("#confirm").classList.add("show");
    const done = v => { $("#confirm").classList.remove("show"); $("#cyes").onclick = $("#cno").onclick = null; res(v); };
    $("#cyes").onclick = () => done({ok: true, obsidian: $("#cobs").checked});
    $("#cno").onclick = () => done({ok: false});
  });
}
async function deleteReels(ids){
  const rs = ids.map(id => reels.find(r => r.id === id)).filter(Boolean);
  if (!rs.length) return;
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
  const groups = [...cats, ""];
  let html = "";
  for (const g of groups) {
    if (!catShown(g)) continue;
    const items = reels.filter(r => catOf(r) === g && matches(r, q));
    html += `<section class="gsec" data-cat="${esc(g)}"><h3>${g ? esc(g) : "Unassigned"} <span class="gcount">${items.length}</span></h3>
      <div class="ggrid">${items.length ? items.map(r => `
        <div class="gcard ${current && current.id===r.id?"active":""} ${selected.has(r.id)?"sel":""}" data-open="${esc(r.id)}" tabindex="0" draggable="true">
          <span class="tick">&#10003;</span>
          <div class="gthumb">${cover(r) ? `<img loading="lazy" src="${media(r.id,"shots/"+cover(r))}" alt="">` : ""}
            ${r.state.favorites.length ? `<span class="gstar">&#9733; ${r.state.favorites.length}</span>` : ""}
            ${r.duration ? `<span class="gdur">${mmss(+r.duration)}</span>` : ""}</div>
          <div class="gtitle">${esc(r.label)}</div>
          <div class="s">${pbadge(r)} ${esc(r.author)} · ${esc(r.date)}</div>
        </div>`).join("") : `<div class="gdrop">Drop reels here</div>`}</div></section>`;
  }
  $("#main").innerHTML = `<div class="gallery"><div class="ghint">Click to open · <kbd>Ctrl</kbd>+click or <kbd>Shift</kbd>+click to select several · drag onto a category to move</div>${html || `<div class="empty">No reels match.</div>`}</div>`;
}

function select(id, scroll=true, keepView=false){
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
  const i = filtered.findIndex(x => x.id === current.id);
  $("#prev").disabled = i <= 0; $("#next").disabled = i < 0 || i >= filtered.length - 1;
  $("#pos").textContent = i < 0 ? "" : `${i+1} / ${filtered.length}`;
}

function render(r){
  const tabs = [["transcript","Transcript",r.transcript.length],["caption","Caption",""],["ocr","On-screen text",r.ocr.length],["notes","My notes",""]];
  $("#main").innerHTML = `
  <div class="detail">
    <div class="player">
      ${r.video ? `<video id="vid" controls playsinline preload="metadata" src="${media(r.id, r.video)}"></video>` : `<div class="empty">No video file</div>`}
      <div class="nav">
        <button class="btn" id="prev">&larr; Prev</button>
        <span class="pos" id="pos"></span>
        <button class="btn" id="next">Next &rarr;</button>
      </div>
      <label class="catsel">Category ${catSelect(r)}</label>
      <div><button class="btn small danger" id="delone">Delete this reel</button></div>
      <div class="meta">${esc(r.author)} · ${esc(r.date)}${r.duration?` · ${Math.round(r.duration)}s`:""}${r.url?` · <a href="${esc(r.url)}" target="_blank" rel="noopener">Open on ${esc(r.platform)}</a>`:""}</div>
      <div class="meta">${r.obsidian && r.obsidian.uri ? `Obsidian note: <a href="${esc(r.obsidian.uri)}">${esc(r.obsidian.name)}</a>` : (r.obsidian && r.obsidian.error ? esc(r.obsidian.error) : "")}</div>
    </div>
    <div class="right">
      <div class="card">
        <div class="tabs">${tabs.map(([k,l,n]) => `<div class="tab ${tab===k?"active":""}" data-tab="${k}">${l}${n!==""?`<span class="n">${n}</span>`:""}</div>`).join("")}</div>
        <div class="pane ${tab==="transcript"?"active":""}" data-pane="transcript">${
          r.transcript.length ? r.transcript.map(x => `<div class="tline" data-t="${x.t}"><span class="ts">${mmss(x.t)}</span><span>${esc(x.text)}</span></div>`).join("")
          : `<span class="meta">No speech detected</span>`}${r.lang?`<div class="meta" style="margin-top:8px">Language: ${esc(r.lang)}</div>`:""}</div>
        <div class="pane ${tab==="caption"?"active":""}" data-pane="caption">${esc(r.caption) || `<span class="meta">No caption</span>`}</div>
        <div class="pane ${tab==="ocr"?"active":""}" data-pane="ocr">${
          r.ocr.length ? r.ocr.map(x => `<div class="oline"><img src="${media(r.id,"shots/"+x.shot)}" data-shot="${esc(x.shot)}" alt=""><span><span class="ts">${mmss(r.times[x.shot])}</span> ${esc(x.text)}</span></div>`).join("")
          : `<span class="meta">No on-screen text found</span>`}</div>
        <div class="pane ${tab==="notes"?"active":""}" data-pane="notes">
          <textarea class="notes" id="notes" placeholder="Your notes: weight used, sets/reps, cues, how it felt">${esc(r.notes)}</textarea>
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
      <div class="card">
        <div class="cardhead"><h2>Snapshots · ${r.shots.length}</h2><span class="hint">&#9734; adds to favorites · click to enlarge</span></div>
        <div class="shots">${r.shots.map(s => `
          <figure><img loading="lazy" src="${media(r.id,"shots/"+s)}" data-shot="${esc(s)}" alt="">
            <button class="star" data-star="${esc(s)}" title="Favorite">&#9734;</button>
            <figcaption>${mmss(r.times[s])}</figcaption></figure>`).join("")}</div>
      </div>
      </div>
    </div>
  </div>`;
  updateNav();
  renderFavs(r);
  showObsLine(r);
  const v = $("#vid");
  if (v) v.addEventListener("timeupdate", () => {
    let best = null;
    document.querySelectorAll(".tline").forEach(el => { if (+el.dataset.t <= v.currentTime) best = el; el.classList.remove("now"); });
    best && best.classList.add("now");
  });
}

function renderFavs(r){
  const favs = r.state.favorites;
  $("#favcount").textContent = favs.length;
  $("#printbtn").disabled = !favs.length;
  $("#favs").className = favs.length ? "favs" : "";
  $("#favs").innerHTML = favs.length ? favs.map((f, i) => `
    <div class="fav" data-i="${i}">
      <div class="favimg" draggable="true" title="Drag to reorder">
        <img src="${media(r.id,"shots/"+f.shot)}" alt="" draggable="false">
        <span class="num">${i+1}</span><span class="tb">${mmss(r.times[f.shot])}</span>
        <button class="ico rm" data-unfav="${esc(f.shot)}" title="Remove from favorites">&#10005;</button>
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
  const favs = r.state.favorites, i = favs.findIndex(f => f.shot === shot);
  if (i >= 0) favs.splice(i, 1);
  else favs.push({shot, text: textAt(r, shot), corrected: false});
  saveState(r, true); renderFavs(r); renderList();
  if ($("#lightbox").classList.contains("show")) showLb();
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
        <img src="${media(r.id,"shots/"+f.shot)}" alt="">
        <div class="pcap">${$("#pnums").checked?`<span class="pnum">${i+1}.</span>`:""}<b>${mmss(r.times[f.shot])}</b>${esc(f.text)}</div>
        ${f.corrected ? `<div class="pcorr">Corrected text</div>` : ""}
      </figure>`).join("")}
    </div>`;
}

/* ---------- lightbox ---------- */
function openLightbox(shot){ lb.list = current.shots; lb.i = Math.max(0, lb.list.indexOf(shot)); showLb(); $("#lightbox").classList.add("show"); }
function showLb(){
  const s = lb.list[lb.i], on = isFav(current, s);
  $("#lbimg").src = media(current.id, "shots/" + s);
  $("#lbstar").classList.toggle("on", on); $("#lbstar").innerHTML = on ? "&#9733;" : "&#9734;";
  const t = textAt(current, s);
  $("#lbcap").textContent = `${lb.i+1} / ${lb.list.length} · ${mmss(current.times[s])}` + (t ? `  ·  ${t}` : "");
}

function move(d){
  if (!current) return;
  const i = filtered.findIndex(x => x.id === current.id) + d;
  if (i >= 0 && i < filtered.length) select(filtered[i].id);
}

/* ---------- events ---------- */
document.addEventListener("click", async e => {
  const t = e.target, c = sel => t.closest(sel);
  let el;
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
  if (t.id === "bulkdel") return deleteReels([...selected]);
  if (t.id === "delone") return deleteReels([current.id]);
  if (t.id === "bulkall") { filtered.forEach(r => selected.add(r.id)); return refreshSel(); }
  if ((el = c(".tab"))) { tab = el.dataset.tab;
    document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x===el));
    document.querySelectorAll(".pane").forEach(x => x.classList.toggle("active", x.dataset.pane===tab)); return; }
  if ((el = c(".tline"))) { const v = $("#vid"); if (v) { v.currentTime = +el.dataset.t; v.play(); } return; }
  if (t.dataset && t.dataset.shot && !c("#favs")) return openLightbox(t.dataset.shot);
  switch (t.id) {
    case "prev": return move(-1);
    case "next": return move(1);
    case "lightbox": case "lbimg": return $("#lightbox").classList.remove("show");
    case "lbprev": lb.i = (lb.i - 1 + lb.list.length) % lb.list.length; return showLb();
    case "lbnext": lb.i = (lb.i + 1) % lb.list.length; return showLb();
    case "lbstar": return toggleFav(current, lb.list[lb.i]);
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
      try { await api("/api/notes", {id: current.id, notes: $("#notes").value}); current.notes = $("#notes").value; $("#saved").hidden = false; }
      catch (err) { alert(err.message); } return;
    }
    case "run": {
      try { await api("/api/run", {urls: $("#urls").value}); } catch (err) { return alert(err.message); }
      $("#urls").value = ""; $("#log").classList.add("show"); return poll();
    }
  }
});

document.addEventListener("input", e => {
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
  const target = sec || (g && groupFor(g));
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
  if (target && data.startsWith("reels:")) {
    e.preventDefault();
    let ids = []; try { ids = JSON.parse(data.slice(6)); } catch (err) {}
    moveReels(ids, target.dataset.cat);
    if (ids.length > 1) clearSel();
  }
  document.querySelectorAll(".drop").forEach(x => x.classList.remove("drop"));
});

document.addEventListener("keydown", e => {
  if (/TEXTAREA|INPUT|SELECT/.test(document.activeElement.tagName)) return;
  if ($("#printsheet").classList.contains("show")) { if (e.key === "Escape") $("#printsheet").classList.remove("show"); return; }
  const open = $("#lightbox").classList.contains("show");
  if (e.key === "Escape" && !open && favFull) return toggleFull(false);
  if ($("#confirm").classList.contains("show")) { if (e.key === "Escape") $("#cno").click(); if (e.key === "Enter") $("#cyes").click(); return; }
  if (e.key === "Escape" && !open && selected.size) return clearSel();
  if (e.key === "Delete" && !open && selected.size) return deleteReels([...selected]);
  if (e.key === "Escape") $("#lightbox").classList.remove("show");
  if (view === "gallery" && !open) return;
  if (open && (e.key === "f" || e.key === "F")) toggleFav(current, lb.list[lb.i]);
  if (e.key === "ArrowLeft") open ? (lb.i = (lb.i-1+lb.list.length)%lb.list.length, showLb()) : move(-1);
  if (e.key === "ArrowRight") open ? (lb.i = (lb.i+1)%lb.list.length, showLb()) : move(1);
});

/* ---------- run polling ---------- */
async function poll(){
  const s = await api("/api/status");
  $("#logtext").textContent = s.log.join("\n") || "(waiting for output)";
  $("#logtext").scrollTop = 1e9;
  $("#run").disabled = s.running;
  const done = s.log.filter(l => l.includes("done ->") || l.includes("already done")).length;
  $("#status").textContent = s.running ? `Running · ${done} / ${s.urls.length} done` : (s.finished ? "Finished" : "Idle");
  $("#status").classList.toggle("on", s.running);
  $("#logtitle").textContent = s.running ? "Run log · working (first run downloads models)" : "Run log";
  if (s.running) { wasRunning = true; refreshQuiet(); setTimeout(poll, 1500); }
  else if (wasRunning) { wasRunning = false; loadReels(current && current.id); }
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

loadReels().then(() => { setView(view); poll(); });
</script>
</body>
</html>
"""


def main():
    global OUT, VAULT
    ap = argparse.ArgumentParser(description="Local web app for reels2notes.py")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--vault", default=str(VAULT), help="Obsidian vault folder (notes go into Instagram/TikTok/YouTube folders)")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    OUT = Path(args.out).resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    VAULT = Path(args.vault)
    if not VAULT.is_dir():
        print(f"Obsidian vault not found at {VAULT} - Obsidian notes are off (use --vault PATH).")
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
