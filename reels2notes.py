#!/usr/bin/env python3
"""
reels2notes.py - download Instagram reels, TikTok videos, YouTube videos/Shorts and X posts and turn
each into a Markdown note with caption, spoken transcript, on-screen text (OCR) and snapshots.
Instagram photo slideshows (several slides, or one photo) are joined into one video, a photo for 4 s each.

Setup (once):
    pip install yt-dlp faster-whisper easyocr
    # plus ffmpeg on PATH  (Windows: winget install ffmpeg | macOS: brew install ffmpeg)

Usage:
    python -m yt_dlp -U                              # keep yt-dlp current
    python reels2notes.py https://www.instagram.com/p/DeugAKZS1eg/
    python reels2notes.py https://www.tiktok.com/@user/video/1234567890
    python reels2notes.py https://www.youtube.com/shorts/abcdEFGhijk
    python reels2notes.py https://x.com/user/status/1234567890123456789
    python reels2notes.py -f links.txt            # one URL per line
Options:
    --cookies cookies.txt           login cookies (Netscape format) - use if a download needs login
    --browser firefox|edge|chrome   read login cookies straight from a browser (default: none)
    --scene 0.3                     scene-change sensitivity for snapshots (lower = more frames)
    --max-shots auto                Auto (default): count from video length and editing pace,
                                    or a number: at most that many (one every 2 s plus scene cuts),
                                    or every:N for exactly one snapshot every N seconds (no scene cuts, at most 1000)
    --ocr-max 60                    read on-screen text from at most this many snapshots
    --redo                          re-process videos that were already done (replaces transcript and snapshots)
    --new-version                   process already-done videos again as a separate copy (_v2, _v3, ...)
    --model small                   whisper model: tiny|base|small|medium
    --lang en                       force transcript language (default: auto)
    --no-ocr                        skip on-screen text recognition
    --out PATH                      output folder (default: Downloads/reels)
"""
import argparse, json, re, shutil, subprocess, sys, time
from pathlib import Path
import os, warnings
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
warnings.filterwarnings("ignore", category=UserWarning)


VIDEO_EXT = (".mp4", ".webm", ".mkv", ".mov", ".m4v")


def platform_of(url, info=None):
    key = ((info or {}).get("extractor_key") or "").lower()
    u = url.lower()
    if "tiktok" in key or "tiktok.com" in u:
        return "TikTok"
    if "youtube" in key or "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    if "instagram" in key or "instagram.com" in u:
        return "Instagram"
    if "twitter" in key or re.match(r"^https?://([a-z0-9-]+\.)*(x|twitter)\.com(/|$)", u):
        return "X"
    return (info or {}).get("extractor_key") or "Web"


def find_video(folder):
    vids = [p for p in folder.glob("video.*") if p.suffix.lower() in VIDEO_EXT]
    return vids[0] if vids else None


# ---------------------------------------------------------------- progress / ETA
# Lines starting with "@@PROGRESS " carry JSON for Reels Hub; it shows them as a progress bar.
DEFAULT_STATS = {"tx_factor": 0.35, "model_load": 6.0, "ocr_per_img": 1.5, "ocr_load": 8.0, "shot_factor": 0.03}


def load_stats(out):
    try:
        s = json.loads((Path(out) / "_stats.json").read_text(encoding="utf-8"))
        return {**DEFAULT_STATS, **{k: float(v) for k, v in s.items() if k in DEFAULT_STATS}}
    except (OSError, ValueError):
        return dict(DEFAULT_STATS)


