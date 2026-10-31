# Reels Hub

Turn Instagram reels, TikTok videos, YouTube videos/Shorts and X posts (or anything yt-dlp can download) into searchable, printable notes - built for saving training videos like kettlebell drills.

For each reel it saves:
- the video and caption
- a timestamped transcript of what is said (Whisper, runs locally)
- dense snapshots: one every 2 s plus every scene cut (choose 60-400 per video; longer videos are spread out). Delete the ones you don't need, keep your favorites
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
| Obsidian notes | `<vault>/Instagram`, `/TikTok`, `/YouTube`, `/X` (one note per video, images in `<Platform>/attachments`) | `--vault PATH` |

The default vault path is `Documents/Obsidian Vault` under OneDrive. If it is not found, Obsidian sync is switched off.

## If a download needs login

Public videos usually download without login. If one fails, export that site's cookies with the "Get cookies.txt LOCALLY" browser extension and run:

```
python reels2notes.py --cookies cookies.txt <link>
```

Keep `cookies.txt` private and delete it afterwards. It is excluded by `.gitignore`.

X often needs login for videos. If an X link fails, this is the usual fix.

In the app: save the file as `cookies.txt` next to `reels_hub.py` and press Run again. The app uses it automatically, and a download that fails with cookies is retried without them.

## Notes

- Everything runs on your own machine. The app listens on 127.0.0.1 only.
- Long videos: snapshots are capped per video (`--max-shots`, default 200; set it next to Run in the app), on-screen text is read from at most 60 of them, and transcription on CPU takes roughly a quarter to a half of the video's length.
- Running a link that is already done asks what to do: **New version** (a separate copy, v2, v3... - the original and its favorites stay) or **Skip**. On a reel, **+ New version** does the same and **Re-process** replaces that version. Both reuse the downloaded video.
- If memory is low, transcription falls back to smaller Whisper models (base, then tiny).
- For personal reference only: respect creators' rights and each platform's terms.
- The sites change often. If downloads break, update yt-dlp: `python -m yt_dlp -U`
