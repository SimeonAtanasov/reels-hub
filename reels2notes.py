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
    python reels2notes.py --transcripts https://www.youtube.com/@name/videos   # transcripts of a whole channel
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
    --transcripts                   transcripts only, no video: each link is a video, a YouTube channel or a playlist;
                                    YouTube captions (creator's, else automatic), Whisper where there are none;
                                    one folder per video in <out>/_transcripts/<channel>/ (Reels Hub makes the notes)
Graphics card: Whisper and the on-screen text use an NVIDIA card when one works (same models, same text),
    else the processor. REELS_HUB_CPU=1 forces the processor. Setup: README "Use the graphics card".
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


# ---------------------------------------------------------------- graphics card (NVIDIA)
# Whisper (CTranslate2) and easyocr (PyTorch) run on an NVIDIA card when it works, else on the processor.
# Same models and full-size images either way. Any error on the card: that step goes on with the processor.
# REELS_HUB_CPU=1 forces the processor. GTX 10 cards need PyTorch 2.14 with CUDA 12.6 (README).
DEV = {}                       # {"whisper": "cuda"|"cpu", "ocr": ...}, decided once per run


def gpu_off():
    return os.environ.get("REELS_HUB_CPU", "").strip() not in ("", "0")


def skey(key, device):
    """Speed-profile key: the graphics card's speeds are learned apart from the processor's."""
    return key + "_gpu" if device == "cuda" else key


def _short(e):
    return (str(e).strip().splitlines() or [type(e).__name__])[0][:160]


_DLLS_DONE = []
def _cuda_dlls():
    """Windows: let CTranslate2 (Whisper) find the CUDA libraries that come with PyTorch (torch\\lib)
    or with the nvidia-cublas-cu12 / nvidia-cudnn-cu12 packages."""
    if _DLLS_DONE or os.name != "nt":
        return
    _DLLS_DONE.append(1)
    import importlib.util, site
    dirs = []
    try:
        spec = importlib.util.find_spec("torch")           # finds torch without loading it
        if spec and spec.submodule_search_locations:
            dirs.append(Path(list(spec.submodule_search_locations)[0]) / "lib")
    except (ImportError, ValueError):
        pass
    try:
        roots = site.getsitepackages() + [site.getusersitepackages()]
    except AttributeError:
        roots = []
    for r in roots:
        dirs += sorted(Path(r, "nvidia").glob("*/bin"))
    for d in dirs:
        if d.is_dir():
            try:
                os.add_dll_directory(str(d))
            except OSError:
                pass
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


def whisper_device():
    if "whisper" not in DEV:
        DEV["whisper"] = "cpu"
        if not gpu_off():
            _cuda_dlls()
            try:
                import ctranslate2
                if ctranslate2.get_cuda_device_count() > 0:
                    DEV["whisper"] = "cuda"
            except Exception:
                pass
    return DEV["whisper"]


def ocr_device():
    if "ocr" not in DEV:
        DEV["ocr"] = "cpu"
        if not gpu_off():
            _cuda_dlls()
            try:
                import torch
                if torch.cuda.is_available():
                    DEV["ocr"] = "cuda"
            except Exception:
                pass
    return DEV["ocr"]


def gpu_name():
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
    except Exception:
        pass
    return "NVIDIA"


# ---------------------------------------------------------------- progress / ETA
# Lines starting with "@@PROGRESS " carry JSON for Reels Hub; it shows them as a progress bar.
DEFAULT_STATS = {"tx_factor": 0.35, "model_load": 6.0, "ocr_per_img": 1.5, "ocr_load": 8.0, "shot_factor": 0.03,
                 # the same, measured on the graphics card (learned separately, so the ETA fits either way)
                 "tx_factor_gpu": 0.1, "model_load_gpu": 5.0, "ocr_per_img_gpu": 0.4, "ocr_load_gpu": 8.0,
                 "cap_secs": 8.0}                                  # transcripts only: seconds per video (captions)


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
        self.times, self.stage_t0 = {}, time.time()     # seconds per step, for the log

    STEP_NAME = {"download": "download", "transcribe": "transcript", "snapshots": "snapshots", "ocr": "on-screen text"}

    def steps(self):
        """'download 3s, transcript 52s, snapshots 12s, on-screen text 38s' (the current step counted so far)."""
        t = dict(self.times)
        if self.stage:
            t[self.stage] = t.get(self.stage, 0) + time.time() - self.stage_t0
        return ", ".join(f"{self.STEP_NAME.get(k, k)} {fmt_dur(v)}" for k, v in t.items())

    def plan(self, duration, nshots=None):
        st = self.stats
        d = max(duration or 0, 1)
        cap = self.max_shots or AUTO_CAP
        if nshots is None and self.every:
            nshots = min(EVERY_CAP, int(d / self.every) + 1)
        n = nshots if nshots is not None else min(cap, max(3, int(d / min(20.0, max(2.0, 2.0 * (d / 60) ** 0.5)))))
        self.est["snapshots"] = 2 + d * st["shot_factor"]
        wd = whisper_device()
        od = ocr_device() if self.do_ocr else "cpu"
        self.est["transcribe"] = st[skey("model_load", wd)] + d * st[skey("tx_factor", wd)]
        self.est["ocr"] = (st[skey("ocr_load", od)] * (_ocr is None or _ocr_dev != od)
                           + min(n, 60) * st[skey("ocr_per_img", od)]) if self.do_ocr else 0
        self.planned = True

    def start(self, stage):
        CURRENT_STEP[0] = stage
        now = time.time()
        if self.stage:
            self.times[self.stage] = self.times.get(self.stage, 0) + now - self.stage_t0
        self.stage_t0 = now
        self.stage, self.frac, self.base = stage, 0.0, now
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
    # decode audio with ffmpeg (16 kHz mono float32) - avoids PyAV version issues
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-vn",
                          "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True).stdout  # no audio -> empty
    audio = np.frombuffer(raw, dtype=np.float32)
    if audio.size == 0:
        return [], lang or "-", []
    dur = audio.size / 16000
    device = whisper_device()
    if device == "cuda":
        print(f"  transcript on the graphics card ({gpu_name()})")
        try:
            return _whisper(audio, dur, model_name, "cuda", [model_name], lang, prog, out)
        except Exception as e:                # any trouble on the card: the processor does it, same model
            print(f"  the graphics card did not work for the transcript ({_short(e)}) - using the processor")
            DEV["whisper"] = "cpu"
            if prog:
                prog.update(0)
    # Low RAM: fall back to smaller models instead of failing (small -> base -> tiny)
    sizes = ["large-v3", "medium", "small", "base", "tiny"]
    chain = [model_name] + [m for m in sizes[sizes.index(model_name) + 1:]] if model_name in sizes else [model_name, "base", "tiny"]
    return _whisper(audio, dur, model_name, "cpu", chain, lang, prog, out)


def _whisper(audio, dur, model_name, device, chain, lang, prog, out):
    from faster_whisper import WhisperModel
    for attempt, name in enumerate(chain):
        model = None
        try:
            t0 = time.time()
            model = WhisperModel(name, device=device, compute_type="int8",   # int8 works on GTX 10 cards too
                                 cpu_threads=min(4, os.cpu_count() or 4))
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
            if device != "cpu" or not low_mem or attempt == len(chain) - 1:
                raise
            print(f"  not enough memory for the '{name}' Whisper model ({e}) - retrying with '{chain[attempt + 1]}'")
    if prog and out and name == model_name:
        stats = prog.stats
        learn(out, stats, skey("model_load", device), t1 - t0)
        if dur > 5:
            learn(out, stats, skey("tx_factor", device), (time.time() - t1) / dur)
    return lines, info.language, starts


_ocr = None
_ocr_dev = None
def _ocr_reader(device):
    global _ocr, _ocr_dev
    if _ocr is None or _ocr_dev != device:
        import easyocr
        _ocr = None
        _ocr = easyocr.Reader(["en"], gpu=(device == "cuda"), verbose=False)
        _ocr_dev = device
    return _ocr


def ocr(images, prog=None, outdir=None):
    """On-screen text of each image, at full size. On the graphics card when it works (same model, same text)."""
    t0 = time.time()
    device = ocr_device()
    loaded_now = _ocr is None or _ocr_dev != device
    switched = False

    def to_cpu(e):
        nonlocal device, switched
        print(f"  the graphics card did not work for the on-screen text ({_short(e)}) - using the processor")
        DEV["ocr"] = device = "cpu"
        switched = True
        return _ocr_reader("cpu")

    if device == "cuda":
        print(f"  on-screen text on the graphics card ({gpu_name()})")
        try:
            reader = _ocr_reader("cuda")
        except Exception as e:
            reader = to_cpu(e)
    else:
        reader = _ocr_reader("cpu")
    t1 = time.time()
    if prog:
        prog.rebase()
    seen, out = set(), []
    for k, img in enumerate(images, 1):
        if prog:
            prog.update((k - 1) / max(len(images), 1))
        try:
            found = reader.readtext(str(img))
        except Exception as e:
            if device == "cpu":
                raise
            reader = to_cpu(e)                 # this image again on the processor: nothing is skipped
            found = reader.readtext(str(img))
        texts = [t for _, t, conf in found if conf > 0.4]
        line = " | ".join(texts).strip()
        if line and line.lower() not in seen:  # drop repeats across frames
            seen.add(line.lower())
            out.append((img.name, line))
    if prog and outdir and images and not switched:
        if loaded_now:
            learn(outdir, prog.stats, skey("ocr_load", device), t1 - t0)
        learn(outdir, prog.stats, skey("ocr_per_img", device), (time.time() - t1) / len(images))
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



def replace_retry(src, dst, tries=60):
    """os.replace that waits while Windows has the target open for a moment (Reels Hub reading _channel.json for
    the Transcripts tab, an antivirus or indexer): up to about 6 s, then the error as before."""
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.05 if i < 20 else 0.2)