def learn(out, stats, key, value, weight=0.6):
    """Blend a measured value into the saved speed profile (exponential moving average)."""
    if value <= 0:
        return
    stats[key] = round(stats[key] * (1 - weight) + value * weight, 4)
    try:
        (Path(out) / "_stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    except OSError:
        pass


class Progress:
    ORDER = ["download", "transcribe", "snapshots", "ocr"]
    LABEL = {"download": "Downloading", "snapshots": "Taking snapshots", "transcribe": "Transcribing", "ocr": "Reading on-screen text"}

    def __init__(self, i, n, stats, do_ocr, max_shots, every=0):
        self.i, self.n, self.stats, self.do_ocr, self.max_shots, self.every = i, n, stats, do_ocr, max_shots, every
        self.t0 = time.time()
        self.est = {}            # estimated seconds per remaining stage
        self.stage, self.frac, self.base = None, 0.0, time.time()
        self.last_emit = 0
        self.dl_eta = None

    def plan(self, duration, nshots=None):
        st = self.stats
        d = max(duration or 0, 1)
        cap = self.max_shots or AUTO_CAP
        if nshots is None and self.every:
            nshots = min(EVERY_CAP, int(d / self.every) + 1)
        n = nshots if nshots is not None else min(cap, max(3, int(d / min(20.0, max(2.0, 2.0 * (d / 60) ** 0.5)))))
        self.est["snapshots"] = 2 + d * st["shot_factor"]
        self.est["transcribe"] = st["model_load"] + d * st["tx_factor"]
        self.est["ocr"] = (st["ocr_load"] * (_ocr is None) + min(n, 60) * st["ocr_per_img"]) if self.do_ocr else 0
        self.planned = True

    def start(self, stage):
        CURRENT_STEP[0] = stage
        self.stage, self.frac, self.base = stage, 0.0, time.time()
        self.emit(force=True)

    def rebase(self):                       # e.g. after the Whisper model has loaded
        self.base = time.time()

    def update(self, frac, dl_eta=None):
        self.frac = max(0.0, min(1.0, frac))
        self.dl_eta = dl_eta
        self.emit()

    def stage_left(self):
        el = time.time() - self.base
        if self.stage == "download":
            return self.dl_eta
        est = self.est.get(self.stage, 0)
        if self.frac > 0.05 and el > 1.5:      # enough progress: use the live rate
            return el / self.frac * (1 - self.frac)
        return max(est * (1 - self.frac) - (el if self.frac == 0 else 0), est * 0.1)

    def emit(self, force=False):
        now = time.time()
        if not force and now - self.last_emit < 0.8:
            return
        self.last_emit = now
        left = self.stage_left()
        later = self.ORDER[self.ORDER.index(self.stage) + 1:] if self.stage in self.ORDER else []
        if left is not None and getattr(self, "planned", False):
            left = left + sum(self.est.get(s, 0) for s in later)
        elif self.stage != "download":
            left = None
        print("@@PROGRESS " + json.dumps({
            "i": self.i, "n": self.n, "stage": self.stage, "label": self.LABEL.get(self.stage, self.stage),
            "stage_pct": round(self.frac * 100, 1), "eta": None if left is None else round(left),
            "elapsed": round(now - self.t0), "eta_partial": not getattr(self, "planned", False),
        }), flush=True)


def _secs(txt):
    try:
        parts = [int(x) for x in txt.split(":")]
    except ValueError:
        return None
    t = 0
    for p in parts:
        t = t * 60 + p
    return t


class YtdlpError(RuntimeError):
    """yt-dlp failed; the message is its own error line, made readable."""


def friendly_error(line):
    """'ERROR: [Instagram] abc: No video formats found!; please report this issue ...' -> a plain sentence."""
    msg = re.sub(r"^ERROR:\s*(\[[^\]]*\]\s*)?([\w-]+:\s+)?", "", line.strip())
    msg = re.split(r";\s*please report this issue|\s*Confirm you are on the latest version", msg)[0].strip()
    if re.search(r"no video formats found|there is no video in this post", msg, re.I):
        return "this post has no video (photo posts work for Instagram only)"
    return msg or "yt-dlp stopped without saying why"


def run_ytdlp(cmd, prog):
    """Run yt-dlp, show its normal output, and turn its progress lines into progress updates."""
    print("  >", " ".join(map(str, cmd)))
    proc = subprocess.Popen(cmd + ["--newline"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    pat = re.compile(r"\[download\]\s+([\d.]+)%.*?(?:ETA\s+(\S+))?\s*$")
    err = ""
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith("ERROR:"):
            err = line
        m = pat.search(line)
        if m:
            if prog:
                prog.update(float(m.group(1)) / 100, _secs(m.group(2) or ""))
            if not line.startswith("[download] 100"):
                continue                      # don't flood the log with progress lines
        print(line, flush=True)
    if proc.wait() != 0:
        raise YtdlpError(friendly_error(err) if err else f"yt-dlp stopped (exit code {proc.returncode})")


def run(cmd):
    print("  >", " ".join(map(str, cmd)))
    return subprocess.run(cmd, check=True)


def download(url, workdir, browser, cookies, prog=None):
    base = [sys.executable, "-m", "yt_dlp", "--write-info-json", "--no-playlist",
            "--playlist-items", "1",   # posts with several videos (e.g. on X): take the first
            # browser-playable MP4, max 1080p (YouTube serves separate video/audio streams)
            "-f", FORMAT,
            "--merge-output-format", "mp4",
            "-o", str(workdir / "video.%(ext)s")]
    if cookies:
        auth = ["--cookies", cookies]
    elif browser and browser != "none":
        auth = ["--cookies-from-browser", browser]
    else:
        auth = []
    try:
        run_ytdlp(base + auth + [url], prog)
    except YtdlpError:
        if not auth:
            raise
        print("  login cookies failed, retrying without login...")
        run_ytdlp(base + [url], prog)
    info = json.loads((workdir / "video.info.json").read_text(encoding="utf-8"))
    video = find_video(workdir)
    if not video:
        raise RuntimeError("download finished but no video file was found")
    return video, info


def video_duration(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(video)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def scene_cuts(video, scene):
    """Times (s) and strength of hard cuts, from one fast low-resolution pass."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-an",
           "-vf", f"scale=160:-2,select='gt(scene,{scene})',metadata=print", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    cuts, t = [], None
    for line in r.stderr.splitlines():
        m = re.search(r"pts_time:\s*([\d.]+)", line)
        if m:
            t = float(m.group(1)); continue
        m = re.search(r"lavfi\.scene_score=([\d.]+)", line)
        if m and t is not None:
            cuts.append((t, float(m.group(1)))); t = None
    return cuts


def _spread(items, k, dur):
    """Thin (time, score) items to at most k, one per equal slice of the video (best score wins)."""
    if len(items) <= k or dur <= 0:
        return items
    best = {}
    for t, sc in items:
        b = min(k - 1, int(t / dur * k))
        if b not in best or sc > best[b][1]:
            best[b] = (t, sc)
    return sorted(best.values())


def _grid(video, outdir, interval):
    """One pass: a frame every `interval` seconds. Returns [(time, path)]."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-i", str(video), "-an",
           "-vf", f"fps=1/{interval:.3f},scale=720:-2,showinfo", "-fps_mode", "vfr", "-q:v", "3",
           str(outdir / "g_%05d.jpg")]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-1500:])
        raise subprocess.CalledProcessError(r.returncode, "ffmpeg")
    files = sorted(outdir.glob("g_*.jpg"))
    times = [float(t) for t in re.findall(r"pts_time:\s*([\d.]+)", r.stderr)]
    if len(times) != len(files):
        times = [k * interval for k in range(len(files))]
    return list(zip(times, files))


AUTO_CAP = 400
EVERY_CAP = 1000                                          # "every N seconds": never more than this many


def every_interval(dur, every):
    """The gap for "a snapshot every N seconds"; widened only if that would give more than EVERY_CAP."""
    if dur and dur / every + 1 > EVERY_CAP:
        gap = dur / (EVERY_CAP - 1)
        print(f"  every {every:g} s would give more than {EVERY_CAP} snapshots: one every {gap:.1f} s instead")
        return gap
    return every


def auto_plan(dur, n_cuts):
    """Auto snapshot plan from video length and editing pace.
    Base gap grows with length: 2 s for a 1-min reel, ~5 s at 6 min, ~12 s at 36 min, max 20 s.
    Every scene cut is added on top (new content), but very fast cutting is held back (flicker, music videos).
    Returns (interval, cut_budget, explanation)."""
    mins = max(dur, 1) / 60
    interval = min(20.0, max(2.0, 2.0 * mins ** 0.5))
    grid_n = int(dur / interval) + 1
    pace = n_cuts / mins                                  # scene cuts per minute
    if pace > 30:                                         # flicker / rapid montage: cap cut frames
        budget = grid_n // 2
    else:
        budget = n_cuts
    budget = max(0, min(budget, AUTO_CAP - grid_n))
    if grid_n > AUTO_CAP:                                 # extremely long: widen the gap instead
        interval = dur / (AUTO_CAP - 1); grid_n = AUTO_CAP; budget = 0
    why = f"{int(dur)//60}:{int(dur)%60:02d} video, {pace:.0f} cuts/min -> every {interval:.0f} s + up to {budget} scene cuts"
    return interval, budget, why


def snapshots(video, workdir, scene, max_shots=200, speech=(), prog=None, every=0):
    """Dense snapshots: a frame every `interval` seconds plus scene cuts.
    max_shots=0 means Auto (see auto_plan); otherwise at most max_shots, every 2 s or spread out.
    every > 0: exactly one frame every `every` seconds, no scene cuts (max_shots is not used)."""
    shots = workdir / "shots"
    shots.mkdir(exist_ok=True)
    dur = video_duration(video)
    all_cuts = scene_cuts(video, scene) if dur and not every else []
    if prog:
        prog.update(0.4)
    if every:
        interval, budget, max_shots = every_interval(dur, every), 0, 0
        print(f"  a snapshot every {interval:g} s (set by hand, no scene cuts)")
    elif max_shots <= 0:
        interval, budget, why = auto_plan(dur, len(all_cuts))
        print(f"  Auto snapshots: {why}")
    else:                                                    # a real maximum: grid + cuts never exceed it
        budget = min(max(1, max_shots // 4), len(all_cuts))
        grid_n = max(1, max_shots - budget)
        interval = max(2.0, dur / grid_n) if dur else 2.0
    grid = _grid(video, shots, interval)
    if prog:
        prog.update(0.7)
    cuts = _spread(all_cuts, budget, dur) if dur and budget else []
    extra = []
    for t, _ in cuts:
        t = min(t + 0.2, max(0.0, dur - 0.1))
        if all(abs(t - g) > 0.6 for g, _ in grid):          # skip cuts right next to a grid frame
            out = shots / f"c_{len(extra):05d}.jpg"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
                            "-frames:v", "1", "-vf", "scale=720:-2", "-q:v", "3", str(out)])
            if out.exists():
                extra.append((t, out))
    if prog:
        prog.update(0.95)
    if max_shots > 0 and len(grid) + len(extra) > max_shots:  # trim to the limit: extra cuts first, then thin the grid
        keep_extra = max(0, max_shots - len(grid))
        for _, f in extra[keep_extra:]:
            f.unlink(missing_ok=True)
        extra = extra[:keep_extra]
        if len(grid) > max_shots:
            idx = {round(i * (len(grid) - 1) / (max_shots - 1)) for i in range(max_shots)} if max_shots > 1 else {0}
            for i, (_, f) in enumerate(grid):
                if i not in idx:
                    f.unlink(missing_ok=True)
            grid = [g for i, g in enumerate(grid) if i in idx]
    frames = sorted(grid + extra, key=lambda x: x[0])
    files, times = [], []
    for k, (t, f) in enumerate(frames, 1):                   # rename in time order: shot_001.jpg ...
        dest = shots / f"shot_{k:03d}.jpg"
        f.replace(dest)
        files.append(dest); times.append(round(t, 2))
    print(f"  {len(files)} snapshots (every {interval:.3g} s + {len(extra)} scene cuts of {len(all_cuts)} found)")
    (workdir / "shots.json").write_text(json.dumps(dict(zip([f.name for f in files], times)), indent=1),
                                        encoding="utf-8")
    return files


def transcribe(video, model_name, lang, prog=None, out=None):
    import numpy as np
    from faster_whisper import WhisperModel
    # decode audio with ffmpeg (16 kHz mono float32) - avoids PyAV version issues
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-vn",
                          "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True).stdout  # no audio -> empty
    audio = np.frombuffer(raw, dtype=np.float32)
    if audio.size == 0:
        return [], lang or "-", []
    dur = audio.size / 16000
    # Low RAM: fall back to smaller models instead of failing (small -> base -> tiny)
    sizes = ["large-v3", "medium", "small", "base", "tiny"]
    chain = [model_name] + [m for m in sizes[sizes.index(model_name) + 1:]] if model_name in sizes else [model_name, "base", "tiny"]
    for attempt, name in enumerate(chain):
        model = None
        try:
            t0 = time.time()
            model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=min(4, os.cpu_count() or 4))
            t1 = time.time()
            if prog:
                prog.rebase(); prog.update(0)
            segments, info = model.transcribe(audio, language=lang, vad_filter=True)
            lines, starts = [], []
            for s in segments:                 # segments arrive as Whisper works through the audio
                lines.append(f"[{int(s.start)//60:02d}:{int(s.start)%60:02d}] {s.text.strip()}")
                starts.append(float(s.start))
                if prog:
                    prog.update(s.end / dur)
            if name != model_name:
                print(f"  note: transcribed with the smaller '{name}' model because memory was low")
            break
        except (RuntimeError, MemoryError, OSError) as e:
            low_mem = isinstance(e, MemoryError) or any(k in str(e).lower() for k in ("alloc", "memory"))
            del model
            import gc; gc.collect()
            if not low_mem or attempt == len(chain) - 1:
                raise
            print(f"  not enough memory for the '{name}' Whisper model ({e}) - retrying with '{chain[attempt + 1]}'")
    if prog and out and name == model_name:
        stats = prog.stats
        learn(out, stats, "model_load", t1 - t0)
        if dur > 5:
            learn(out, stats, "tx_factor", (time.time() - t1) / dur)
    return lines, info.language, starts


