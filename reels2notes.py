#!/usr/bin/env python3
"""
reels2notes.py - download Instagram reels, TikTok videos, YouTube videos/Shorts and X posts and turn
each into a Markdown note with caption, spoken transcript, on-screen text (OCR) and snapshots.

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
    --max-shots 200                 max snapshots per video: one every 2 s plus scene cuts (long videos: spread out)
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

    def __init__(self, i, n, stats, do_ocr, max_shots):
        self.i, self.n, self.stats, self.do_ocr, self.max_shots = i, n, stats, do_ocr, max_shots
        self.t0 = time.time()
        self.est = {}            # estimated seconds per remaining stage
        self.stage, self.frac, self.base = None, 0.0, time.time()
        self.last_emit = 0
        self.dl_eta = None

    def plan(self, duration, nshots=None):
        st = self.stats
        d = max(duration or 0, 1)
        n = nshots if nshots is not None else min(self.max_shots, max(3, int(d / 2)))
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


def run_ytdlp(cmd, prog):
    """Run yt-dlp, show its normal output, and turn its progress lines into progress updates."""
    print("  >", " ".join(map(str, cmd)))
    proc = subprocess.Popen(cmd + ["--newline"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    pat = re.compile(r"\[download\]\s+([\d.]+)%.*?(?:ETA\s+(\S+))?\s*$")
    for line in proc.stdout:
        line = line.rstrip()
        m = pat.search(line)
        if m:
            if prog:
                prog.update(float(m.group(1)) / 100, _secs(m.group(2) or ""))
            if not line.startswith("[download] 100"):
                continue                      # don't flood the log with progress lines
        print(line, flush=True)
    if proc.wait() != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)


def run(cmd):
    print("  >", " ".join(map(str, cmd)))
    return subprocess.run(cmd, check=True)


def download(url, workdir, browser, cookies, prog=None):
    base = [sys.executable, "-m", "yt_dlp", "--write-info-json", "--no-playlist",
            "--playlist-items", "1",   # posts with several videos (e.g. on X): take the first
            # browser-playable MP4, max 1080p (YouTube serves separate video/audio streams)
            "-f", "bv*[ext=mp4][height<=1080]+ba[ext=m4a]/b[ext=mp4][height<=1080]/bv*[height<=1080]+ba/b",
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
    except subprocess.CalledProcessError:
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


def snapshots(video, workdir, scene, max_shots=200, speech=(), prog=None):
    """Dense snapshots: a frame every 2 s (spread out more for long videos) plus every scene cut."""
    shots = workdir / "shots"
    shots.mkdir(exist_ok=True)
    dur = video_duration(video)
    interval = max(2.0, dur / max_shots) if dur else 2.0
    grid = _grid(video, shots, interval)
    if prog:
        prog.update(0.6)
    cuts = _spread(scene_cuts(video, scene), max(1, max_shots // 4), dur) if dur else []
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
    frames = sorted(grid + extra, key=lambda x: x[0])
    files, times = [], []
    for k, (t, f) in enumerate(frames, 1):                   # rename in time order: shot_001.jpg ...
        dest = shots / f"shot_{k:03d}.jpg"
        f.replace(dest)
        files.append(dest); times.append(round(t, 2))
    print(f"  {len(files)} snapshots (every {interval:.0f} s + {len(extra)} scene cuts)")
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


def write_note(workdir, url, info, shots, transcript, lang, ocr_lines):
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
        f"author: {author}",
        f"date: {date}",
        f"duration_s: {info.get('duration', '')}",
        f"tags: [reel, {platform.lower()}]",
        "---",
        f"# {title or info.get('id')}",
        "",
        "## Caption", caption or "_none_", "",
        f"## Transcript ({lang})" if transcript else "## Transcript",
        *(transcript or ["_no speech detected_"]), "",
    ]
    if ocr_lines is not None:
        md += ["## On-screen text", *(f"- `{n}`: {t}" for n, t in ocr_lines or [("-", "_none_")]), ""]
    md += ["## Snapshots", *(f"![[{workdir.name}/shots/{s.name}]]" for s in shots), "",
           "## My notes", "- "]
    note = workdir / "note.md"
    note.write_text("\n".join(md), encoding="utf-8")
    return note


def new_version(out, workdir, video, info):
    """Copy the downloaded video into <date>_<id>_v2 (v3, ...) so it can be processed again separately."""
    base = f"{info.get('upload_date', '')}_{info['id']}"
    k = 2
    while (out / f"{base}_v{k}").exists():
        k += 1
    dest = out / f"{base}_v{k}"
    dest.mkdir()
    for f in (video, workdir / "video.info.json"):
        try:
            os.link(f, dest / f.name)           # hard link: no extra disk space
        except OSError:
            shutil.copy2(f, dest / f.name)
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
    prog = Progress(i, n, stats or load_stats(args.out), not args.no_ocr, args.max_shots)
    existing = find_existing(args.out, url, args.target)
    if existing:
        workdir, video, info = existing
        if (workdir / "note.md").exists() and args.new_version:
            workdir, video = new_version(Path(args.out), workdir, video, info)
            print(f"  already downloaded, making a new version: {workdir.name}")
        elif (workdir / "note.md").exists() and not args.redo:
            print("  already done, skipping (use New version or Re-process to run it again)")
            return
        else:
            print("  already downloaded, re-processing" if args.redo else "  already downloaded, resuming")
    else:
        tmp = Path(args.out) / "_tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        prog.start("download")
        video, info = download(url, tmp, args.browser, args.cookies, prog)
        workdir = Path(args.out) / f"{info.get('upload_date', '')}_{info['id']}"
        tmp.rename(workdir)
        video = workdir / video.name
    shots_dir = workdir / "shots"
    if shots_dir.exists():
        shutil.rmtree(shots_dir)
    print(f"  platform: {platform_of(url, info)} - {info.get('title') or info.get('id')}")
    dur = video_duration(video) or float(info.get("duration") or 0)
    prog.plan(dur)
    est = sum(prog.est.values())
    print(f"  video length {int(dur)//60}:{int(dur)%60:02d} - estimated processing time about {fmt_dur(est)}")
    # transcript first, so snapshots can be taken where each sentence starts
    prog.start("transcribe")
    transcript, lang, starts = transcribe(video, args.model, args.lang, prog, args.out)
    prog.start("snapshots")
    t0 = time.time()
    shots = snapshots(video, workdir, args.scene, args.max_shots, starts, prog)
    if dur > 5:
        learn(args.out, prog.stats, "shot_factor", (time.time() - t0) / dur)
    prog.plan(dur, len(shots))
    ocr_lines = None
    if not args.no_ocr:
        prog.start("ocr")
        step = max(1, -(-len(shots) // args.ocr_max))       # read text from at most --ocr-max frames
        ocr_lines = ocr(shots[::step], prog, args.out)
    note = write_note(workdir, url, info, shots, transcript, lang, ocr_lines)
    print(f"  done in {fmt_dur(time.time() - prog.t0)} -> {note}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("urls", nargs="*")
    ap.add_argument("-f", "--file")
    ap.add_argument("--browser", default="none")
    ap.add_argument("--cookies", help="cookies.txt exported from your browser")
    ap.add_argument("--scene", type=float, default=0.3)
    ap.add_argument("--max-shots", type=int, default=200)
    ap.add_argument("--ocr-max", type=int, default=60)
    ap.add_argument("--model", default="small")
    ap.add_argument("--lang", default=None)
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--redo", action="store_true", help="re-process videos that were already done (replaces them)")
    ap.add_argument("--new-version", action="store_true", help="process already-done videos again as a new copy (_v2, _v3)")
    ap.add_argument("--target", help="reel folder name to use (with --redo or --new-version)")
    ap.add_argument("--out", default=str(Path.home() / "Downloads" / "reels"))
    args = ap.parse_args()

    urls = list(args.urls)
    if args.file:
        urls += [l.strip() for l in Path(args.file).read_text().splitlines() if l.strip().startswith("http")]
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
        Path(args.out, "failed.txt").write_text("\n".join(failed))
        print(f"\n{len(failed)} failed - see {args.out}/failed.txt")


if __name__ == "__main__":
    sys.exit(main())
