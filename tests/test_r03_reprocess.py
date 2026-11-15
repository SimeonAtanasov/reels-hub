"""Audit R-03: a Re-process that fails must leave the reel as it was (snapshots, edited images and their originals,
snapshots taken by hand, favorites); one that succeeds moves the old set to the reels folder's _trash instead of
deleting it. Runs the real reels2notes.py on a made-up reel in the temp folder, with stand-ins for Whisper and
easyocr (tests\\standins) and ffmpeg for a 4-second test video. Skipped when ffmpeg or numpy is missing.
Before the fix: the failed run deleted shots\\, shots\\_orig and shots_manual.json."""
import hashlib, json, os, shutil, subprocess, sys
from pathlib import Path
from helpers import run, APP, REAL_APPDATA

HERE = Path(__file__).resolve().parent


def tree(d):
    d = Path(d)
    return {p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()}


def body(env):
    try:
        import numpy  # noqa: F401  (the pipeline needs it for the audio)
    except ImportError:
        print("  skipped: numpy is not installed (it comes with faster-whisper)")
        return
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("  skipped: ffmpeg is not on PATH")
        return
    rid = "20261101_KitRedo0001"
    R = env.out / rid
    (R / "shots" / "_orig").mkdir(parents=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x240:d=4:r=10", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=4", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(R / "video.mp4")], check=True)
    url = "https://www.youtube.com/watch?v=KitRedo0001"
    info = {"id": "KitRedo0001", "title": "Made-up redo test", "upload_date": "20261101", "duration": 4, "webpage_url": url,
            "extractor_key": "Youtube", "channel": "Kit Channel", "uploader": "Kit Channel", "uploader_id": "@kitchannel"}
    (R / "video.info.json").write_text(json.dumps(info), encoding="utf-8")
    for k in range(1, 5):                                  # the old set: 3 automatic, 1 taken by hand, 1 edited
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=0x{k * 40:02x}2040:s=160x120",
                        "-frames:v", "1", str(R / "shots" / f"shot_{k:03d}.jpg")], check=True)
    shutil.copyfile(R / "shots" / "shot_001.jpg", R / "shots" / "_orig" / "shot_002.jpg")   # shot_002 was edited
    (R / "shots.json").write_text(json.dumps({"shot_001.jpg": 0.0, "shot_002.jpg": 1.0, "shot_003.jpg": 2.0, "shot_004.jpg": 1.37}), encoding="utf-8")
    (R / "shots_manual.json").write_text(json.dumps(["shot_004.jpg"]), encoding="utf-8")
    env.r2n.write_note(R, url, info, sorted((R / "shots").glob("*.jpg")), ["[00:00] Old line."], "en", [], "tester")
    hub = {"category": "", "favorites": [{"shot": "shot_002.jpg", "text": "MY EDITED IMAGE TEXT", "corrected": True},
                                         {"shot": "shot_004.jpg", "text": "MY HAND-TAKEN SNAPSHOT", "corrected": False}]}
    (R / "hub.json").write_text(json.dumps(hub), encoding="utf-8")
    old_set = {k: v for k, v in tree(R).items() if k.startswith("shots") and k != "shots.new"}
    before = tree(R)

    def pipeline(fail):
        # the stand-ins first, then exactly the places this test's Python found numpy and the rest in (an existing
        # PYTHONPATH, the per-user packages), so the pipeline sees the same modules as this process
        path = [str(HERE / "standins")] + [p for p in sys.path if p and os.path.isdir(p)]
        e = dict(os.environ, PYTHONPATH=os.pathsep.join(path), PYTHONIOENCODING="utf-8", REELS_HUB_CPU="1")
        if REAL_APPDATA:                    # reels2notes.py never reads APPDATA (no config.json), but Python looks for
            e["APPDATA"] = REAL_APPDATA     # the per-user packages there (numpy on many Windows installs)
        e.pop("RH_TEST_WHISPER_FAIL", None)
        if fail:
            e["RH_TEST_WHISPER_FAIL"] = "1"
        r = subprocess.run([sys.executable, str(APP / "reels2notes.py"), "--out", str(env.out), "--redo", "--target", rid,
                            "--max-shots", "auto", url], env=e, capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = r.stdout + r.stderr
        if "No module named" in out:                    # say where this test found numpy, for the report
            import numpy
            out += f"\n(this test's numpy: {numpy.__file__}; PYTHONPATH given: {e['PYTHONPATH'][:300]})"
        return r.returncode, out

    rc, out = pipeline(fail=True)
    after = tree(R)
    env.check("failed Re-process: the pipeline reports the failure", rc != 0, out[-300:])
    env.check("failed Re-process: every file of the reel is as it was", after == before,
              f"gone {sorted(set(before) - set(after))[:5]}, new {sorted(set(after) - set(before))[:5]}")
    env.check("failed Re-process: no shots.new left", not (R / "shots.new").exists())
    env.check("failed Re-process: nothing moved to _trash", not (env.out / "_trash").exists())

    (R / "shots.new" / "shots").mkdir(parents=True)        # what a run that was killed would leave
    (R / "shots.new" / "shots" / "junk.jpg").write_bytes(b"x")
    rc, out = pipeline(fail=False)
    env.check("good Re-process: it finishes", rc == 0, out[-400:])
    trash = [p for p in (env.out / "_trash").iterdir()] if (env.out / "_trash").exists() else []
    env.check("good Re-process: the old set is in _trash\\<reel>-shots-<time>", len(trash) == 1 and trash[0].name.startswith(rid + "-shots-"),
              [p.name for p in trash])
    if len(trash) == 1:
        env.check("good Re-process: the old set is there byte for byte (edited original, hand-taken list)", tree(trash[0]) == old_set)
    new = sorted(p.name for p in (R / "shots").glob("*.jpg"))
    env.check("good Re-process: a new set is in place", len(new) >= 2 and (R / "shots.json").is_file(), new)
    env.check("good Re-process: no shots_manual.json, no shots\\_orig, no shots.new", not (R / "shots_manual.json").exists()
              and not (R / "shots" / "_orig").exists() and not (R / "shots.new").exists())
    note = (R / "note.md").read_text(encoding="utf-8")
    env.check("good Re-process: note.md lists the new snapshots and the new transcript", all(f"shots/{n}" in note for n in new)
              and "Test line at 0 seconds." in note)


run("r03", body)