_ocr = None
def ocr(images, prog=None, outdir=None):
    global _ocr
    import easyocr
    t0 = time.time()
    loaded_now = _ocr is None
    if _ocr is None:
        _ocr = easyocr.Reader(["en"], gpu=False, verbose=False)
    t1 = time.time()
    if prog:
        prog.rebase()
    seen, out = set(), []
    for k, img in enumerate(images, 1):
        if prog:
            prog.update((k - 1) / max(len(images), 1))
        texts = [t for _, t, conf in _ocr.readtext(str(img)) if conf > 0.4]
        line = " | ".join(texts).strip()
        if line and line.lower() not in seen:  # drop repeats across frames
            seen.add(line.lower())
            out.append((img.name, line))
    if prog and outdir and images:
        if loaded_now:
            learn(outdir, prog.stats, "ocr_load", t1 - t0)
        learn(outdir, prog.stats, "ocr_per_img", (time.time() - t1) / len(images))
    return out


NOTES_S, NOTES_E = "<!-- reels-hub:notes start -->", "<!-- reels-hub:notes end -->"


def old_notes(note):
    """The user's 'My notes' from an existing note.md, so Re-process keeps them."""
    if not note.exists():
        return ""
    t = note.read_text(encoding="utf-8", errors="replace")
    if NOTES_S in t and NOTES_E in t and t.find(NOTES_S) < t.find(NOTES_E):
        return t[t.find(NOTES_S) + len(NOTES_S):t.find(NOTES_E)].strip("\n")
    if "\n## My notes" in t:                                  # older notes: last section
        txt = t.split("\n## My notes", 1)[1].split("\n", 1)[1] if "\n" in t.split("\n## My notes", 1)[1] else ""
        return "" if txt.strip() == "-" else txt.strip("\n")
    return ""


