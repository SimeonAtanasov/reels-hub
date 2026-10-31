# Reels Hub

Turn Instagram reels, TikTok videos and YouTube videos/Shorts (or anything yt-dlp can download) into searchable, printable notes - built for saving training videos like kettlebell drills.

For each reel it saves:
- the video and caption
- a timestamped transcript of what is said (Whisper, runs locally)
- snapshots at scene changes (or every 2 s for single-take videos)
- on-screen text read from the snapshots (OCR)
- a Markdown note per reel, ready for Obsidian

The **Reels Hub** web app (local only) lets you paste links and run them, browse reels by category, star snapshots as favorites, reorder them, correct the text under each one, print them on A4, and send them to an Obsidian vault.

## Setup (Windows)

```
pip install yt-dlp faster-whisper easyocr
winget install Gyan.FFmpeg
```

Open a new terminal after installing ffmpeg.

## Run

Double-click `reels-hub.bat`, or:

```
python reels_hub.py
```

It opens http://127.0.0.1:8765. Paste links, one per line, and press **Run**. The first run downloads the speech and OCR models (a few minutes).

Command line only:

```
python reels2notes.py https://www.instagram.com/p/XXXXXXXX/
python reels2notes.py -f links.txt
```

## Where things are stored

| What | Default location | Change with |
|---|---|---|
| Videos, snapshots, notes, favorites, categories | `Downloads/reels` | `--out PATH` |
| Obsidian notes | `<vault>/Instagram`, `/TikTok`, `/YouTube` (one note per video, images in `<Platform>/attachments`) | `--vault PATH` |

The default vault path is `Documents/Obsidian Vault` under OneDrive. If it is not found, Obsidian sync is switched off.

## If a download needs login

Public videos usually download without login. If one fails, export that site's cookies with the "Get cookies.txt LOCALLY" browser extension and run:

```
python reels2notes.py --cookies cookies.txt <link>
```

Keep `cookies.txt` private and delete it afterwards. It is excluded by `.gitignore`.

## Notes

- Everything runs on your own machine. The app listens on 127.0.0.1 only.
- Long videos: snapshots are capped at 60 per video (`--max-shots`), and transcription on CPU takes roughly a quarter to a half of the video's length.
- For personal reference only: respect creators' rights and each platform's terms.
- The sites change often. If downloads break, update yt-dlp: `python -m yt_dlp -U`
