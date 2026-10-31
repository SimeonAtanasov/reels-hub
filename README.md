# Reels Hub

Turn Instagram reels, TikTok videos, YouTube videos/Shorts and X posts (or anything yt-dlp can download) into searchable, printable notes - built for saving training videos like kettlebell drills.

For each reel it saves:
- the video and caption
- a timestamped transcript of what is said (Whisper, runs locally)
- snapshots: **Auto** by default - a frame every 2 s for short videos, gradually wider for longer ones (about 5 s at 6 min, 12 s at 36 min, 20 s from 1.5 h), plus every scene cut, up to 400 (very long videos get a wider gap to stay under that). Or pick a fixed maximum in the app (60-400), which is never exceeded. Delete the ones you don't need, keep your favorites
- on-screen text read from the snapshots (OCR)
- a Markdown note per reel, ready for Obsidian

The **Reels Hub** web app (local only) lets you paste links and run them, browse reels grouped by category or source and sorted by date posted, date added or title, star snapshots as favorites, reorder them, correct the text under each one, print them on A4, and send them to an Obsidian vault.

While watching, **📸 Snapshot** (or the S key; Shift+S also stars it) captures the current frame; it slots into the snapshots in time order. Every time shown on a snapshot, favorite or on-screen text is clickable and plays the video from that moment (P in the enlarged view).

Drag the edges between the list, the video and the text to give each one more or less room, and drag the bar under the video to make it taller (a wide YouTube video grows when you widen its column instead). Double-click an edge or the bar to go back to the standard size. The sizes are remembered in that browser on that computer.

Snapshots can be edited inside the app (crop, rotate, draw, arrows, boxes, text, blur, undo) or opened in Paint; the original is always kept for **Reset to original**. Images and video links can be copied to paste into chat apps.

## Install (Windows)

**Easiest:** download this folder (on GitHub: Code, Download ZIP, then extract it, e.g. to `Documents\reels-hub`) and double-click **`install.bat`**. It installs Python, ffmpeg and Deno if they are missing, the Python packages, and puts a **Reels Hub** shortcut on the desktop. If it says Windows needs a fresh window, close it and run `install.bat` once more.

By hand instead:

```
pip install yt-dlp faster-whisper easyocr
winget install Gyan.FFmpeg
winget install DenoLand.Deno
```

Open a new terminal after installing ffmpeg.

## First start

Double-click **Reels Hub** on the desktop (or `reels-hub.bat`). The first time, it asks:

- **Your name** - each laptop belongs to one person; every video you add is marked as yours.
- **Reels folder** - where videos, snapshots and notes are saved (suggested: `Downloads\reels`).
- **Obsidian vault** - optional; leave it empty if you don't use Obsidian.

These are saved on that computer only (`%APPDATA%\ReelsHub\config.json`). Change them any time with **Settings** at the top right. Each of you has your own library, categories, favorites and notes on your own laptop.

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
| Videos, snapshots, notes, favorites, categories | the reels folder from Settings (default `Downloads/reels`) | Settings, or `--out PATH` |
| Obsidian notes | new notes in `<vault>/YouTube`, `/TikTok`, `/Instagram`, `/X` - move or rename them freely, the app finds them again; favorite images in `910_Attachments/ReelsHub` | `--vault PATH` |

The vault comes from Settings. If none is set or it is not found, Obsidian sync is switched off.

Notes follow the vault's video template: date and time, Title, Source, Tags, Related (MOC links), Creator; then the AI sections (Obsidian Tags, Summary, Main Takeaways, Key Concepts, Reflective Questions, Actionable Insights, Compare and Contrast) left ready to fill, Favorites, My notes, Description, Content (transcript in paragraphs) and Original text (with timestamps). YouTube notes start with `'` and TikTok notes with a backtick, like the templates. **Copy AI prompt** in the app copies the vault's `$Obsidian AI prompts` with the transcript, for ChatGPT or Claude.

## If a download needs login

Public videos usually download without login. If one fails, export that site's cookies with the "Get cookies.txt LOCALLY" browser extension and run:

```
python reels2notes.py --cookies cookies.txt <link>
```

Keep `cookies.txt` private and delete it afterwards. It is excluded by `.gitignore`.

X often needs login for videos. If an X link fails, this is the usual fix.

In the app: save the file as `cookies.txt` next to `reels_hub.py` and press Run again. The app uses it automatically, and a download that fails with cookies is retried without them.

## Notes

- Everything runs on your own machine. The app listens on 127.0.0.1 only and ignores requests from other websites.
- My notes and favorite text save automatically. Re-processing a video keeps your notes; its favorites are cleared only if the run succeeds, and the video's controls pause while it runs.
- Long videos: snapshots are capped per video (`--max-shots auto` or a number; set it next to Run in the app), on-screen text is read from at most 60 of them, and transcription on CPU takes roughly a quarter to a half of the video's length.
- Running a link that is already done asks what to do: **New version** (a separate copy, v2, v3... - the original and its favorites stay) or **Skip**. On a reel, **+ New version** does the same and **Re-process** replaces that version. Both reuse the downloaded video.
- If memory is low, transcription falls back to smaller Whisper models (base, then tiny).
- For personal reference only: respect creators' rights and each platform's terms.
- The sites change often. If downloads break, update yt-dlp: `python -m yt_dlp -U`