def old_owner(note):
    """Who added this video (kept when it is re-processed)."""
    if not note.exists():
        return ""
    m = re.search(r"^owner:\s*(\S+)\s*$", note.read_text(encoding="utf-8", errors="replace")[:1500], re.M)
    return m.group(1) if m else ""


def write_note(workdir, url, info, shots, transcript, lang, ocr_lines, owner="", slides=None):
    author = info.get("uploader") or info.get("channel") or "unknown"
    caption = (info.get("description") or "").strip()
    date = info.get("upload_date", "")
    date = f"{date[:4]}-{date[4:6]}-{date[6:]}" if len(date) == 8 else ""
    platform = platform_of(url, info)
    title = (info.get("title") or "").strip().replace("\n", " ")
    md = [
        "---",
        f"source: {url}",
        f"platform: {platform}",
        f"owner: {old_owner(workdir / 'note.md') or owner or 'me'}",
        f"author: {author}",
        f"date: {date}",
        f"duration_s: {info.get('duration', '')}",
        *([f"slides: {len(slides)}"] if slides else []),
        f"tags: [reel, {platform.lower()}]",
        "---",
        f"# {title or info.get('id')}",
        "",
        "## Caption", caption or "_none_", "",
        f"## Transcript ({lang})" if transcript else "## Transcript",
        *(transcript or ["_photo slideshow, no speech_" if slides and all(s["kind"] == "image" for s in slides)
                         else "_no speech detected_"]), "",
    ]
    if ocr_lines is not None:
        md += ["## On-screen text", *(f"- `{n}`: {t}" for n, t in ocr_lines or [("-", "_none_")]), ""]
    note = workdir / "note.md"
    md += ["## Snapshots", *(f"![[{workdir.name}/shots/{s.name}]]" for s in shots), "",
           "## My notes", NOTES_S, old_notes(note), NOTES_E, ""]
    tmp = note.with_name(".note.md.tmp")
    tmp.write_text("\n".join(md), encoding="utf-8")
    os.replace(tmp, note)
    return note


