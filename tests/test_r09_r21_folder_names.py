"""Audit R-09 and R-21: the folder a run makes is always one plain folder inside the reels folder (or inside its
channel's folder in _transcripts), whatever id the site reports, and a video without an upload date gets
00000000 instead of a name starting with "_" (which every listing hides).
Before the fix: the id went into the path as it came ("../../x" put the folder outside the reels folder on Windows),
and a missing date gave "_<id>"."""
import ntpath
from helpers import run

ODD = ["tmp/../../escaped", "../../escaped", "..\\..\\escaped", "a\\b", "x:y", "C:\\Windows\\x", "\\\\server\\share\\x",
       "CON", "..", ".", "", "   ", "name.", "name ", ".hidden", "a" * 300, "id with spaces & signs?*<>|\"", "\u00e9t\u00e9"]
PLAIN = ["dQw4w9WgXcQ", "DZsdJ6oxAQl", "7482780205514280214", "Ab_c-1.2"]


def body(env):
    r2n = env.r2n
    out_win = "C:\\Users\\kit\\Downloads\\reels"
    for vid in ODD:
        name = r2n.folder_name({"id": vid, "upload_date": "20261101"}, "https://example.com/v/" + vid)
        tag = repr(vid)[:30]
        env.check(f"id {tag}: one plain folder name", "/" not in name and "\\" not in name and ":" not in name
                  and name not in (".", "..") and not name.endswith((".", " ")) and len(name) <= 89, name)
        joined = ntpath.normpath(ntpath.join(out_win, name))
        env.check(f"id {tag}: on Windows it stays inside the reels folder", ntpath.dirname(joined) == out_win, joined)
        env.check(f"id {tag}: inside() agrees here", r2n.inside(env.out, env.out / name))
    for vid in PLAIN:
        env.check(f"a normal id keeps its folder name: {vid}", r2n.folder_name({"id": vid, "upload_date": "20261101"}) == f"20261101_{vid}")
    for date in (None, "", "2026", "2026-11-01", "abcdefgh"):
        name = r2n.folder_name({"id": "KitNoDate01", "upload_date": date})
        env.check(f"upload date {date!r}: 00000000, not a hidden '_' folder", name == "00000000_KitNoDate01", name)
    env.check("two odd ids give two different names", r2n.folder_name({"id": ".."}, "https://example.com/a") != r2n.folder_name({"id": ".."}, "https://example.com/b"))
    env.check("inside(): a path outside is refused", not r2n.inside(env.out, env.out.parent / "escaped")
              and not r2n.inside(env.out, env.out / "a" / "b") and not r2n.inside(env.out, env.out / ".."))
    rh = env.rh                                            # the app takes the new names as reels
    for name in (r2n.folder_name({"id": "x:y", "upload_date": None}), r2n.folder_name({"id": "../../x", "upload_date": "20261101"})):
        (env.out / name).mkdir()
        try:
            rh.reel_dir(name)
            env.check(f"the app accepts {name} as a reel id", True)
        except Exception as e:
            env.check(f"the app accepts {name} as a reel id", False, str(e))


run("r09_r21", body)
