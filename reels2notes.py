#!/usr/bin/env python3
"""
reels2notes.py - download Instagram reels/posts and turn each into a Markdown note
with caption, spoken transcript, on-screen text (OCR) and snapshots.

Setup (once):
    pip install yt-dlp faster-whisper easyocr
    # plus ffmpeg on PATH  (Windows: winget install ffmpeg | macOS: brew install ffmpeg)

Usage:
    python -m yt_dlp -U                              # keep yt-dlp current
    python reels2notes.py https://www.instagram.com/p/DeugAKZS1eg/
    python reels2notes.py -f links.txt            # one URL per line
Options:
    --cookies cookies.txt           Instagram login cookies (Netscape format) - use if download needs login
    --browser firefox|edge|chrome   read login cookies straight from a browser (default: none)
    --scene 0.3                     scene-change sensitivity for snapshots (lower = more frames)
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


def run(cmd):
    print("  >", " ".join(map(str, cmd)))
    return subprocess.run(cmd, check=True)


def download(url, workdir, browser, cookies):
    base = [sys.executable, "-m", "yt_dlp", "--write-info-json", "--no-playlist",
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
    vids = [p for p in workdir.glob("video.*") if p.suffix not in (".json", ".part")]
    info = json.loads((workdir / "video.info.json").read_text(encoding="utf-8"))
    return vids[0], info


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


def snapshots(video, workdir, scene):
    shots = workdir / "shots"
    shots.mkdir(exist_ok=True)
    times = _extract(video, shots, f"select='eq(n,0)+gt(scene,{scene})',scale=720:-2")
    files = sorted(shots.glob("*.jpg"))
    if len(files) < 3:  # one continuous take: fall back to one frame every 2 s
        for f in files:
            f.unlink()
        times = _extract(video, shots, "fps=0.5,scale=720:-2")
        files = sorted(shots.glob("*.jpg"))
    if len(times) != len(files):  # safety net if timestamps could not be read
        times = [i * 2.0 for i in range(len(files))]
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
    md = [
        "---",
        f"source: {url}",
        f"author: {author}",
        f"date: {date}",
        f"duration_s: {info.get('duration', '')}",
        "tags: [reel]",
        "---",
        f"# {info.get('title') or info.get('id')}",
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
        info = json.loads(info_file.read_text(encoding="utf-8"))
        if info.get("id") and info["id"] in url:
            vids = [p for p in info_file.parent.glob("video.*") if p.suffix not in (".json", ".part")]
            if vids:
                return info_file.parent, vids[0], info
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
    shots = snapshots(video, workdir, args.scene)
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
            time.sleep(5)  # be gentle with Instagram
    if failed:
        Path(args.out, "failed.txt").write_text("\n".join(failed))
        print(f"\n{len(failed)} failed - see {args.out}/failed.txt")


if __name__ == "__main__":
    sys.exit(main())