# ---------------------------------------------------------------- photo slideshows (Instagram carousels)
# yt-dlp only downloads videos. For an Instagram post with several slides (or a single photo), the app asks
# yt-dlp what the post holds (-J --ignore-no-formats-error), downloads each photo slide's full image itself,
# downloads video slides with yt-dlp, and joins everything into one video.mp4 (a photo shows for SLIDE_SECS).
# The rest of the app then works as for any video: snapshots, favorites, sharing (the other laptop builds the
# same video, so favorite times match), print, Obsidian. slides.json says where each slide starts.
SLIDE_SECS = 4.0
FORMAT = "bv*[ext=mp4][height<=1080]+ba[ext=m4a]/b[ext=mp4][height<=1080]/bv*[height<=1080]+ba/b"


def _auth(browser, cookies):
    if cookies:
        return ["--cookies", cookies]
    if browser and browser != "none":
        return ["--cookies-from-browser", browser]
    return []


def probe_post(url, browser, cookies):
    """What an Instagram post holds, without downloading (yt-dlp -J). None if yt-dlp can't read it."""
    base = [sys.executable, "-m", "yt_dlp", "-J", "--ignore-no-formats-error", "--no-warnings"]
    auth = _auth(browser, cookies)
    for a in ([auth, []] if auth else [[]]):
        print("  > checking the post: " + " ".join(base[2:] + a + [url]))
        r = subprocess.run(base + a + [url], capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = (r.stdout or "").strip()
        if r.returncode == 0 and out.startswith("{"):
            try:
                return json.loads(out)
            except ValueError:
                pass
    return None


def post_items(post):
    """The slides of a post as yt-dlp sees them (a single post is one item)."""
    if post.get("_type") == "playlist":
        return [e for e in (post.get("entries") or []) if isinstance(e, dict)]
    return [post]


def is_slideshow(post):
    """Several slides, or a photo without video. A single video goes the normal way."""
    items = post_items(post) if post else []
    return bool(items) and (len(items) > 1 or not items[0].get("formats"))


def best_image(item):
    """Address of a slide's full image: the one without a size or crop in its name, else yt-dlp's main one."""
    thumbs = [t for t in (item.get("thumbnails") or []) if isinstance(t, dict) and t.get("url")]
    full = [t["url"] for t in thumbs if not re.search(r"[_.,=](?:s|p)\d+x\d+|stp=c\d", t["url"])]
    return (full[-1] if full else None) or item.get("thumbnail") or (thumbs[-1]["url"] if thumbs else None)


def fetch_image(url, dest, headers=None):
    """Download one slide image to dest (.jpg); other formats are converted with ffmpeg."""
    import urllib.request
    h = {k: v for k, v in (headers or {}).items() if k.lower() in ("user-agent", "accept-language")}
    h.setdefault("User-Agent", "Mozilla/5.0")
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    if len(raw) < 100:
        raise RuntimeError("an image of this post came back empty")
    if raw[:3] == b"\xff\xd8\xff":
        dest.write_bytes(raw)
        return
    tmp = dest.with_suffix(".src")
    tmp.write_bytes(raw)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), "-q:v", "2", str(dest)], check=False)
    tmp.unlink(missing_ok=True)
    if not dest.exists():
        raise RuntimeError("an image of this post could not be read")


