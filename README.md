# Reels Hub

Turn Instagram reels, TikTok videos, YouTube videos/Shorts and X posts (or anything yt-dlp can download) into searchable, printable notes - built for saving training videos like kettlebell drills.

For each reel it saves:
- the video and caption
- a timestamped transcript of what is said (Whisper, runs locally)
- snapshots: **Auto** by default - a frame every 2 s for short videos, gradually wider for longer ones (about 5 s at 6 min, 12 s at 36 min, 20 s from 1.5 h), plus every scene cut, up to 400 (very long videos get a wider gap to stay under that). Or pick a fixed maximum in the app (60-400), which is never exceeded, or **Every ... s** and type the seconds between snapshots yourself (0.5 to 600): exactly one snapshot every that many seconds, with no extra ones at scene cuts, at most 1000 per video (a longer video gets a slightly wider gap, and the log says so). Delete the ones you don't need, keep your favorites
- on-screen text read from the snapshots (OCR)
- a Markdown note per reel, ready for Obsidian

The **Reels Hub** web app (local only) lets you paste links and run them, browse reels grouped by category or source and sorted by date posted, date added or title, star snapshots as favorites, reorder them, correct the text under each one, print them on A4, and send them to an Obsidian vault.

While watching, **📸 Snapshot** (or the S key; Shift+S also stars it) captures the current frame; it slots into the snapshots in time order. Every time shown on a snapshot, favorite or on-screen text is clickable and plays the video from that moment (P in the enlarged view).

The left side has two panes, as in a map app: **Filters** (search, Group, Sort, Source, then the **Category**, **Channel** and **Tags** lists, in the Videos and the Transcripts tab alike) and the **list** (links box, Run, progress, Videos | Transcripts, the reels). Each folds with **‹** to a thin rail and opens again with **›** (or a click on its name), so the video gets the room; the app remembers which are open. Drag the edges between the list, the video and the text to give each one more or less room, and drag the bar under the video to make it taller (a wide YouTube video grows when you widen its column instead). The bar between the tools at the top of the left column (links box, filters) and the reel list works the same way: drag it up for a longer list, and the tools above scroll on their own. Double-click an edge or the bar to go back to the standard size. The sizes are remembered in that browser on that computer.

Snapshots can be edited inside the app (crop, rotate, draw, arrows, boxes, text, blur, undo) or opened in Paint; the original is always kept for **Reset to original**. Images and video links can be copied to paste into chat apps.

**Instagram photo slideshows** (posts with several slides, or a single photo) work too: the app downloads every slide's full image, joins the slides into one video (each photo shows for 4 seconds, video slides play as they are), saves each photo as a snapshot and reads its text. **◀ Slide** and **Slide ▶** under the video (or the , and . keys) step through the slides, and times show as "Slide 3" instead of 00:10. A slideshow of photos only has no transcript; its Obsidian note puts the slides' text under Content, in order, and has no Original text. Video slides in a mixed post get a transcript and snapshots as usual. Photo posts on other sites (for example TikTok photo posts) are not supported yet; the app says the post has no video.

