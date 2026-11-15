"""How much slower Reels Hub's saves get when every write is forced onto the disk (audit R-17).

    py tests\\time_writes.py <a folder on the reels drive> <a folder on the vault's drive> [number of notes]
    for example:  py tests\\time_writes.py D:\\ "%TEMP%"

It makes its own folder rh-write-timing-<number> in each of the two folders, writes made-up files there the way
the app writes (a temp file, then a rename), once as before and once forced onto the disk (flush + fsync), and
removes its folder again. It reads and writes nothing else: not your reels folder, your vault or config.json.
Not a test: run_tests.py does not start it. Close nothing; it takes well under a minute.

What it prints:
  one write        milliseconds for one save of a note (8 KB) on each drive, as before and forced
  one pass step    what a pass over all notes does per video: read the vault note, write it, read hub.json,
                   write it (the note on the vault's drive, hub.json on the reels drive)
  a pass over N    the same times N (default 8000), and how much slower the forced pass is
A real pass also looks notes up and moves some, so its share of writing is smaller: the percentage printed here
is the upper end."""
import os, shutil, sys, tempfile, time
from pathlib import Path

N = 200                                   # files per round
ROUNDS = 3                                # the best round counts (the first one warms up the disk cache)
NOTE = ("2026-11-01\n**Title:** A made-up title\n**Tags**: #YouTube #Training #creator/kit-channel\n\n"
        + "A made-up line of a transcript, about eighty characters long, repeated to fill a note.\n" * 90)
HUB = '{\n "category": "Training",\n "favorites": [],\n "note_path": "YouTube/Training/\'A made-up title.md",\n' \
      ' "tags": ["training/kettlebell"],\n "tag_tokens": ["#training/kettlebell"],\n "cat_tag": "#Training"\n}'


def write(path, text, forced):
    """As the app's atomic_write: a temp file next to the target, then a rename over it."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.1.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        if forced:
            fh.flush()
            os.fsync(fh.fileno())
    for i in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:           # an antivirus or indexer has it open for a moment
            time.sleep(0.05)
    raise PermissionError(f"could not replace {path}")


def one_write(folder, forced):
    """ms per save of one note, the best of ROUNDS."""
    best = None
    for _ in range(ROUNDS):
        t0 = time.perf_counter()
        for k in range(N):
            write(folder / f"note_{k:04d}.md", NOTE + str(k), forced)
        ms = (time.perf_counter() - t0) * 1000 / N
        best = ms if best is None else min(best, ms)
    return best


def pass_step(reels, vault, forced):
    """ms per video of a pass: read the note, write it, read hub.json, write it. The best of ROUNDS."""
    best = None
    for r in range(ROUNDS):
        t0 = time.perf_counter()
        for k in range(N):
            note, hub = vault / f"note_{k:04d}.md", reels / f"hub_{k:04d}.json"
            write(note, note.read_text(encoding="utf-8").rstrip("0123456789") + str(r), forced)
            write(hub, hub.read_text(encoding="utf-8").rstrip() + "\n" * (r + 1), forced)
        ms = (time.perf_counter() - t0) * 1000 / N
        best = ms if best is None else min(best, ms)
    return best


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        print("No folders given: both parts run in the temp folder", tempfile.gettempdir(), "\n")
        args = [tempfile.gettempdir(), tempfile.gettempdir()] + args[2:]
    count = int(args[2]) if len(args) > 2 else 8000
    bases = [Path(a.strip('"')).expanduser() for a in args[:2]]
    for b in bases:
        if not b.is_dir():
            sys.exit(f"not a folder: {b}")
    made = []
    try:
        dirs = []
        for b in bases:
            d = Path(tempfile.mkdtemp(prefix="rh-write-timing-", dir=str(b)))
            made.append(d)
            dirs.append(d)
        reels, vault = dirs
        print(f"reels drive: {reels}\nvault drive: {vault}\n{N} files per round, the best of {ROUNDS} rounds\n")
        res = {}
        for name, d in (("reels drive", reels), ("vault drive", vault)):
            res[name] = (one_write(d, False), one_write(d, True))
            a, b = res[name]
            print(f"one write, {name}:  as before {a:6.2f} ms   forced {b:6.2f} ms   (+{b - a:.2f} ms)")
        for k in range(N):
            (reels / f"hub_{k:04d}.json").write_text(HUB, encoding="utf-8")
        a, b = pass_step(reels, vault, False), pass_step(reels, vault, True)
        pct = (b / a - 1) * 100 if a > 0 else 0
        print(f"one pass step (note + hub.json):  as before {a:6.2f} ms   forced {b:6.2f} ms")
        print(f"a pass over {count} notes: as before about {a * count / 1000:.0f} s, forced about {b * count / 1000:.0f} s"
              f"  ->  {pct:+.0f} % (upper end: a real pass also looks notes up)")
        print("\nRESULT " + " | ".join([f"reels {res['reels drive'][0]:.2f}/{res['reels drive'][1]:.2f}",
                                        f"vault {res['vault drive'][0]:.2f}/{res['vault drive'][1]:.2f}",
                                        f"step {a:.2f}/{b:.2f}", f"{pct:+.0f}%"]))
    finally:
        for d in made:
            shutil.rmtree(d, ignore_errors=True)
        left = [str(d) for d in made if d.exists()]
        if left:
            print("could not remove (delete by hand):", ", ".join(left))


if __name__ == "__main__":
    main()