def write_note(workdir, url, info, shots, transcript, lang, ocr_lines, owner="", slides=None, extra=()):
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
        *extra,
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
    replace_retry(tmp, note)
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
        t0 = time.time()
        r = subprocess.run(base + a + [url], capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = (r.stdout or "").strip()
        print(f"  post checked in {fmt_dur(time.time() - t0)}")
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


def channel_category(out, workdir, info, url):
    """A new video starts in its channel's home category when you gave the channel one in Reels Hub
    (_categories.json "homes" {channel key: category}, the key as tr_key); otherwise in Unassigned, where the app
    shows it under its channel. Written before the note so the app and the vault note see it at once. Only for
    videos without hub.json (new downloads). Since 2026-11-09 no category is made per channel any more."""
    f = out / "_categories.json"
    try:
        raw = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except (OSError, ValueError):
        return                                               # unreadable: leave it to the app
    homes, cats = raw.get("homes"), raw.get("categories") or []
    if not isinstance(homes, dict) or not homes:
        return
    key = tr_key(info, url).casefold()
    cat = next((v for k, v in homes.items() if str(k).casefold() == key), "")
    if not cat or cat not in cats:
        return
    (workdir / "hub.json").write_text(json.dumps({"category": cat, "favorites": []}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"  category: {cat} (the channel's)")


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
    if args.channel_category and not (workdir / "hub.json").exists():
        channel_category(Path(args.out), workdir, info, url)
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
    print(f"  done in {fmt_dur(time.time() - prog.t0)} ({prog.steps()}) -> {note}")
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
    print(f"  done in {fmt_dur(time.time() - prog.t0)} ({prog.steps()}) -> {note}")
    print("@@DONE " + json.dumps({"url": url, "folder": workdir.name}), flush=True)

# ---------------------------------------------------------------- transcripts only (videos and whole channels)
# `--transcripts`: no video download, no snapshots, no on-screen text. Each link is a video, or a YouTube channel or
# playlist (every video in it). The transcript comes from YouTube's captions: the creator's subtitles in the video's
# own language, else the automatic ones (the untranslated "xx-orig" track). A video without captions, or on another
# site, is transcribed with Whisper from its audio only. Each video gets a small folder
# <out>/_transcripts/<channel>/<date>_<id>/ (note.md as for a reel, video.info.json trimmed), which Reels Hub turns
# into an Obsidian note and lists in its Transcripts catalogue. A video that is already there, or already in the
# library as a full reel, is left alone.
TR_DIR = "_transcripts"
TR_PAUSE = 1.5                       # seconds between videos, on top of yt-dlp's own waits (--sleep-requests)
TR_SUB_SLEEP = 2                     # seconds yt-dlp waits before each caption download (YouTube limits these)
TR_RETRY_WAIT = float(os.environ.get("REELS_HUB_RETRY_WAIT", 60))   # after "429 Too Many Requests": wait, then try once more
INFO_KEEP = ("id", "title", "description", "upload_date", "timestamp", "duration", "uploader", "uploader_id",
             "uploader_url", "channel", "channel_id", "channel_url", "channel_follower_count", "webpage_url",
             "original_url", "extractor_key", "extractor", "language", "view_count", "like_count", "tags",
             "categories", "thumbnail", "chapters", "availability", "live_status", "playable_in_embed")
AUDIO_FORMAT = "ba[ext=m4a]/ba/b"    # for Whisper: the audio only (the video would be downloaded for nothing)
CONTROL = "_control.json"            # in _transcripts: {"pause": bool, "stop": bool}, written by Reels Hub's Pause / Stop


def yt_video_id(url):
    """The id of a YouTube video link (watch, youtu.be, shorts, live, embed), else None."""
    m = re.search(r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|live/|embed/|v/)|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def video_id_of(url):
    """The video id a link carries, as yt-dlp names it: YouTube, TikTok (/video/<n>), Instagram (/p|reel/<code>),
    X (/status/<n>); else None."""
    v = yt_video_id(url)
    if v:
        return v
    for pat in (r"tiktok\.com/.*/video/(\d+)", r"instagram\.com/(?:[^/]+/)?(?:p|reels?|tv)/([A-Za-z0-9_-]+)",
                r"(?:x|twitter)\.com/[^/]+/status/(\d+)"):
        m = re.search(pat, url, re.I)
        if m:
            return m.group(1)
    return None


def is_listing(url):
    """Many videos in one link: a YouTube channel or playlist, or a TikTok profile (tiktok.com/@name)."""
    u = url.split("#")[0]
    if video_id_of(u):
        return False
    if re.search(r"youtube\.com/(@[^/?]+|channel/|c/|user/|playlist\?)", u, re.I):
        return True
    return bool(re.search(r"tiktok\.com/@[\w.-]+/?(?:$|[?])", u, re.I))


def listing_url(url):
    """A bare channel link lists every upload, Shorts and live streams included: take its Videos tab unless a tab
    is given (youtube.com/@name/shorts lists the Shorts)."""
    u = url.split("#")[0].rstrip("/")
    if re.search(r"youtube\.com/(@[^/?]+|channel/[^/?]+|c/[^/?]+|user/[^/?]+)$", u, re.I):
        return u + "/videos"
    return u


def _ytdlp_quiet(cmd):
    """Run yt-dlp without showing its output; on failure raise its error line made readable."""
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        err = next((l for l in (proc.stderr + "\n" + proc.stdout).splitlines() if l.startswith("ERROR:")), "")
        raise YtdlpError(friendly_error(err) if err else f"yt-dlp stopped (exit code {proc.returncode})")
    return proc.stdout


def list_videos(url, browser, cookies):
    """The videos of a channel tab or playlist as YouTube lists them (newest first): (listing, [entries])."""
    cmd = [sys.executable, "-m", "yt_dlp", "-J", "--flat-playlist", "--no-warnings", "--sleep-requests", "0.75"]
    out = _ytdlp_quiet(cmd + _auth(browser, cookies) + [url])
    try:
        data = json.loads(out)
    except ValueError:
        raise RuntimeError("yt-dlp did not return the channel's list")
    entries = []
    for e in data.get("entries") or []:
        if not isinstance(e, dict) or not e.get("id"):
            continue
        if e.get("_type") == "playlist" or (e.get("ie_key") or "").endswith("Tab"):
            continue                                        # a tab or a playlist inside the list, not a video
        if not e.get("url") and (e.get("ie_key") or "Youtube") != "Youtube":
            continue
        entries.append({"id": str(e["id"]), "url": e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
                        "title": (e.get("title") or "").strip(), "duration": e.get("duration")})
    return data, entries


def _handle(info, url=""):
    """The creator's handle as yt-dlp reports it per site: YouTube uploader_id (@name), TikTok uploader (name),
    Instagram channel (username), X uploader_id (screen name); else what the link or the title says."""
    plat = platform_of(url, info)
    order = {"YouTube": ("uploader_id", "channel", "channel_id"), "TikTok": ("uploader", "channel", "uploader_id"),
             "Instagram": ("channel", "uploader", "uploader_id"), "X": ("uploader_id", "uploader", "channel")}
    for k in order.get(plat, ("uploader_id", "uploader", "channel", "channel_id")):
        v = str(info.get(k) or "").strip()
        if v and not (k == "uploader_id" and plat != "X" and v.isdigit()):   # a numeric uploader_id is not a handle
            return v
    m = re.search(r"/@([\w.-]+)", url or "")
    return m.group(1) if m else str(info.get("title") or "")


def tr_key(info, url=""):
    """Folder name of a video's channel in _transcripts: the handle without the @, made safe for a folder."""
    key = re.sub(r"[^0-9A-Za-z._-]+", "_", _handle(info, url).lstrip("@")).strip("._-")[:60]
    return key or "channel"


def tr_name(info, url=""):
    """The channel as shown: YouTube's channel name, TikTok's nickname, Instagram's full name, X's display name."""
    plat = platform_of(url, info)
    for k in {"Instagram": ("uploader", "channel"), "X": ("uploader", "uploader_id")}.get(plat, ("channel", "uploader", "uploader_id")):
        v = str(info.get(k) or "").strip()
        if v and not v.isdigit():
            return v
    return _handle(info, url) or "channel"


def tr_done(out):
    """Video ids already here: {id: ("transcript", folder) or ("reel", folder)}."""
    ids, out = {}, Path(out)
    for note in (out / TR_DIR).glob("*/*/note.md"):
        name = note.parent.name
        if not name.startswith((".", "_")) and not note.parent.parent.name.startswith((".", "_")):
            ids[name.split("_", 1)[-1]] = ("transcript", f"{TR_DIR}/{note.parent.parent.name}/{name}")
    for note in out.glob("*/note.md"):                       # the library: a full reel wins over a transcript
        name = note.parent.name
        if not name.startswith((".", "_")) and find_video(note.parent):
            ids[re.sub(r"_v\d+$", "", name).split("_", 1)[-1]] = ("reel", name)
    return ids


def fetch_info(url, workdir, browser, cookies):
    """yt-dlp's metadata of one video, nothing downloaded: video.info.json in workdir."""
    cmd = [sys.executable, "-m", "yt_dlp", "--skip-download", "--write-info-json", "--no-playlist",
           "--playlist-items", "1", "--no-warnings", "--sleep-requests", "0.75",
           "-o", str(workdir / "video.%(ext)s")]
    _ytdlp_quiet(cmd + _auth(browser, cookies) + [url])
    f = workdir / "video.info.json"
    if not f.exists():
        raise RuntimeError("yt-dlp wrote no metadata for this video")
    return json.loads(f.read_text(encoding="utf-8", errors="replace"))


LANG3 = {"en": "eng", "es": "spa", "de": "deu", "fr": "fra", "it": "ita", "pt": "por", "bg": "bul", "ru": "rus", "nl": "nld",
         "pl": "pol", "tr": "tur", "ja": "jpn", "zh": "zho", "ko": "kor", "ar": "ara", "hi": "hin", "sv": "swe", "da": "dan",
         "no": "nor", "fi": "fin", "cs": "ces", "el": "ell", "ro": "ron", "hu": "hun", "uk": "ukr", "he": "heb", "id": "ind",
         "vi": "vie", "th": "tha", "ca": "cat", "sr": "srp", "hr": "hrv", "sk": "slk", "sl": "slv"}


def pick_caption(info, prefer=""):
    """Which caption track to take: ("subs"|"auto", code), or None when the site has none in the video's language.
    YouTube: the creator's subtitles come first, then the automatic ones ("xx-orig" is the untranslated track; the
    other automatic languages are machine translations and are never taken). Other sites (TikTok has captions on most
    videos): the track in the video's language, else the first one."""
    subs = {k: v for k, v in (info.get("subtitles") or {}).items() if v and k != "live_chat"}
    auto = {k: v for k, v in (info.get("automatic_captions") or {}).items() if v}
    prefer = (prefer or "").strip().lower()
    if prefer and platform_of(info.get("webpage_url") or "", info) == "YouTube":
        # a language set in Settings wins over YouTube's language field, which can point at an automatic dub
        k = next((k for k in subs if k.lower() == prefer or k.lower().startswith(prefer + "-")), None)
        if k:
            return "subs", k
        k = next((k for k in auto if k.lower() == f"{prefer}-orig"), None)
        if k:
            return "auto", k
    if platform_of(info.get("webpage_url") or "", info) != "YouTube":
        if not subs:
            return None
        lang = (info.get("language") or "").lower()
        if prefer:
            p3 = LANG3.get(prefer, prefer)
            k = next((k for k in subs if k.lower() in (prefer, p3) or k.lower().startswith((prefer + "-", p3 + "-"))), None)
            if k:
                return "subs", k
        wanted = {lang, LANG3.get(lang, lang)} if lang else set()        # TikTok codes are 3 letters (eng-US)
        k = next((k for k in subs if any(w and (k.lower() == w or k.lower().startswith(w + "-")) for w in wanted)), None)
        return "subs", k or next(iter(subs))
    orig = [k for k in auto if k.lower().endswith("-orig")]
    lang = (info.get("language") or (orig[0][:-5] if orig else "")).lower()
    if lang:
        k = next((k for k in subs if k.lower() == lang or k.lower().startswith(lang + "-")), None)
        if k:
            return "subs", k
        k = next((k for k in orig if k.lower() == f"{lang}-orig"), None)
        if k:
            return "auto", k
    if subs and not lang:
        return "subs", next(iter(subs))
    if orig:
        return "auto", orig[0]
    return None


def fetch_caption(workdir, kind, code):
    """Download one caption track as json3 from the saved metadata (no second lookup of the video)."""
    cmd = [sys.executable, "-m", "yt_dlp", "--load-info-json", str(workdir / "video.info.json"), "--skip-download",
           "--write-subs" if kind == "subs" else "--write-auto-subs", "--sub-langs", re.escape(code),
           "--sub-format", "json3/srt/vtt/best", "--no-warnings", "--sleep-subtitles", str(TR_SUB_SLEEP),
           "-o", str(workdir / "video.%(ext)s")]
    _ytdlp_quiet(cmd)
    files = sorted(p for p in workdir.glob("video.*") if p.suffix.lower() in (".json3", ".srt", ".vtt"))
    if not files:
        raise RuntimeError(f"the {code} captions could not be downloaded")
    return files[0]


def _cues(path):
    """(start seconds, text) per cue from a json3 (YouTube), srt or vtt caption file."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    cues = []
    if path.suffix.lower() == ".json3":
        for ev in (json.loads(text).get("events") or []):
            if ev.get("tStartMs") is None:
                continue
            t = re.sub(r"\s+", " ", "".join(s.get("utf8", "") for s in ev.get("segs") or [])).strip()
            if t:
                cues.append((ev["tStartMs"] / 1000.0, t))
        return cues
    last = None
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):          # srt and vtt: blocks of cue lines
        m = re.search(r"(\d+):(\d{2}):(\d{2})[.,](\d{1,3})\s*-->|(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->", block)
        if not m:
            continue
        if m.group(1) is not None:
            t0 = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4).ljust(3, "0")) / 1000
        else:
            t0 = int(m.group(5)) * 60 + int(m.group(6)) + int(m.group(7).ljust(3, "0")) / 1000
        lines = block.split("\n")
        body = lines[[i for i, l in enumerate(lines) if "-->" in l][0] + 1:]
        t = re.sub(r"<[^>]+>", "", " ".join(body)).replace("&nbsp;", " ")
        t = re.sub(r"\s+", " ", t).strip()
        if t and t != last:                                     # rolling captions repeat the previous line
            cues.append((t0, t)); last = t
    return cues


def caption_lines(path, max_span=8.0, max_chars=150):
    """A caption file (json3, srt or vtt) -> ([mm:ss] lines, start times). Cues are joined into readable lines: a
    line ends at a sentence end, after about max_span seconds, or at max_chars (automatic captions have no
    punctuation)."""
    cues = _cues(Path(path))
    lines, starts, cur, t0 = [], [], [], 0.0
    def flush():
        if cur:
            lines.append(f"[{int(t0)//60:02d}:{int(t0)%60:02d}] " + " ".join(cur)); starts.append(t0)
            cur.clear()
    for t, text in cues:
        if cur and (t - t0 >= max_span or sum(len(x) + 1 for x in cur) + len(text) > max_chars
                    or re.search(r"[.!?…]$", cur[-1])):
            flush()
        if not cur:
            t0 = t
        cur.append(text)
    flush()
    return lines, starts


def fetch_audio(workdir, url, browser, cookies, prog):
    """The audio only, for Whisper: from the saved metadata, else by looking the video up again."""
    cmd = [sys.executable, "-m", "yt_dlp", "-f", AUDIO_FORMAT, "--no-warnings", "--no-playlist", "--playlist-items", "1",
           "-o", str(workdir / "audio.%(ext)s")]
    try:
        run_ytdlp(cmd + ["--load-info-json", str(workdir / "video.info.json")], prog)
    except YtdlpError:
        run_ytdlp(cmd + _auth(browser, cookies) + [url], prog)
    files = [p for p in workdir.glob("audio.*") if p.suffix.lower() not in (".part", ".ytdl", ".json")]
    if not files:
        raise RuntimeError("no audio file was downloaded")
    return files[0]


def _channel_url(url, info):
    """The creator's page: the @handle address when there is one (else the /channel/ one), Videos tab on YouTube;
    built from the handle on Instagram and X."""
    plat = platform_of(url, info)
    u = next((x for x in (info.get("uploader_url"), info.get("channel_url")) if x and "/@" in x), None) \
        or info.get("channel_url") or info.get("uploader_url") or ""
    u = u.rstrip("/")
    if not u:
        h = _handle(info, url).lstrip("@")
        u = {"Instagram": f"https://www.instagram.com/{h}/", "X": f"https://x.com/{h}", "TikTok": f"https://www.tiktok.com/@{h}"}.get(plat, "") if h else ""
    return u + "/videos" if u and plat == "YouTube" else u


def trim_info(info):
    return {k: info[k] for k in INFO_KEEP if k in info}


def _is_429(e):
    return bool(re.search(r"\b429\b|too many requests", str(e), re.I))


class TrProgress:
    """@@PROGRESS lines for a transcripts run: the bar covers the whole run (videos done of all), the time left
    comes from the learned seconds per video; a Whisper fallback shows its own stage."""
    def __init__(self, n, stats, out):
        self.n, self.stats, self.out, self.i = n, stats, out, 0
        self.t0, self.last, self.stage, self.base, self.frac = time.time(), 0, "captions", time.time(), 0.0
        self.dl_eta, self.est, self.tx = None, {}, 0.0
    def plan(self, dur):                 # Whisper for this video: same estimate as a normal run
        wd = whisper_device()
        self.tx = self.stats[skey("model_load", wd)] + dur * self.stats[skey("tx_factor", wd)]
    def start(self, stage):
        self.stage, self.frac, self.base = stage, 0.0, time.time(); self.emit(force=True)
    def rebase(self):
        self.base = time.time()
    def update(self, frac, dl_eta=None):
        self.frac, self.dl_eta = max(0.0, min(1.0, frac)), dl_eta; self.emit()
    def video(self, i):
        self.i = i; self.start("captions")
    def emit(self, force=False):
        now = time.time()
        if not force and now - self.last < 0.8:
            return
        self.last = now
        left_videos = max(0, self.n - self.i)                  # after this one
        rest = left_videos * self.stats["cap_secs"]
        if self.stage == "paused":
            label, pct, eta = "Paused", 100.0 * (self.i - 1) / max(1, self.n), None
        elif self.stage == "captions":
            label, pct, eta = "Getting captions", 100.0 * (self.i - 1) / max(1, self.n), self.stats["cap_secs"] + rest
        elif self.stage == "download":
            label, pct, eta = "Downloading the audio", self.frac * 100, (self.dl_eta or 0) + self.tx + rest
        else:
            el = now - self.base
            mine = el / self.frac * (1 - self.frac) if self.frac > 0.05 and el > 1.5 else max(self.tx * (1 - self.frac), self.tx * 0.1)
            label, pct, eta = "Transcribing (no captions on YouTube)", self.frac * 100, mine + rest
        print("@@PROGRESS " + json.dumps({"i": self.i, "n": self.n, "stage": self.stage, "label": label,
                                          "stage_pct": round(pct, 1), "eta": None if eta is None else round(eta),
                                          "elapsed": round(now - self.t0), "eta_partial": False}), flush=True)


def transcript_one(url, args, out, prog, source_hint="link"):
    """One video: metadata, captions (or audio + Whisper), note.md in _transcripts/<channel>/<date>_<id>/.
    Returns the @@DONE dict."""
    t_start = time.time()
    tmp = out / TR_DIR / "_tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    info = fetch_info(url, tmp, args.browser, args.cookies)
    vid = str(info.get("id") or "")
    title = (info.get("title") or vid).strip().replace("\n", " ")
    key, name = tr_key(info, url), tr_name(info, url)
    dest = out / TR_DIR / key / f"{info.get('upload_date') or '00000000'}_{vid}"
    if dest.exists():
        shutil.rmtree(tmp)
        print(f"  already done: {title}")
        return {"url": url, "folder": f"{TR_DIR}/{key}/{dest.name}", "skipped": True, "transcripts": True, "channel": key}
    pick = pick_caption(info, getattr(args, "prefer_lang", ""))
    lang, source = "-", ""
    if pick:
        kind, code = pick
        cap = fetch_caption(tmp, kind, code)
        lines, starts = caption_lines(cap)
        cap.unlink()
        lang = re.sub(r"-orig$", "", code, flags=re.I)
        site = platform_of(url, info).lower()
        source = f"{site} captions" if kind == "subs" else f"{site} captions (automatic)"
        how = (f"{platform_of(url, info)}'s captions" if site != "youtube" else "the creator's captions") if kind == "subs" else "automatic captions"
    else:
        print(f"  no captions on {platform_of(url, info)}")
        print("  downloading the audio for Whisper")
        prog.start("download")
        audio = fetch_audio(tmp, url, args.browser, args.cookies, prog)
        dur = video_duration(audio) or float(info.get("duration") or 0)
        prog.plan(dur)
        prog.start("transcribe")
        lines, lang, starts = transcribe(audio, args.model, args.lang, prog, str(out))
        audio.unlink(missing_ok=True)
        source, how = "whisper", "Whisper"
        prog.start("captions")
    tinfo = trim_info(info)
    (tmp / "video.info.json").write_text(json.dumps(tinfo, ensure_ascii=False, indent=1), encoding="utf-8")
    write_note(tmp, url, tinfo, [], lines, lang, None, args.owner, extra=[f"transcript: {source}"])
    for p in tmp.iterdir():                                   # nothing else stays (a leftover caption file)
        if p.name not in ("note.md", "video.info.json"):
            p.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    chan = dest.parent / "_channel.json"
    if chan.exists():                                          # a listing knew only the handle: the video knows the name
        try:
            meta = json.loads(chan.read_text(encoding="utf-8"))
            if isinstance(meta, dict) and name and meta.get("name") in ("", key, meta.get("handle", "").lstrip("@")) and meta.get("name") != name:
                meta["name"] = name
                tmp_c = chan.with_name("._channel.json.tmp")
                tmp_c.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
                replace_retry(tmp_c, chan)
        except (OSError, ValueError):
            pass
    if not chan.exists():
        chan.write_text(json.dumps({"key": key, "name": name, "id": info.get("channel_id") or info.get("uploader_id") or "",
                                    "handle": ("@" + _handle(info, url).lstrip("@")) if _handle(info, url) else "",
                                    "url": _channel_url(url, info),
                                    "platform": platform_of(url, info), "at": time.strftime("%Y-%m-%d %H:%M")},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    replace_retry(tmp, dest)
    took = time.time() - t_start
    if source != "whisper":
        learn(str(out), prog.stats, "cap_secs", took)
    print(f"  done in {fmt_dur(took)} ({how}, {len(lines)} lines) -> {dest / 'note.md'}")
    return {"url": url, "folder": f"{TR_DIR}/{key}/{dest.name}", "transcripts": True, "channel": key,
            "from": source_hint, "source": source, "title": title}


def control(out):
    """What Reels Hub asked for: {"pause": bool, "stop": bool} (no file: nothing)."""
    try:
        d = json.loads((out / TR_DIR / CONTROL).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def wait_or_stop(out, secs):
    """Sleep up to secs, a second at a time; True if a Stop came in meanwhile."""
    end = time.time() + secs
    while time.time() < end:
        if control(out).get("stop"):
            return True
        time.sleep(min(1.0, max(0.0, end - time.time())))
    return False


def hold_if_paused(out, prog, i):
    """Pause pressed: wait here (the bar says Paused) until Resume or Stop. True if Stop came."""
    c = control(out)
    if not c.get("pause") and not c.get("stop"):
        return False
    if c.get("stop"):
        return True
    print("  paused - press Resume to go on", flush=True)
    prog.start("paused")
    while True:
        time.sleep(1)
        c = control(out)
        if c.get("stop"):
            return True
        if not c.get("pause"):
            print("  resumed", flush=True)
            prog.video(i)
            return False
        prog.emit()


class RunRecord:
    """What a channel (or playlist) run did, kept in its _channel.json under "run" so Reels Hub can say where it
    stopped and offer Resume: listed, here (already done before), todo, done, failed, last, next, state."""
    def __init__(self, out, data, lurl, entries, new):
        self.cdir = out / TR_DIR / tr_key(data, lurl)
        self.rec = {"url": lurl, "title": data.get("title") or lurl, "pid": os.getpid(),
                    "started": time.strftime("%Y-%m-%d %H:%M"), "at": time.strftime("%Y-%m-%d %H:%M"),
                    "listed": len(entries), "here": len(entries) - len(new), "todo": len(new), "done": 0, "failed": 0,
                    "last": "", "next": new[0]["title"] if new else "", "state": "running"}
        self.titles = [e["title"] for e in new]
        self.data = data
        self.save()

    def save(self):
        self.cdir.mkdir(parents=True, exist_ok=True)
        f = self.cdir / "_channel.json"
        try:
            meta = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
            if not isinstance(meta, dict):
                meta = {}
        except (OSError, ValueError):
            meta = {}
        if not meta.get("key"):
            d, u = self.data, self.rec["url"]
            plat = platform_of(u, d)
            meta.update({"key": tr_key(d, u), "name": tr_name(d, u), "id": d.get("channel_id") or d.get("uploader_id") or "",
                         "handle": ("@" + _handle(d, u).lstrip("@")) if _handle(d, u) else "",
                         "url": (_channel_url(u, d) or u) if plat == "YouTube" else u,
                         "platform": plat, "at": time.strftime("%Y-%m-%d %H:%M")})
        self.rec["at"] = time.strftime("%Y-%m-%d %H:%M")
        meta["run"] = self.rec
        tmp = f.with_name("._channel.json.tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            replace_retry(tmp, f)
        except OSError as e:          # only the "where it got to" line in the app: never worth stopping the run for
            print(f"  (could not update {f.name}: {e}; the run goes on)")
            try:
                tmp.unlink()
            except OSError:
                pass

    def step(self, k, ok):
        """Video number k of this listing (1-based) finished (ok) or failed."""
        self.rec["done" if ok else "failed"] += 1
        self.rec["last"] = self.titles[k - 1] if k - 1 < len(self.titles) else ""
        self.rec["next"] = self.titles[k] if k < len(self.titles) else ""
        self.save()

    def end(self, state):
        left = self.rec["todo"] - self.rec["done"] - self.rec["failed"]
        self.rec["state"] = "finished" if left <= 0 and state == "finished" else state
        self.rec["left"] = max(0, left)
        self.save()


def transcripts_main(urls, args):
    """`--transcripts`: every link (video, channel or playlist) -> transcript folders, one @@DONE line per video.
    Pause / Stop come through the control file; a run that is stopped or breaks off can be resumed with the same link
    (what is here is skipped) and its channel's _channel.json says where it got to."""
    out = Path(args.out)
    (out / TR_DIR).mkdir(parents=True, exist_ok=True)
    stats = load_stats(args.out)
    todo, failed_lists, records = [], [], []                    # todo: (url, from, title, record, k)
    done = tr_done(args.out)
    for url in urls:
        if is_listing(url):
            lurl = listing_url(url)
            print(f"\n== listing {lurl}")
            try:
                data, entries = list_videos(lurl, args.browser, args.cookies)
            except Exception as e:
                print(f"  FAILED to list it: {e}"); failed_lists.append(url); continue
            seen = set(x[0] for x in todo)
            new = [e for e in entries if e["id"] not in done and e["url"] not in seen]
            print(f"  {data.get('title') or lurl}: {len(entries)} videos, {len(entries) - len(new)} already here, {len(new)} to do")
            rec = RunRecord(out, data, lurl, entries, new)
            records.append(rec)
            todo += [(e["url"], "channel", e["title"], rec, k) for k, e in enumerate(new, 1)]
        else:
            vid = video_id_of(url)
            if vid and vid in done:
                kind, folder = done[vid]
                print(f"\n== {url}\n  already {'in your library as a full video' if kind == 'reel' else 'done'}: {folder}")
                print("@@DONE " + json.dumps({"url": url, "folder": folder, "skipped": True, "transcripts": True}), flush=True)
                continue
            if url not in set(x[0] for x in todo):
                todo.append((url, "link", "", None, 0))
    print("@@TOTAL " + json.dumps({"n": len(todo)}), flush=True)
    if not todo:
        for rec in records:
            rec.end("finished")
        print("\nnothing to do" + (": the listed channels could not be read" if failed_lists else ""))
        return 1 if failed_lists else 0
    prog = TrProgress(len(todo), stats, args.out)
    failed, consecutive_429, channels, ended, tried = [], 0, {}, "finished", 0
    for i, (url, origin, title, rec, k) in enumerate(todo, 1):
        if hold_if_paused(out, prog, i):
            ended = "stopped"
            print(f"\n  stopped by you after {i - 1} of {len(todo)}: next would be {title or url} - "
                  "Resume on the channel (or the same link again) goes on from here", flush=True)
            break
        print(f"\n== [{i}/{len(todo)}] {title or url}")
        prog.video(i)
        tried = i
        ok = False
        try:
            try:
                d = transcript_one(url, args, out, prog, origin)
            except (YtdlpError, RuntimeError) as e:
                if not _is_429(e):
                    raise
                consecutive_429 += 1
                if consecutive_429 >= 3:
                    print("  YouTube keeps refusing (429 Too Many Requests): stopping here, run it again later - "
                          "what is done stays done")
                    failed.append(url)
                    ended = "interrupted"
                    if rec:
                        rec.step(k, False)
                    break
                print(f"  YouTube is limiting requests (429): waiting {TR_RETRY_WAIT:g} s and trying once more")
                if wait_or_stop(out, TR_RETRY_WAIT):
                    ended = "stopped"
                    print(f"  stopped by you after {i - 1} of {len(todo)}: next would be {title or url}", flush=True)
                    break
                d = transcript_one(url, args, out, prog, origin)
            consecutive_429 = 0
            ok = True
            print("@@DONE " + json.dumps(d, ensure_ascii=False), flush=True)
            if d.get("channel") and not d.get("skipped"):
                channels[d["channel"]] = channels.get(d["channel"], 0) + 1
        except Exception as e:
            print(f"  FAILED: {e}"); failed.append(url)
        if rec:
            rec.step(k, ok)
        if i < len(todo):
            if wait_or_stop(out, TR_PAUSE):
                ended = "stopped"
                nxt = todo[i][2] or todo[i][0]
                print(f"\n  stopped by you after {i} of {len(todo)}: next would be {nxt} - "
                      "Resume on the channel (or the same link again) goes on from here", flush=True)
                break
    for rec in records:
        rec.end(ended)
    for key, n in channels.items():
        print("@@CHANNEL " + json.dumps({"channel": key, "new": n}), flush=True)
    shutil.rmtree(out / TR_DIR / "_tmp", ignore_errors=True)   # a failed video's leftovers
    attempted = len(todo) if ended == "finished" else tried
    ok = attempted - len(failed)
    if ended == "finished":
        print(f"\n{ok} of {len(todo)} done" + (f", {len(failed)} failed" if failed else "")
              + (f", {len(failed_lists)} channel link(s) could not be listed" if failed_lists else ""))
    else:
        print(f"\n{ended} after {attempted} of {len(todo)}: {ok} done, {len(failed)} failed, {len(todo) - attempted} not done yet")
    if failed:
        Path(args.out, "failed.txt").write_text("\n".join(failed), encoding="utf-8")
    return 0 if ok or not (failed or failed_lists) else 1



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
    ap.add_argument("--channel-category", action="store_true", help="put a new video in its channel's home category, if the app gave the channel one")
    ap.add_argument("--prefer-lang", default="", help="transcripts: take captions in this language first (e.g. en)")
    ap.add_argument("--transcripts", action="store_true",
                    help="transcripts only (no video, no snapshots): links can be videos, YouTube channels or playlists")
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
    if args.transcripts:
        return transcripts_main(urls, args)

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