def _size(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0:s=x", str(path)], capture_output=True, text=True)
    try:
        w, h = (int(x) for x in r.stdout.strip().split("x")[:2])
        return w, h
    except ValueError:
        return 1080, 1350


def _has_audio(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return bool(r.stdout.strip())


def _ffmpeg(cmd):
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + cmd,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("ffmpeg: " + (r.stderr or "").strip()[-400:])


def build_slideshow_video(workdir, slides):
    """Join the slides into video.mp4: photos SLIDE_SECS each, videos as they are, all on one canvas.
    Fills in each slide's start and dur (seconds). Sound only if a video slide has some."""
    sd = workdir / "slides"
    w, h = _size(sd / slides[0]["file"])
    if w > 1080:
        w, h = 1080, round(h * 1080 / w)
    w, h = w - w % 2, h - h % 2
    # photos only: 2 frames a second is plenty for still pictures and joins many times faster than 30
    fps = 30 if any(s["kind"] == "video" for s in slides) else 2
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
          f"setsar=1,fps={fps},format=yuv420p")
    sound = any(s["kind"] == "video" and _has_audio(sd / s["file"]) for s in slides)
    seg_dir = sd / "_seg"
    shutil.rmtree(seg_dir, ignore_errors=True)
    seg_dir.mkdir()
    venc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    aenc = ["-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "128k"] if sound else ["-an"]
    silence = ["-f", "lavfi", "-t", "{d}", "-i", "anullsrc=r=44100:cl=stereo"]
    t = 0.0
    for k, s in enumerate(slides):
        src, out = sd / s["file"], seg_dir / f"seg_{k:03d}.mp4"
        if s["kind"] == "image":
            d = f"{SLIDE_SECS:g}"
            cmd = ["-loop", "1", "-framerate", str(fps), "-t", d, "-i", str(src)]
            if sound:
                cmd += [x.replace("{d}", d) for x in silence] + ["-map", "0:v", "-map", "1:a", "-shortest"]
            cmd += ["-vf", vf] + venc + ["-tune", "stillimage"] + aenc + [str(out)]
        else:
            cmd = ["-i", str(src)]
            if sound and not _has_audio(src):
                d = f"{video_duration(src) or 1:.3f}"
                cmd += [x.replace("{d}", d) for x in silence] + ["-map", "0:v", "-map", "1:a", "-shortest"]
            elif sound:
                cmd += ["-map", "0:v:0", "-map", "0:a:0"]
            cmd += ["-vf", vf] + venc + aenc + [str(out)]
        _ffmpeg(cmd)
        dur = video_duration(out) or (SLIDE_SECS if s["kind"] == "image" else 0)
        s["start"], s["dur"] = round(t, 3), round(dur, 3)
        t += dur
    (seg_dir / "list.txt").write_text("".join(f"file 'seg_{k:03d}.mp4'\n" for k in range(len(slides))), encoding="utf-8")
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(seg_dir / "list.txt"), "-c", "copy",
             "-movflags", "+faststart", str(workdir / "video.mp4")])
    shutil.rmtree(seg_dir, ignore_errors=True)
    return workdir / "video.mp4"


def download_slideshow(url, post, workdir, browser, cookies, prog=None):
    """Download every slide of an Instagram post into workdir/slides and build video.mp4 from them.
    Writes video.info.json (the post's details, without the expiring image addresses) and slides.json."""
    items = post_items(post)
    n = len(items)
    print(f"  photo slideshow: {n} slide{'s' if n != 1 else ''}"
          f" ({sum(1 for e in items if e.get('formats'))} video)")
    sd = workdir / "slides"
    sd.mkdir(exist_ok=True)
    auth = _auth(browser, cookies)
    slides = []
    for k, e in enumerate(items, 1):
        if prog:
            prog.update((k - 1) / n)
        if e.get("formats"):                                  # a video slide: yt-dlp downloads it
            base = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--playlist-items", str(k), "-f", FORMAT,
                    "--merge-output-format", "mp4", "-o", str(sd / f"slide_{k:02d}.%(ext)s")]
            try:
                run_ytdlp(base + auth + [url], None)
            except YtdlpError:
                if not auth:
                    raise
                run_ytdlp(base + [url], None)
            f = next((p for p in sd.glob(f"slide_{k:02d}.*") if p.suffix.lower() in VIDEO_EXT), None)
            if not f:
                raise RuntimeError(f"slide {k} is a video but it did not download")
            slides.append({"n": k, "kind": "video", "file": f.name})
        else:                                                 # a photo: fetch the full image
            img = best_image(e)
            if not img:
                raise RuntimeError(f"slide {k} has no image address")
            dest = sd / f"slide_{k:02d}.jpg"
            fetch_image(img, dest, e.get("http_headers") or post.get("http_headers"))
            slides.append({"n": k, "kind": "image", "file": dest.name})
        print(f"  slide {k}/{n}: {slides[-1]['kind']}")
    print("  joining the slides into one video...")
    video = build_slideshow_video(workdir, slides)
    info = {k: v for k, v in post.items() if k not in (
        "entries", "formats", "thumbnails", "thumbnail", "requested_formats", "requested_downloads",
        "requested_subtitles", "comments", "http_headers", "url")}
    info["duration"] = round(sum(s["dur"] for s in slides), 3)
    info["slideshow"] = len(slides)
    (workdir / "video.info.json").write_text(json.dumps(info, indent=1, ensure_ascii=False), encoding="utf-8")
    (workdir / "slides.json").write_text(json.dumps({"secs": SLIDE_SECS, "slides": slides}, indent=1), encoding="utf-8")
    return video, info


def load_slides(workdir):
    try:
        d = json.loads((workdir / "slides.json").read_text(encoding="utf-8"))
        s = d.get("slides") if isinstance(d, dict) else None
        return s if isinstance(s, list) and s else None
    except (OSError, ValueError):
        return None


