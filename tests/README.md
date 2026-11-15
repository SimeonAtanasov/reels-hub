# Reels Hub tests

Checks that run the app's own functions on a throwaway reels folder and vault in the temp folder. They never read or write your reels folder, your vault, your `config.json` or Cloudflare, need no internet, and use made-up names and links only. Python's standard library is enough (no yt-dlp, Whisper, easyocr or ffmpeg).

Run them in the app folder (Command Prompt):

```
py tests\run_tests.py
```

One line per file, then `N of N test files passed`; the exit code is 1 when anything failed, and the failing checks are listed. `py tests\run_tests.py r01` runs only the files whose name contains `r01`. Each file can also run on its own: `py tests\test_r01_markers.py`.

| File | Audit finding | What it proves |
|---|---|---|
| `test_r01_markers.py` | R-01 | Send to Obsidian, the channel note and the full video's transcript change a vault note only between one whole start marker line and one whole end marker line. A missing, doubled or swapped marker leaves the note byte for byte as it was (and no image is copied) with a message; a marker quoted inside a sentence, a title or a favorite's text is only text. |
| `test_r02_channel_note.py` | R-02 | Remove a channel with its notes kept, run it again: the channel note keeps your lines and the videos' notes are taken again (no "(2)" twins). An emptied `_channel.json` is made again without losing the note; an unreadable one is not written over; a file of yours with the channel note's name is never touched. |
| `test_r14_quoted_id.py` | R-14 | Only a whole id marker line makes a note the video's note: a note of yours that quotes the marker is not indexed, changed or moved. |
| `test_r41_version.py` | R-41 | A copy of the app on a free port: the black window, `/api/ping`, Settings and the page carry `Reels Hub <date> <8 hex digits>`; after the files are replaced while it runs, the stamp stays and the app says the files on disk are newer. |

`helpers.py` holds the shared parts: the temp folders (it refuses to run if the app's folders are not inside them), a sample reel and transcript folder made with the pipeline's own `write_note`, and the checks. A test that names an audit finding fails on the code from before its fix.

The audit kit (`to-do\reels-hub-audit-kit\`, local only) has the bigger checks: the stand-in yt-dlp, Whisper and easyocr, the sample library and the Playwright page checks. They run in a Linux sandbox; these tests are the part that runs on Windows as it is.
