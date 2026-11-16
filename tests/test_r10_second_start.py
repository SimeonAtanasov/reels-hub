"""Audit R-10: a second start of Reels Hub (a double-click on the shortcut while it runs) was meant to be refused
because the port is busy. On Windows it was not: Python's http.server sets SO_REUSEADDR, which there lets a second
program bind a port that is in use (laptop check of 2026-11-15: "second bind worked"). Two servers on one reels
folder then both ran the waiting line: the same links twice, failed downloads, _queue.json written by both.
Now a second start is refused twice over: the port is bound so that Windows gives it to one program only, and the
reels folder carries _running.json (process number, port) while the app runs, so a second copy is refused whatever
its port; the new window says that Reels Hub already runs and where. A file left behind by a crash is replaced.
Runs copies of the app from a temp folder on free ports, with temp folders and no config: nothing of yours, and no
browser window is opened.
Before the fix: on Windows the second server on the same port started (check 6 below); on every system a second
copy on another port started on the same reels folder (check 4)."""
import json, os, shutil, socket, subprocess, sys, time, urllib.request
from helpers import run, APP


def body(env):
    app = env.root / "app"
    app.mkdir()
    for f in ("reels_hub.py", "reels2notes.py"):
        shutil.copyfile(APP / f, app / f)
    out2 = env.root / "reels2"
    out2.mkdir()
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    procs = []

    def free_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def start(name, port, out, appdata="appdata"):
        """Start a copy; returns (process, the file its black window goes to)."""
        e = dict(os.environ, APPDATA=str(env.root / appdata), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        log = env.root / f"{name}.log"
        fh = open(log, "wb")
        p = subprocess.Popen([sys.executable, "-u", str(app / "reels_hub.py"), "--port", str(port), "--no-browser",
                              "--out", str(out), "--vault", str(env.vault)], cwd=str(app), env=e, stdout=fh, stderr=subprocess.STDOUT)
        p.fh = fh
        procs.append(p)
        return p, log

    def ping(port):
        try:
            with op.open(f"http://127.0.0.1:{port}/api/ping", timeout=5) as r:
                return json.loads(r.read())
        except (OSError, ValueError):
            return None

    def up(p, port, secs=40):
        """Wait until this copy answers on its port (None: it ended, or never answered)."""
        t0 = time.time()
        while time.time() - t0 < secs:
            if p.poll() is not None:
                return None
            a = ping(port)
            if a:
                return a
            time.sleep(0.2)
        return None

    def ended(p, secs=40):
        """True when the copy ended by itself within secs; a copy that keeps running is stopped and counts as False."""
        try:
            p.wait(secs)
            return True
        except subprocess.TimeoutExpired:
            stop(p)
            return False

    def stop(p):
        if p.poll() is None:
            p.kill()                      # as closing the black window: no goodbye
            try:
                p.wait(15)
            except subprocess.TimeoutExpired:
                pass
        p.fh.close()

    def text(log):
        return log.read_text(encoding="utf-8", errors="replace")

    def mark(folder=None):
        try:
            return json.loads(((folder or env.out) / "_running.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    try:
        port = free_port()
        a, alog = start("a", port, env.out)
        boot = (up(a, port) or {}).get("boot")
        env.check("1. the first copy starts", bool(boot), text(alog)[-300:])
        env.check("2. while it runs the reels folder holds _running.json with its process number and port",
                  mark().get("pid") == a.pid and mark().get("port") == port, str(mark()))

        # ---------- the double-click: a second copy, same folder, same port
        b, blog = start("b", port, env.out)
        gone = ended(b)
        env.check("3. a second start on the same reels folder ends by itself and says that Reels Hub already runs, and where",
                  gone and "Reels Hub already runs on this reels folder" in text(blog) and f"http://127.0.0.1:{port}" in text(blog),
                  ("still running after 40 s: stopped by the test. " if not gone else "") + text(blog)[-300:])

        # ---------- same folder, another port
        port2 = free_port()
        c, clog = start("c", port2, env.out)
        gone = ended(c)
        env.check("4. a second copy on another port is refused too, and names the first one's address (before: both ran the same line)",
                  gone and "already runs on this reels folder" in text(clog) and f"http://127.0.0.1:{port}" in text(clog)
                  and ping(port2) is None, ("still running after 40 s: stopped by the test. " if not gone else "") + text(clog)[-300:])
        env.check("5. the first copy still runs, and the mark is still its own",
                  (ping(port) or {}).get("boot") == boot and mark().get("pid") == a.pid, str(mark()))

        # ---------- the port alone: another reels folder, the same port (the case Windows let through)
        d, dlog = start("d", port, out2, appdata="appdata2")
        gone = ended(d)
        env.check("6. a second server on the same port is refused by the system itself (before, on Windows: it started)",
                  gone and f"Port {port} is busy" in text(dlog), ("still running after 40 s: stopped by the test. " if not gone else "")
                  + text(dlog)[-300:])
        env.check("7. and it left no mark in its own reels folder", not (out2 / "_running.json").exists(), str(mark(out2)))
        env.check("8. the first copy is the one that answers on the port", (ping(port) or {}).get("boot") == boot)

        # ---------- the black window closed (the process killed): the mark stays, the next start replaces it
        stop(a)
        env.check("9. after the window is closed the mark is left behind", mark().get("pid") == a.pid, str(mark()))
        e, elog = start("e", port, env.out)
        boot2 = (up(e, port) or {}).get("boot")
        env.check("10. the next start on the same port and folder works (the left-over mark is recognised and replaced)",
                  bool(boot2) and boot2 != boot and mark().get("pid") == e.pid and "running at" in text(elog), text(elog)[-300:])
        stop(e)

        # ---------- a mark whose process number now belongs to another program (after a restart of the PC)
        (env.out / "_running.json").write_text(json.dumps({"pid": os.getpid(), "port": free_port(), "boot": "feedfacecafe",
                                                            "at": "2026-11-01 10:00:00"}), encoding="utf-8")
        f, flog = start("f", port, env.out)
        boot3 = (up(f, port) or {}).get("boot")
        env.check("11. a mark that names a running program which is not Reels Hub does not lock the folder: the app starts",
                  bool(boot3) and mark().get("pid") == f.pid, text(flog)[-300:])
        stop(f)
    finally:
        for p in procs:
            stop(p)


run("r10", body)