def slide_shots(workdir, slides, max_shots=0, every=0):
    """Snapshots of a slideshow: each photo slide's full image (time = middle of the slide), and for video
    slides a frame every few seconds (as Auto would). A fixed maximum thins the video frames only."""
    shots = workdir / "shots"
    shots.mkdir(exist_ok=True)
    sd = workdir / "slides"
    photos, frames = [], []
    for s in slides:
        if s["kind"] == "image":
            photos.append((s["start"] + s["dur"] / 2, sd / s["file"], True))
        else:
            tmp = shots / f"_v{s['n']:02d}"
            tmp.mkdir(exist_ok=True)
            interval = every_interval(s["dur"], every) if every else auto_plan(s["dur"], 0)[0]
            for t, f in _grid(sd / s["file"], tmp, interval):
                frames.append((s["start"] + t, f, False))
    if max_shots > 0 and not every and len(photos) + len(frames) > max_shots and frames:
        room = max(1, max_shots - len(photos))
        if room < len(frames):
            idx = {round(i * (len(frames) - 1) / max(room - 1, 1)) for i in range(room)}
            frames = [f for i, f in enumerate(frames) if i in idx]
    files, times = [], []
    for k, (t, f, copy) in enumerate(sorted(photos + frames, key=lambda x: x[0]), 1):
        dest = shots / f"shot_{k:03d}.jpg"
        if copy:
            shutil.copy2(f, dest)
        else:
            f.replace(dest)
        files.append(dest); times.append(round(t, 2))
    for d in shots.glob("_v*"):
        shutil.rmtree(d, ignore_errors=True)
    print(f"  {len(files)} snapshots ({len(photos)} photo slides"
          f"{f' + {len(frames)} frames from video slides' if frames else ''})")
    (workdir / "shots.json").write_text(json.dumps(dict(zip([f.name for f in files], times)), indent=1),
                                        encoding="utf-8")
    return files


def new_version(out, workdir, video, info):
    """Copy the downloaded video into <date>_<id>_v2 (v3, ...) so it can be processed again separately."""
    base = f"{info.get('upload_date', '')}_{info['id']}"
    k = 2
    while (out / f"{base}_v{k}").exists():
        k += 1
    dest = out / f"{base}_v{k}"
    dest.mkdir()
    files = [video, workdir / "video.info.json"]
    if (workdir / "slides.json").exists():              # a slideshow: its slide images and videos too
        (dest / "slides").mkdir()
        files += [workdir / "slides.json"] + [f for f in (workdir / "slides").iterdir() if f.is_file()]
    for f in files:
        rel = f.relative_to(workdir)
        try:
            os.link(f, dest / rel)              # hard link: no extra disk space
        except OSError:
            shutil.copy2(f, dest / rel)
    return dest, dest / video.name


def fmt_dur(sec):
    sec = int(round(sec))
    return f"{sec // 60}m {sec % 60:02d}s" if sec >= 60 else f"{sec}s"


