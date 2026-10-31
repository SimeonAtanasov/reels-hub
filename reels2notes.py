#!/usr/bin/env python3
"""
reels2notes.py - download Instagram reels, TikTok videos and YouTube videos/Shorts and turn
each into a Markdown note with caption, spoken transcript, on-screen text (OCR) and snapshots.

Setup (once):
    pip install yt-dlp faster-whisper easyocr
    # plus ffmpeg on PATH  (Windows: winget install ffmpeg | macOS: brew install ffmpeg)

Usage:
    python -m yt_dlp -U                              # keep yt-dlp current
    python reels2notes.py https://www.instagram.com/p/DeugAKZS1eg/
    python reels2notes.py https://www.tiktok.com/@user/video/1234567890
    python reels2notes.py https://www.youtube.com/shorts/abcdEFGhijk
    python reels2notes.py -f links.txt            # one URL per line
Options:
    --cookies cookies.txt           login cookies (Netscape format) - use if a download needs login
    --browser firefox|edge|chrome   read login cookies straight from a browser (default: none)
    --scene 0.3                     scene-change sensitivity for snapshots (lower = more frames)
    --max-shots 60                  cap on snapshots per video (long YouTube videos)
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
    return (info or {}).get("extractor_key") or "Web"


def find_video(folder):
    vids = [p for p in folder.glob("video.*") if p.suffix.lower() in VIDEO_EXT]
    return vids[0] if vids else None


def run(cmd):
    print("  >", " ".join(map(str, cmd)))
    return subprocess.run(cmd, check=True)


def download(url, workdir, browser, cookies):
    base = [sys.executable, "-m", "yt_dlp", "--write-info-json", "--no-playlist",
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
        run(base + auth + [url])
    except subprocess.CalledProcessError:
        if not auth:
            raise
        print("  login cookies failed, retrying without login...")
        run(base + [url])
    info = json.loads((workdir / "video.info.json").read_text(encoding="utf-8"))
    video = find_video(workdir)
    if not video:
        raise RuntimeError("download finished but no video file was found")
    return video, info


def _extract(video, shots, vf):
    """Extract frames and return their timestamps (seconds) via ffmpeg's showinfo."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-i", str(video), "-vf", vf + ",showinfo",
           "-fps_mode", "vfr", "-q:v", "3", str(shots / "shot_%03d.jpg")]
    print("  >", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-2000:])
        raise subprocess.CalledProcessError(r.returncode, cmd)
    return [round(float(t), 2) for t in re.findall(r"pts_time:\s*([\d.]+)", r.stderr)]


def video_duration(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(video)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def snapshots(video, workdir, scene, max_shots=60):
    shots = workdir / "shots"
    shots.mkdir(exist_ok=True)
    dur = video_duration(video)
    interval = max(2.0, dur / max_shots) if dur else 2.0   # long videos: spread frames evenly
    times = _extract(video, shots, f"select='eq(n,0)+gt(scene,{scene})',scale=720:-2")
    files = sorted(shots.glob("*.jpg"))
    if len(files) < 3 or len(files) > max_shots:
        # one continuous take, or too many cuts: one frame every `interval` seconds instead
        for f in files:
            f.unlink()
        times = _extract(video, shots, f"fps=1/{interval:.3f},scale=720:-2")
        files = sorted(shots.glob("*.jpg"))
    if len(times) != len(files):  # safety net if timestamps could not be read
        times = [i * interval for i in range(len(files))]
    (workdir / "shots.json").write_text(
        json.dumps({f.name: t for f, t in zip(files, times)}, indent=1), encoding="utf-8")
    return files


def transcribe(video, model_name, lang):
    import numpy as np
    from faster_whisper import WhisperModel
    # decode audio with ffmpeg (16 kHz mono float32) - avoids PyAV version issues
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-vn",
                          "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True).stdout  # no audio -> empty
    audio = np.frombuffer(raw, dtype=np.float32)
    if audio.size == 0:
        return [], lang or "-"
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(audio, language=lang, vad_filter=True)
    lines = [f"[{int(s.start)//60:02d}:{int(s.start)%60:02d}] {s.text.strip()}" for s in segments]
    return lines, info.language


_ocr = None
def ocr(images):
    global _ocr
    import easyocr
    if _ocr is None:
        _ocr = easyocr.Reader(["en"], gpu=False, verbose=False)
    seen, out = set(), []
    for img in images:
        texts = [t for _, t, conf in _ocr.readtext(str(img)) if conf > 0.4]
        line = " | ".join(texts).strip()
        if line and line.lower() not in seen:  # drop repeats across frames
            seen.add(line.lower())
            out.append((img.name, line))
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


def find_existing(out, url):
    """Return (workdir, video, info) if this post was already downloaded."""
    for info_file in Path(out).glob("*/video.info.json"):
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


def process(url, args):
    print(f"\n== {url}")
    existing = find_existing(args.out, url)
    if existing:
        workdir, video, info = existing
        if (workdir / "note.md").exists():
            print("  already done, skipping")
            return
        print("  already downloaded, resuming")
    else:
        tmp = Path(args.out) / "_tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        video, info = download(url, tmp, args.browser, args.cookies)
        workdir = Path(args.out) / f"{info.get('upload_date', '')}_{info['id']}"
        tmp.rename(workdir)
        video = workdir / video.name
    shots_dir = workdir / "shots"
    if shots_dir.exists():
        shutil.rmtree(shots_dir)
    print(f"  platform: {platform_of(url, info)} - {info.get('title') or info.get('id')}")
    shots = snapshots(video, workdir, args.scene, args.max_shots)
    transcript, lang = transcribe(video, args.model, args.lang)
    ocr_lines = None if args.no_ocr else ocr(shots)
    note = write_note(workdir, url, info, shots, transcript, lang, ocr_lines)
    print(f"  done -> {note}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("urls", nargs="*")
    ap.add_argument("-f", "--file")
    ap.add_argument("--browser", default="none")
    ap.add_argument("--cookies", help="cookies.txt exported from your browser")
    ap.add_argument("--scene", type=float, default=0.3)
    ap.add_argument("--max-shots", type=int, default=60)
    ap.add_argument("--model", default="small")
    ap.add_argument("--lang", default=None)
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--out", default=str(Path.home() / "Downloads" / "reels"))
    args = ap.parse_args()

    urls = list(args.urls)
    if args.file:
        urls += [l.strip() for l in Path(args.file).read_text().splitlines() if l.strip().startswith("http")]
    if not urls:
        ap.error("give at least one URL or -f links.txt")

    failed = []
    for i, url in enumerate(urls):
        try:
            process(url, args)
        except Exception as e:
            print(f"  FAILED: {e}"); failed.append(url)
        if i < len(urls) - 1:
            time.sleep(5)  # be gentle with the sites
    if failed:
        Path(args.out, "failed.txt").write_text("\n".join(failed))
        print(f"\n{len(failed)} failed - see {args.out}/failed.txt")


if __name__ == "__main__":
    sys.exit(main())
