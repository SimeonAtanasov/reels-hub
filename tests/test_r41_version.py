"""Audit R-41: you can see which version runs. The black window, /api/ping, Settings (/api/config) and the page
carry "Reels Hub <date> <8 hex digits>"; when the files are replaced while the app runs (a USB update), the
running server keeps its own stamp and says that the files on disk are newer, so the page can ask for a restart.
Runs a copy of the app from a temp folder on a free port, with temp folders and no config: nothing of yours."""
import json, os, re, shutil, socket, subprocess, sys, time, urllib.request
from helpers import run, APP


def body(env):
    app = env.root / "app"
    app.mkdir()
    for f in ("reels_hub.py", "reels2notes.py"):
        shutil.copyfile(APP / f, app / f)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    e = dict(os.environ, APPDATA=str(env.root / "appdata"), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    log = open(env.root / "server.log", "wb")
    proc = subprocess.Popen([sys.executable, "-u", str(app / "reels_hub.py"), "--port", str(port), "--no-browser",
                             "--out", str(env.out), "--vault", str(env.vault)], cwd=str(app), env=e, stdout=log,
                            stderr=subprocess.STDOUT)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(path):
        with op.open(f"http://127.0.0.1:{port}{path}", timeout=20) as r:
            body = r.read()
        return json.loads(body) if path.startswith("/api/") else body.decode("utf-8")
    try:
        for _ in range(100):
            try:
                ping = get("/api/ping")
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("the app did not start")
        v = ping.get("version", "")
        env.check("ping: version is '<date> <8 hex digits>'", re.fullmatch(r"\d{4}-\d\d-\d\d [0-9a-f]{8}", v or "") is not None, v)
        env.check("ping: not updated right after the start", ping.get("updated") is False, str(ping))
        env.check("the page carries the same version", f'PAGE_VERSION = "{v}"' in get("/"))
        env.check("Settings (/api/config) shows it", get("/api/config").get("version") == v)
        time.sleep(0.2)
        log.flush()
        env.check("the black window prints it", f"Reels Hub {v} running at" in (env.root / "server.log").read_text(encoding="utf-8", errors="replace"))
        with open(app / "reels2notes.py", "a", encoding="utf-8") as fh:     # a USB update while it runs
            fh.write("\n# a newer copy\n")
        ping2 = get("/api/ping")
        env.check("after the files change: the running version stays", ping2.get("version") == v, str(ping2))
        env.check("after the files change: it says the files are newer", ping2.get("updated") is True and ping2.get("files") != v.split()[1], str(ping2))
        cfg = get("/api/config")
        env.check("after the files change: Settings says so too", cfg.get("updated") is True)
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


run("r41", body)
