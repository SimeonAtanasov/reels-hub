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

Two people with their own laptops can **share** videos with each other on purpose: see [Sharing between two laptops](#sharing-between-two-laptops-optional).

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
- **Sharing** - optional: the address of your Cloudflare inbox and this laptop's token (see below). Leave it empty if you don't share.

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
| Shares waiting in your Inbox | `_inbox` in the reels folder (dismissed ones in `_trash/_inbox`) | - |
| Your name, folders, sharing token | `%APPDATA%\ReelsHub\config.json` on this computer | Settings |

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

## Sharing between two laptops (optional)

Each laptop has its own library. To give the other person a video on purpose, press **Share with <name>** on a reel, or select several (Ctrl+click or Shift+click) and use **Share with <name>** in the bar at the bottom.

**What goes through the inbox:** the link, the video's title (so the Inbox can show what it is), your favorites (their time in the video, their text, the Corrected mark, and the image only where you edited it), your My notes and your category as a suggestion. Who sent it comes from the sign-in, not from the app. Nothing else: no video, snapshots, transcript or Obsidian note. The other laptop downloads and processes the video itself.

**On the other laptop:** Reels Hub looks for shares when it opens, when you open **Inbox**, and every 90 seconds while it runs (or press **Check now**). A share is copied to that laptop and deleted from Cloudflare straight away; one that is never picked up is deleted after 30 days. **Inbox** lists who sent what, how many favorites, the notes and the suggested category (pick another one if you like):

- **Add** downloads and processes the video on that laptop, takes the favorites again at the same moments (with the sender's edited images), fills in their text and Corrected marks, the notes and the category, and marks the reel as shared (↘ name). If you already have the video, it offers a new version instead. Several Adds wait in line, like Run. If the download fails, the share stays in the Inbox with the reason; press Add again later.
- **Dismiss** leaves it out (it moves to `_trash/_inbox` in the reels folder).

**Settings, Sharing:** the inbox address and this laptop's service token (Client ID and Client Secret), with **Test connection**. They are saved only on this computer, never in the app folder.

**Deleting:** each person has full control of their own gallery, deleting videos and images included. Optional, off by default: `"delete": false` in a person's entries in the inbox's `PEOPLE` list hides **Delete this reel** on their laptop and the app refuses it.

**Your own inbox** (the `cloud/` folder): a Cloudflare Pages project with one Functions API and a D1 database, behind Cloudflare Access (an email code for people, one service token per laptop). It runs on free plans and needs Node.js 22 or newer for `wrangler`:

1. In `cloud/`: `npm install`, `npx wrangler login`, `npx wrangler d1 create reels-hub --jurisdiction eu`.
2. Copy `wrangler.toml.example` to `wrangler.toml` (kept out of git), put in a hard-to-guess project name and the `database_id`, then `npm run db:remote`.
3. `npx wrangler pages project create <name> --production-branch main`.
4. Cloudflare Zero Trust: one service token per laptop; a Self-hosted Access application for `<name>.pages.dev` and `*.<name>.pages.dev` (once the Pages project exists, its address is in the Domain list) with an Allow policy (your emails, one-time PIN) and a Service Auth policy (the tokens). Copy its AUD tag (the application's Additional settings).
5. Secrets: `npx wrangler pages secret put ACCESS_TEAM_DOMAIN` (`<team>.cloudflareaccess.com`), `ACCESS_AUD`, and `PEOPLE`, for example `{"you@example.com":{"person":"anna","name":"Anna"},"<laptop client id>":{"person":"anna","name":"Anna"},"him@example.com":{"person":"ben","name":"Ben"},"<his laptop client id>":{"person":"ben","name":"Ben"}}`.
6. `npm run deploy`, then put the address and each laptop's token into that laptop's Settings.

The API checks Access's signed token on every request (audience, issuer, expiry), so it also refuses anything that did not come through Access.

## Notes

- Everything runs on your own machine. The app listens on 127.0.0.1 only and ignores requests from other websites. Sharing is the only part that goes online, and it sends only what is listed above.
- My notes and favorite text save automatically. Re-processing a video keeps your notes; its favorites are cleared only if the run succeeds, and the video's controls pause while it runs.
- Long videos: snapshots are capped per video (`--max-shots auto` or a number; set it next to Run in the app), on-screen text is read from at most 60 of them, and transcription on CPU takes roughly a quarter to a half of the video's length.
- Running a link that is already done asks what to do: **New version** (a separate copy, v2, v3... - the original and its favorites stay) or **Skip**. On a reel, **+ New version** does the same and **Re-process** replaces that version. Both reuse the downloaded video.
- If memory is low, transcription falls back to smaller Whisper models (base, then tiny).
- For personal reference only: respect creators' rights and each platform's terms.
- The sites change often. If downloads break, update yt-dlp: `python -m yt_dlp -U`