def find_existing(out, url, target=None):
    """Return (workdir, video, info) if this post was already downloaded (or the given folder)."""
    if target:
        folder = Path(out) / target
        try:
            info = json.loads((folder / "video.info.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise RuntimeError(f"reel folder not found: {target}")
        video = find_video(folder)
        if not video:
            raise RuntimeError(f"no video file in {target}")
        return folder, video, info
    for info_file in sorted(Path(out).glob("*/video.info.json")):   # sorted: the original before _v2, _v3
        if info_file.parent.name.startswith("_"):
            continue
        try:
            info = json.loads(info_file.read_text(encoding="utf-8"))
        except ValueError:
            continue
        same = (info.get("id") and info["id"] in url) or url.rstrip("/") in (
            str(info.get("webpage_url", "")).rstrip("/"), str(info.get("original_url", "")).rstrip("/"))
        video = find_video(info_file.parent)
        if same and video:
            return info_file.parent, video, info
    return None


CURRENT_STEP = [None]


def process(url, args, i=1, n=1, stats=None):
    """Run one video; on error, say which step failed."""
    CURRENT_STEP[0] = None
    try:
        return _process(url, args, i, n, stats)
    except Exception as e:
        step = Progress.LABEL.get(CURRENT_STEP[0], CURRENT_STEP[0])
        raise RuntimeError(f"{step} failed - {e}" if step else str(e)) from e


def _process(url, args, i=1, n=1, stats=None):
    print(f"\n== {url}")
    prog = Progress(i, n, stats or load_stats(args.out), not args.no_ocr, args.max_shots, args.every)
    existing = find_existing(args.out, url, args.target)
    if existing:
        workdir, video, info = existing
        if (workdir / "note.md").exists() and args.new_version:
            workdir, video = new_version(Path(args.out), workdir, video, info)
            print(f"  already downloaded, making a new version: {workdir.name}")
        elif (workdir / "note.md").exists() and not args.redo:
            print("  already done, skipping (use New version or Re-process to run it again)")
            print("@@DONE " + json.dumps({"url": url, "folder": workdir.name, "skipped": True}), flush=True)
            return
        else:
            print("  already downloaded, re-processing" if args.redo else "  already downloaded, resuming")
    else:
        tmp = Path(args.out) / "_tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        prog.start("download")
        post = probe_post(url, args.browser, args.cookies) if platform_of(url) == "Instagram" else None
        if post and is_slideshow(post):
            video, info = download_slideshow(url, post, tmp, args.browser, args.cookies, prog)
        else:
            video, info = download(url, tmp, args.browser, args.cookies, prog)
        workdir = Path(args.out) / f"{info.get('upload_date', '')}_{info['id']}"
        tmp.rename(workdir)
        video = workdir / video.name
    shots_dir = workdir / "shots"
    if shots_dir.exists():
        shutil.rmtree(shots_dir)                             # also removes edited originals in shots/_orig
    (workdir / "shots_manual.json").unlink(missing_ok=True)  # your own snapshots are gone with the old set
    print(f"  platform: {platform_of(url, info)} - {info.get('title') or info.get('id')}")
    slides = load_slides(workdir)
    if slides:
        return _process_slides(url, args, workdir, video, info, slides, prog)
    dur = video_duration(video) or float(info.get("duration") or 0)
    prog.plan(dur)
    est = sum(prog.est.values())
    print(f"  video length {int(dur)//60}:{int(dur)%60:02d} - estimated processing time about {fmt_dur(est)}")
    # transcript first, so snapshots can be taken where each sentence starts
    prog.start("transcribe")
    transcript, lang, starts = transcribe(video, args.model, args.lang, prog, args.out)
    prog.start("snapshots")
    t0 = time.time()
    shots = snapshots(video, workdir, args.scene, args.max_shots, starts, prog, args.every)
    if dur > 5:
        learn(args.out, prog.stats, "shot_factor", (time.time() - t0) / dur)
    prog.plan(dur, len(shots))
    ocr_lines = None
    if not args.no_ocr:
        prog.start("ocr")
        step = max(1, -(-len(shots) // args.ocr_max))       # read text from at most --ocr-max frames
        ocr_lines = ocr(shots[::step], prog, args.out)
    note = write_note(workdir, url, info, shots, transcript, lang, ocr_lines, args.owner)
    print(f"  done in {fmt_dur(time.time() - prog.t0)} -> {note}")
    # for Reels Hub: which folder this link went to (an Add from the inbox fills in that reel next)
    print("@@DONE " + json.dumps({"url": url, "folder": workdir.name}), flush=True)


def _process_slides(url, args, workdir, video, info, slides, prog):
    """The rest of a slideshow: transcript only if a slide is a video, snapshots from the slides, OCR, note."""
    dur = sum(float(s.get("dur") or 0) for s in slides) or video_duration(video)
    videos = [s for s in slides if s["kind"] == "video"]
    prog.plan(sum(float(s["dur"]) for s in videos), len(slides))
    if not videos:
        prog.est["transcribe"] = prog.est["snapshots"] = 0
    print(f"  {len(slides)} slides, {int(dur)//60}:{int(dur)%60:02d} as a video"
          f" - estimated processing time about {fmt_dur(sum(prog.est.values()))}")
    transcript, lang = [], "-"
    if videos:
        prog.start("transcribe")
        transcript, lang, _ = transcribe(video, args.model, args.lang, prog, args.out)
    prog.start("snapshots")
    shots = slide_shots(workdir, slides, args.max_shots, args.every)
    prog.plan(sum(float(s["dur"]) for s in videos), len(shots))
    ocr_lines = None
    if not args.no_ocr:
        prog.start("ocr")
        step = max(1, -(-len(shots) // args.ocr_max))
        ocr_lines = ocr(shots[::step], prog, args.out)
    note = write_note(workdir, url, info, shots, transcript, lang, ocr_lines, args.owner, slides)
    print(f"  done in {fmt_dur(time.time() - prog.t0)} -> {note}")
    print("@@DONE " + json.dumps({"url": url, "folder": workdir.name}), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("urls", nargs="*")
    ap.add_argument("-f", "--file")
    ap.add_argument("--browser", default="none")
    ap.add_argument("--cookies", help="cookies.txt exported from your browser")
    ap.add_argument("--scene", type=float, default=0.3)
    ap.add_argument("--max-shots", default="auto", help="'auto', a number, or every:N (one snapshot every N seconds)")
    ap.add_argument("--ocr-max", type=int, default=60)
    ap.add_argument("--model", default="small")
    ap.add_argument("--lang", default=None)
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--owner", default="", help="who adds the videos (set by Reels Hub from its settings)")
    ap.add_argument("--redo", action="store_true", help="re-process videos that were already done (replaces them)")
    ap.add_argument("--new-version", action="store_true", help="process already-done videos again as a new copy (_v2, _v3)")
    ap.add_argument("--target", help="reel folder name to use (with --redo or --new-version)")
    ap.add_argument("--out", default=str(Path.home() / "Downloads" / "reels"))
    args = ap.parse_args()
    ms = str(args.max_shots).strip().lower()
    args.every = 0.0
    if ms.startswith("every:"):                                          # a snapshot every N seconds
        try:
            args.every = min(600.0, max(0.5, float(ms.split(":", 1)[1])))
        except ValueError:
            ap.error("--max-shots every:N needs a number of seconds, e.g. every:5")
        args.max_shots = 0
    else:
        args.max_shots = 0 if ms in ("auto", "0", "") else max(10, int(ms))   # 0 = Auto

    urls = list(args.urls)
    if args.file:
        urls += [l.strip() for l in Path(args.file).read_text(encoding="utf-8-sig", errors="replace").splitlines() if l.strip().startswith("http")]
    if not urls:
        ap.error("give at least one URL or -f links.txt")

    failed = []
    stats = load_stats(args.out)
    for i, url in enumerate(urls):
        try:
            process(url, args, i + 1, len(urls), stats)
        except Exception as e:
            print(f"  FAILED: {e}"); failed.append(url)
        if i < len(urls) - 1:
            time.sleep(5)  # be gentle with the sites
    if failed:
        Path(args.out, "failed.txt").write_text("\n".join(failed), encoding="utf-8")
        print(f"\n{len(failed)} failed - see {args.out}/failed.txt")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
