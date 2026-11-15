"""Shared helpers for the Reels Hub tests.

Every test makes a throwaway reels folder and vault in the temp folder, imports the app from the folder above
(reels_hub.py and reels2notes.py as they are on disk) and calls the app's own functions on them. Nothing here
reads or writes your reels folder, your vault, your config.json or Cloudflare, and nothing needs the internet,
yt-dlp, Whisper or ffmpeg. All names and links are made up.
"""
import json, os, shutil, sys, tempfile, traceback
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
REAL_APPDATA = os.environ.get("APPDATA")      # before Env replaces it: on Windows Python's own per-user packages
                                             # (numpy, faster-whisper) live under it, which a pipeline run needs
FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60 + b"\xff\xd9"      # only copied by the app, never decoded here


class Env:
    def __init__(self, name):
        self.root = Path(tempfile.mkdtemp(prefix=f"reels-hub-test-{name}-"))
        self.out = self.root / "reels"
        self.vault = self.root / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.out.mkdir()
        os.environ["APPDATA"] = str(self.root / "appdata")        # never the real config.json
        sys.path.insert(0, str(APP))
        import reels_hub as rh
        import reels2notes as r2n
        self.rh, self.r2n = rh, r2n
        cfg = {"name": "Tester", "person": "tester", "out": str(self.out), "vault": str(self.vault), "sub_lang": "en"}
        rh.apply_config(cfg)
        rh.CONFIG.clear()
        rh.CONFIG.update(cfg)
        root = str(self.root.resolve())
        if not (str(rh.OUT.resolve()).startswith(root) and str(rh.VAULT.resolve()).startswith(root)):
            raise SystemExit("refusing to run: the app's folders are not the test's temp folders")
        self.ok, self.bad = [], []

    # ---------- sample data ----------
    def make_reel(self, vid, title, platform="YouTube", date="20261101", caption="A made-up caption.",
                  transcript=("[00:00] Hello there.", "[00:05] This is a test."), nshots=3, hub=None):
        """A reel folder as the pipeline leaves it (note.md by the pipeline's own write_note)."""
        urls = {"YouTube": f"https://www.youtube.com/watch?v={vid}", "TikTok": f"https://www.tiktok.com/@kit.user/video/{vid}",
                "Instagram": f"https://www.instagram.com/reel/{vid}/", "X": f"https://x.com/kituser/status/{vid}"}
        url = urls[platform]
        folder = self.out / f"{date}_{vid}"
        (folder / "shots").mkdir(parents=True)
        shots = []
        for k in range(1, nshots + 1):
            p = folder / "shots" / f"shot_{k:03d}.jpg"
            p.write_bytes(FAKE_JPEG)
            shots.append(p)
        (folder / "shots.json").write_text(json.dumps({p.name: i * 2.0 for i, p in enumerate(shots)}), encoding="utf-8")
        info = {"id": vid, "title": title, "description": caption, "upload_date": date, "duration": 30,
                "webpage_url": url, "extractor_key": {"YouTube": "Youtube", "TikTok": "TikTok", "Instagram": "Instagram",
                                                      "X": "Twitter"}[platform],
                "channel": "Kit Channel", "uploader": "Kit Channel", "uploader_id": "@kitchannel"}
        (folder / "video.info.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        self.r2n.write_note(folder, url, info, shots, list(transcript), "en", [(shots[0].name, "ON SCREEN")] if shots else [],
                            "tester")
        (folder / "hub.json").write_text(json.dumps(hub or {"category": "", "favorites": []}), encoding="utf-8")
        return folder

    def make_transcript(self, key, vid, title, name=None, date="20261020"):
        """A transcript-only folder as `reels2notes.py --transcripts` leaves it (no vault note yet)."""
        name = name or key
        url = f"https://www.youtube.com/watch?v={vid}"
        cdir = self.out / "_transcripts" / key
        folder = cdir / f"{date}_{vid}"
        folder.mkdir(parents=True)
        info = {"id": vid, "title": title, "description": "Made-up description.", "upload_date": date, "duration": 60,
                "webpage_url": url, "extractor_key": "Youtube", "channel": name, "uploader": name, "uploader_id": "@" + key}
        (folder / "video.info.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        self.r2n.write_note(folder, url, info, [], ["[00:00] First line.", "[00:08] Second line."], "en", None, "tester",
                            extra=["transcript: youtube captions"])
        ch = cdir / "_channel.json"
        if not ch.exists():
            ch.write_text(json.dumps({"key": key, "name": name, "id": "UC" + key, "handle": "@" + key,
                                      "url": f"https://www.youtube.com/@{key}/videos", "platform": "YouTube",
                                      "at": "2026-11-01 10:00"}), encoding="utf-8")
        return folder

    def note_of(self, folder):
        """Make the reel's vault note (as the first listing does) and return its path."""
        info = self.rh.ensure_vault_note(folder, self.rh.parse_note(folder))
        return self.vault / info["path"]

    def set_favorites(self, folder, favs):
        st = json.loads((folder / "hub.json").read_text(encoding="utf-8"))
        st["favorites"] = favs
        (folder / "hub.json").write_text(json.dumps(st), encoding="utf-8")

    def tree(self, root=None):
        """{relative path: bytes} of every file under the vault (or root)."""
        root = Path(root or self.vault)
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}

    # ---------- results ----------
    def check(self, name, cond, detail=""):
        (self.ok if cond else self.bad).append(name)
        print(("  ok   " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
        return cond

    def finish(self):
        print(f"{len(self.ok)} ok, {len(self.bad)} failed")
        shutil.rmtree(self.root, ignore_errors=True)
        sys.exit(1 if self.bad else 0)


def run(name, body):
    """Run body(env) and exit with 0 when every check passed."""
    env = Env(name)
    try:
        body(env)
    except Exception:
        traceback.print_exc()
        env.bad.append("exception")
    env.finish()
