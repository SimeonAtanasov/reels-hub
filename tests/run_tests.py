"""Run every tests/test_*.py, each in its own Python process, and print one line per file.
    py tests\\run_tests.py            (in the app folder; exit code 1 if anything failed)
    py tests\\run_tests.py r01 r41    (only the files whose name contains one of these)"""
import subprocess, sys, time
from pathlib import Path

here = Path(__file__).resolve().parent
want = [a.lower() for a in sys.argv[1:]]
files = [f for f in sorted(here.glob("test_*.py")) if not want or any(w in f.name.lower() for w in want)]
bad = 0
for f in files:
    t0 = time.time()
    r = subprocess.run([sys.executable, "-X", "utf8", str(f)], cwd=str(here), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    last = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
    print(f"{'ok  ' if r.returncode == 0 else 'FAIL'} {f.name}: {last} ({time.time() - t0:.1f} s)")
    if r.returncode != 0:
        bad += 1
        for line in r.stdout.splitlines():
            if line.startswith("  FAIL"):
                print("    " + line.strip())
        if r.stderr.strip():
            print("    " + r.stderr.strip().replace("\n", "\n    ")[-1500:])
print(f"{len(files) - bad} of {len(files)} test files passed")
sys.exit(1 if bad else 0)