**Transcripts only**: paste a YouTube channel link (or a playlist, or single video links) and press **Transcripts** instead of Run: every video gets its transcript as a note in Obsidian, without downloading the video. See [Transcripts of a whole channel](#transcripts-of-a-whole-channel-or-just-the-text).

Two people with their own laptops can **share** videos with each other on purpose: see [Sharing between two laptops](#sharing-between-two-laptops-optional).

**Notes follow the category.** When you change a video's category (or a transcript's), its Obsidian note moves at once to `<platform>\<category>\` in your vault, for example `YouTube\Training\` or `Instagram\Training\`, and its Category line and category tag change with it; Unassigned puts it back in the platform folder (`YouTube\`). Renaming a category moves its notes to the new folder; deleting one sends them back to the platform folder. New videos get their note straight in their category's folder. Only notes still in the app's folders move (the platform folder, a category's or a channel's folder): a note you filed somewhere else yourself stays where it is (its Category line still follows). The file name never changes, so links to the note keep working; if a note of the same name is already in the target folder, it is not moved. Folders left empty stay (delete them yourself if you like). A channel you put in a category gets its own folder inside that category's folder, as in the app's list: `YouTube\Red light therapy (RLT)\Mychondria\` (the channel's own note sits there too); a channel run's transcripts that have no category yet sit in `YouTube\Channels\<channel>\`; other videos sit directly in their category's folder (`Instagram\Training\`). Picked the wrong category? Pick the right one: the note simply moves again, with all you wrote in it (it is moved, never rewritten or copied). For the notes made before this: **Settings > Move notes to category folders** shows how many would move, with examples, and moves them when you confirm.

**Channels inside your categories.** Your categories are the top level; channels (and creators) are groups inside them. In a category, and in Unassigned, the videos of one channel are shown together under the channel's name when there are two or more of them, or when that category is the channel's own; single videos of other creators stay loose under the category. This works the same under Videos and Transcripts (group by Category), and in the gallery. To put a whole channel in a category, drag the channel's line onto the category, or press the folder button (📁) on the channel's line and pick one (or **+ New category...**). Its videos that are in Unassigned move there, their notes go to `<platform>\<category>\<channel>\`, and its new videos start there, transcripts and downloaded videos alike. A video you moved into another category yourself stays where you put it. Pick **No category** in the same menu to take the channel out again (its videos go back to Unassigned). Example: create **Red light therapy (RLT)**, then drag your two red light channels onto it: both show inside it, each with its own videos. Grouped by Channel in the Transcripts tab, the folder button is on each channel too, and the line under the channel name says its category. The **⊟** button next to the Videos | Transcripts tabs collapses every group of the list that is showing (the categories and the channels inside them); it then turns into **⊞**, which opens them all again. A category with two or more channel groups also has its own **⊟** next to its count: it folds just the channels inside that category (the category stays open, so its loose videos still show), and **⊞** opens them again. Like the other folds it is remembered in the browser. Each list (Videos, Transcripts) and each grouping keeps its own folds, and they are remembered in the browser. Until 2026-11-09 each channel was a category of its own (the Channels group); on the first start of this version those categories turn into channel groups by themselves, in the background (the status line next to Run says **Channel groups: k / n** meanwhile, and the page refreshes itself when it is done; the black window prints the progress too): their videos go to Unassigned, where they show under their channel, ready to be dragged into your categories. A channel category you had renamed (or that held videos of other channels too) stays as your own category and its channel stays in it.

**Getting the video of a transcript you already have** (Get the video, or Run with its link): the video is downloaded and processed in full, and it keeps the category and tags you gave the transcript (not its channel's); the same Obsidian note stays where it is, its caption text is replaced by the video's own transcript, and the Transcripts tab shows one entry (VID) that opens the video.

**The filter lists (Category, Channel, Tags)** work like a shop's filters. Each shows its first 5 rows; **Show 10 more** adds ten, **Show all 52** opens the whole list and **Show less** folds it back. A list longer than 8 has a search box at the top (*Search categories (52)*): type a few letters and it shows every match, also rows that were folded away, without caring about accents (`energie` finds *Énergie*); **Esc** empties it. A row you ticked always stays in view. Category starts with **Unassigned**, then your categories in your order. **Channel** lists every channel or creator with the site's badge, the ones with the most videos first. Tick rows to filter: within a list any ticked row matches; between lists all must match (Category *Training* and Channel *Jamie Brindle* shows his Training videos). Each list counts what the other lists leave, so after ticking a channel the Category list shows only the categories that channel has videos in. **clear** next to a list's name unticks that list; **Clear filters** unticks everything. What you tick is remembered in that browser.

**Tags.** A video has one main category and any number of tags (for example category Training, tags `training/kettlebell` and `training/mobility`). Tags are kept in lower case. Give them a family with a slash (`ai/claude-code`, `ai/agents`, `tech/sql`): typing `ai/` in the Tags list's search shows the whole family. Under **Category** on a video (and on a transcript) type a tag in the **+ tag** box and press Enter: an existing one is suggested as you type (your categories too), a new one is made for you; × takes a tag off. Several videos at once: select them (Ctrl+click) and press **+ Tag** in the bar at the bottom. The **Tags** list in the Filters pane ticks like the categories (a video with any ticked tag shows) and counts per tag; **+ New tag** makes one, and on each row ✎ renames it and × deletes it (taken off every video). Search finds tags too. Each tag also goes on the Obsidian note's Tags line in lower case with spaces as hyphens, the slash kept: `ai/claude code` becomes `#ai/claude-code`, a nested tag, so Obsidian's tag pane shows **ai** with claude-code, agents and the rest folded under it (the platform and category tags stay capitalised, `#Instagram #AI`); taking a tag off removes only the tag the app put there, never the platform or category tag or one you typed yourself. Tags stay on your laptop and are not sent with a share. **Creator tag:** every note also gets the creator's handle as a nested tag, `#creator/angus-sewell` (dots and underscores as hyphens), so Obsidian's tag pane lists each creator's notes under **creator**, whatever category folder they are in. It is only in the notes, not in the app's Tags list (use the Channel filter there). New notes get it when they are made; for older notes, one pass adds it to all of them (`POST /api/notes/creatortags`, `{"dry": true}` counts first; it runs in the background, `{"status": true}` shows progress; nothing else in a note changes, and a note without the app's id marker is left alone).

## Install (Windows)

**Easiest:** download this folder (on GitHub: Code, Download ZIP, then extract it, e.g. to `Documents\reels-hub`) and double-click **`install.bat`**. It installs Python, ffmpeg and Deno if they are missing, the Python packages, and puts a **Reels Hub** shortcut on the desktop. If it says Windows needs a fresh window, close it and run `install.bat` once more.

By hand instead:

```
pip install yt-dlp faster-whisper easyocr
winget install Gyan.FFmpeg
winget install DenoLand.Deno
```

Open a new terminal after installing ffmpeg.

### Use the graphics card (optional, NVIDIA only)

With an NVIDIA graphics card, the transcript and the on-screen text run on the card: the same models on full-size snapshots, so the same text, only several times faster (a 1:30 reel took about 6 minutes on the processor). The app uses the card by itself when it works. If it doesn't, or stops working halfway, that step goes on with the processor and the Log says why. Intel or AMD graphics are not used.

Check what you have (Command Prompt): `powershell -NoProfile -Command "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"`. If it lists an NVIDIA card, install the graphics-card build of PyTorch (about 2.5 GB). Older cards (GTX 10 series such as the GTX 1060) need exactly this version: PyTorch 2.15 and later no longer support them.

```
py -m pip uninstall -y torch torchvision
py -m pip install torch==2.14.0 torchvision==0.29.0 --index-url https://download.pytorch.org/whl/cu126
py -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

The last line should print `2.14.0+cu126 True` and the card's name. The transcript (Whisper) uses the CUDA libraries that come with that PyTorch. If its Log line says the card did not work for the transcript and mentions a missing `cublas` or `cudnn` file, also run `py -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"`.

- The Log shows "transcript on the graphics card" and "on-screen text on the graphics card", and every video's `done in` line lists how long each step took.
- Don't upgrade torch afterwards (`pip install --upgrade torch`, or a tool that does it): that brings back the processor-only build. Run the two install lines again to repair it. `install.bat` does not change an installed torch.
- To use the processor anyway: `set REELS_HUB_CPU=1` before `reels-hub.bat` in the same Command Prompt window.
- The estimated time learns the card's speed separately, so it is right after one or two videos.

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

It opens http://127.0.0.1:8765. Paste links, one per line, and press **Run**. The first run downloads the speech and OCR models (a few minutes). A new video starts in its channel's category when you have given that channel one (see Channels inside your categories); otherwise it starts in Unassigned, shown there under its channel or creator once there are two of theirs. Move it to another category whenever you like. Videos added from the Inbox take the sender's category instead, and Re-process keeps the category the video has.

**A whole channel or playlist with Run:** paste a YouTube channel link (`https://www.youtube.com/@name`), a playlist link (`https://www.youtube.com/playlist?list=...`) or a TikTok profile link and press **Run**. The app lists it first (a few seconds, up to a minute for a big channel) and asks which videos to download: **All not downloaded yet**, **Only those without a transcript either**, or **The newest N** (for a playlist: the first N). It shows how many are already downloaded, how many you have as a transcript only and how many are new. The chosen videos wait in line in groups of 10 ("Mychondria: 1-10 of 48"), so you can set a group for Later, move it or take it out like any Run; videos already downloaded are never fetched again. Each video is processed in full (about as long as one video on its own), so a big channel takes many hours and several GB: start with the newest 10. A watch link that also carries `&list=` is taken as the single video; for the playlist, copy the playlist page's link. **Transcripts** takes the same channel, playlist and profile links, for the text only.

**Links from an Instagram or TikTok chat:** neither site has a "copy all links" for a chat. [tools/instagram-chat-links.md](tools/instagram-chat-links.md) and [tools/tiktok-chat-links.md](tools/tiktok-chat-links.md) each have a short script for the browser console that lists every reel or video link in a chat (for example the one with yourself, where you send them to keep them), oldest first, ready to paste into the links box. Set `AFTER` in it to the last link of your previous list to get only the new ones. TikTok photo posts are listed apart, as Reels Hub cannot take them yet.

**The next video while one is running:** paste its link and press **Run** again. It waits under **Waiting** (below the progress bar) and starts by itself when the current one finishes; line up as many as you like. The app works on one video at a time on purpose: transcription and on-screen text reading keep the processor busy, so two at once would each take about twice as long.

**Videos and transcripts at the same time (two lanes):** a **Transcripts** run and a video run (**Run**, **Re-process**, **+ New version**, Inbox **Add**) go side by side, so a channel's 1,000 transcripts don't hold up the reel you want now. Each has its own progress box, one under the other, with a small head line when both run (*VIDEOS instagram.com/reel/... 0 / 1 done*, *TRANSCRIPTS @channel 57 / 1276 done*); the status says *Running · 2 at once*. Inside each lane it is still one at a time: a second video waits for the first, a second Transcripts run waits for the first. Only when the video and the transcripts come from the **same site** (a YouTube video while YouTube transcripts run) do they take turns, so the site doesn't see two downloads from you at once: the transcripts run finishes the video it is on, waits while the video downloads (its box says *Waiting: a video is downloading from YouTube*), then goes on by itself. Transcribing, snapshots and on-screen text happen on your laptop, so the transcripts carry on during those. An Instagram, TikTok or X video while YouTube transcripts run: nothing waits. A video without captions in a transcripts run is transcribed with Whisper, and then both lanes share the processor, so each is a bit slower for that video. The **Log** has a tab per lane (**Videos**, **Transcripts**) once both have lines; it opens on the one you just started. **×** next to a waiting item takes it out of the line (its link goes back into the box). **Re-process**, **+ New version** and Inbox **Add** wait in the same line, first come, first served; a video waiting to be re-processed is paused (no edits) until it is done. A link that is already running or waiting is not added twice. The **Log** keeps every video's lines, each next one under a `== next in line` header, so you can still read why an earlier one failed. Each waiting item has **↑ ↓** to move it (or drag it up or down the list) and **❚❚ Later**: it stays in line, marked LATER, and is skipped until you press **▶** on it (then it runs when its turn comes). **❚❚ Pause all** at the top of the Waiting list sets everything waiting for Later in one go: the video or transcripts run going now still finishes, and after that nothing starts by itself (for example before you change the reels folder in Settings, which waits until nothing runs). **▶ Resume all** lets them all run again in their order, STOPPED ones too. The line is saved on your laptop (`_queue.json` in the reels folder): close Reels Hub and it is all still there next time, in the same order, Later items still Later. What was running when you closed it comes back first in line, marked STOPPED and set for Later, so nothing starts by itself unexpectedly; press **▶** to run it again (a channel's transcripts then skip what is already done). The others start by themselves as usual. Items set for Later don't block Settings. The Waiting list is the same in the Videos and Transcripts tabs. When both lanes have something waiting it shows them in two parts, **Videos** and **Transcripts**, each numbered from 1: ↑ ↓ and dragging move an item within its own part (each lane keeps its own order), and a waiting item only waits for its own lane.

**The laptop stays awake while Reels Hub works:** while a video or a transcripts run is going, or anything waits in line to start by itself, Reels Hub asks Windows not to go to sleep (the screen can still turn off), so a long line is not paused halfway. When the line is done, or only items set for Later wait, Windows may sleep again as usual. The black window says when this starts and stops. Closing the lid, the Sleep button or a low battery still put the laptop to sleep: for a long line keep it plugged in with the lid open. To check it, in a Command Prompt opened as administrator: `powercfg /requests` lists python.exe under SYSTEM while Reels Hub keeps the PC awake.

The top right of the page shows **Connected to this PC** with a green dot while the app answers. Keep its black Command Prompt window open while you use it: when that window is closed, the status turns yellow within a few seconds and says *The server does not answer: start Reels Hub again*. Start it again (**Reels Hub** on the desktop, or `reels-hub.bat`) and the open page connects by itself. Changes made while it is yellow can't be saved (the app tells you when a save fails).

Command line only:

```
python reels2notes.py https://www.instagram.com/p/XXXXXXXX/
python reels2notes.py -f links.txt
python reels2notes.py --transcripts https://www.youtube.com/@name/videos   # the folders only: the app makes the notes
```

## Transcripts of a whole channel, or just the text

Sometimes you want the words, not the video: everything a creator has said, searchable in Obsidian. Paste one or more links in the box and press **Transcripts** instead of Run:

- a **YouTube channel** (`https://www.youtube.com/@name` or `.../@name/videos`): every video in its Videos tab. Shorts and live streams are left out unless you paste their tab (`.../@name/shorts`, `.../@name/streams`)
- a **playlist** link: every video in it
- a **TikTok profile** (`https://www.tiktok.com/@name`): every video of that creator. TikTok changes its site often, so this is less reliable than YouTube; if it fails, update yt-dlp or try with `cookies.txt`
- **single video links** from YouTube, TikTok, Instagram or X. Whole accounts on Instagram and X cannot be listed (Instagram blocks it, X offers nothing to list), so paste their video links one by one; X usually needs `cookies.txt`

No video is downloaded. For a YouTube video the transcript is its captions, in the **Transcript language** from Settings first (default `en`): the creator's subtitles in that language, else YouTube's automatic captions of that language's own audio (`en-orig`). Only when the video has neither does it fall back to its own language: the creator's subtitles, else the automatic captions (the untranslated track; machine translations are never taken). This matters for channels with YouTube's automatic dubbing, where the video's language can point at a dub (for example Arabic). Leave the setting empty to always use the video's own language. TikTok videos usually carry captions too, and those are taken the same way. A video without captions, and every Instagram or X video (no captions there), is transcribed with Whisper from its audio only, as a normal Run would (slower, on the graphics card when you have one). A video that is already in your library, in full or as a transcript, is skipped, so you can paste the same channel again next month and only the new videos are done.

Each video gets:

- an **Obsidian note made from your own `'YouTube Template.md`**, read from the vault's Templates folder each time (so a change to the template carries over): date, time, Title, Source, Tags, Related, the formula line, the six sections to fill in, then the transcript under Content (paragraphs) and Original text (with times). Nothing else is added; the app's own bookkeeping (the video's id, where the text came from) sits in `<!-- reels-hub -->` comments that Obsidian does not show. Videos from a channel, playlist or profile link go to `YouTube\Channels\<channel name>\` (or `TikTok\Channels\<creator>\`), or straight to `YouTube\<category>\<channel name>\` when you have put that channel in a category; single video links go to `YouTube\` like any other note (or into their channel's category folder, if it has one). TikTok links use the TikTok template (Creator and Description filled in); Instagram and X links keep the app's usual video-note format in `Instagram\` and `X\`
- a line in the **channel note**, `'<channel name> (channel).md` in the channel's folder (`YouTube\Channels\<channel name>\`, or `YouTube\<category>\<channel name>\` once you put the channel in a category): every video of that channel with its date, length and where the text came from, linked to its note (newest first). Only the list between its markers is rewritten; write your own notes anywhere else in it
- a small folder in the reels folder (`_transcripts\<channel>\<date>_<id>\`, the note's text and the video's details), nothing else

The **Transcripts** tab above the list (next to **Videos**) is the catalogue: each channel or creator as a folder (with its site's badge) and its videos, newest first, with a badge for where the text came from (CC = the site's captions, AUTO = YouTube's automatic captions, W = Whisper). Click a video to read its transcript: every time opens the video on YouTube at that moment. From there: **Open note** (the link under the title), **Get the video** (downloads it and processes it in full: Whisper transcript, snapshots, on-screen text; it then shows under Videos, and its note is kept: the caption text is replaced by Whisper's transcript, the video is added by Send to Obsidian as usual), **Copy transcript**, **Copy AI prompt** and **Remove** (the folder goes to `_trash\_transcripts`, and if you tick it, the note to the vault's trash). The × on a channel removes all its transcripts. The list is grouped by **Category** (your categories, each with its channels inside; see Channels inside your categories); grouped by **Channel**, every channel sits inside one **Channels** group (fold it with its arrow, each channel inside folds too). **Categories work here as for videos**, with the same list of categories: a channel's transcripts start in the channel's category once you have given it one, otherwise in Unassigned under the channel; pick another category on a transcript, or drag it onto one, group the list by Category, Channel or None, sort it, and filter by category. A transcript's category also goes into its Obsidian note as a tag on the Tags line (`#YouTube #Training`). Search looks at titles, channels and categories here. **Your downloaded videos are in the catalogue too**, with their own transcript (badge VID), under their channel or creator (or **Unknown channel** when the video has no creator data); click one and the Videos tab opens with that video. If you first got only the transcript and later the whole video, the two are one entry, joined by the video's id from its link: it then shows the video's transcript, title and category (re-process the video and the entry follows), and a click opens the video. Changing the category of such an entry changes the video's category. Remove is not offered for them here (delete the video under Videos). Transcripts runs have their own lane: they run beside a video run, and only wait for each other (**Waiting** under the progress bars, the **Transcripts** part when videos wait too). They show in the **Log** (its **Transcripts** tab) video by video; their progress box counts videos. A video downloading from the same site makes them wait a moment (see Videos and transcripts at the same time, under Run).

**Pause, Stop, Resume.** During a transcripts run, **Pause** and **Stop** sit in its progress box; both let the current video finish first, and neither touches a video run going at the same time. Pause holds the run (the bar says *paused*) until you press **Resume**; Stop ends it. Under each channel in the Transcripts tab a line says how its last run went: *Running · 57 of 255 done · next: ...*, *Stopped after 57 of 255 · next: ... · 198 left*, or *Interrupted* when the app was closed or the run broke off, each with a **Resume** button that lists the channel again and does only what is not here yet (pasting the link again does the same). After a run with failed videos the line offers **Retry**. When some of a channel's transcripts came from captions in another language than the one in Settings, a line under the channel says how many, with **Redo**: it lists them, asks, moves their folders to `_trash\_transcripts` and their notes to the vault's trash, and runs the channel again so they come back in your language with the same category. Videos with no captions in your language come back as they were.

How long it takes: roughly 10 seconds per video with captions, so a channel of 300 videos in under an hour, and a few MB on disk. YouTube limits how fast captions can be fetched: the app waits between videos and, when YouTube answers "429 Too Many Requests", waits a minute and tries again; after three refusals in a row the run stops and says so, and what is done stays done (just press Transcripts again later). Install Deno (see Install) before a big run: yt-dlp uses it for the checks YouTube now asks for; without it some videos or captions are refused. Members-only, private and unavailable videos are listed as failed in the Log and left out. If YouTube asks you to sign in, `cookies.txt` next to the app works here too.

## Where things are stored

| What | Default location | Change with |
|---|---|---|
| Videos, snapshots, notes, favorites, categories | the reels folder from Settings (default `Downloads/reels`); deleted reels in its `_trash`, and the Delete question names that folder (for example `D:\reels\_trash`) | Settings, or `--out PATH` |
| Obsidian notes | new notes in `<vault>/YouTube`, `/TikTok`, `/Instagram`, `/X` - move or rename them freely, the app finds them again (it reads the vault once to find all moved notes, at most every 2 minutes, and the console says how long that took); the video and favorite images in `910_Attachments/ReelsHub/<reel>` | `--vault PATH` |
| Shares waiting in your Inbox | `_inbox` in the reels folder (dismissed ones in `_trash/_inbox`) | - |
| Transcript-only videos (the Transcripts tab) | `_transcripts/<channel>/<date>_<id>` in the reels folder (removed ones in `_trash/_transcripts`); their notes in `<vault>/YouTube/Channels/<channel>` or `<vault>/YouTube/<category>/<channel>` | Settings |
| The two lanes taking turns on one site | `_transcripts/_yield.json` (a video is downloading) and `_transcripts/_trbusy.json` (a transcripts run is fetching), there only for seconds while a run goes | - |
| Your name, folders, sharing token | `%APPDATA%\ReelsHub\config.json` on this computer | Settings |

The vault comes from Settings. If none is set or it is not found, Obsidian sync is switched off.

Notes follow the vault's video template: date and time, Title, Source, Tags, Related (MOC links), Creator; then the AI sections (Obsidian Tags, Summary, Main Takeaways, Key Concepts, Reflective Questions, Actionable Insights, Compare and Contrast) left ready to fill, Favorites, My notes, Description, Content (transcript in paragraphs) and Original text (with timestamps). **Send to Obsidian** also copies the video into the vault and plays it at the top of the note, under the header lines (older notes get it there too the next time you send them). The vault is outside this app's folder, so videos never go to GitHub; if your vault syncs (OneDrive, Obsidian Sync), the videos sync with it. YouTube notes start with `'` and TikTok notes with a backtick, like the templates. **Copy AI prompt** in the app copies the vault's `$Obsidian AI prompts` with the transcript, for ChatGPT or Claude.

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

- **Add** downloads and processes the video on that laptop, takes the favorites again at the same moments (with the sender's edited images), fills in their text and Corrected marks, the notes and the category, and marks the reel as shared (↘ name). If you already have the video, it offers a new version instead. Adds wait in the same line as your Runs, first come, first served (see **Waiting** under the progress bar). If the download fails, the share stays in the Inbox with the reason; press Add again later.
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
- My notes and favorite text save automatically. Re-processing a video keeps your notes; its favorites are cleared only if the run succeeds, and the video's controls pause while it runs (or waits in line).
- Long videos: snapshots are capped per video (`--max-shots auto`, a number, or `every:5` for one every 5 seconds; set it next to Run in the app, where your choice is remembered), on-screen text is read from at most 60 of them, and transcription on CPU takes roughly a quarter to a half of the video's length (much less on an NVIDIA graphics card, see [Use the graphics card](#use-the-graphics-card-optional-nvidia-only)). The Log's `done in` line shows how long each step took, and `post checked in` how long the Instagram check took.
- Running a link that is already done asks what to do: **New version** (a separate copy, v2, v3... - the original and its favorites stay) or **Skip**. On a reel, **+ New version** does the same and **Re-process** replaces that version. Both reuse the downloaded video.
- Instagram links are checked first (one quick extra request) to see whether the post is a slideshow. Slideshows keep their slide images in a `slides` folder inside the reel's folder, so Re-process and New version work without downloading again. A shared slideshow is rebuilt the same way on the other laptop, so favorites land on the same slides; both laptops need this version of the app.
- If memory is low, transcription falls back to smaller Whisper models (base, then tiny).
- For personal reference only: respect creators' rights and each platform's terms.
- The sites change often. If downloads break, update yt-dlp: `python -m yt_dlp -U`
