-- Reels Hub online library (Cloudflare D1, SQLite): one database per person, bound as LIB_<PERSON>.
-- Applied with: npm run db:remote (scripts/migrate-all.mjs applies every database in wrangler.toml).
-- Written only by that person's laptop (lib/library.js); read by their phone. No videos: they play from the link.

CREATE TABLE items (
  id      TEXT PRIMARY KEY,               -- reel folder name, or t/<channel>/<folder> for a transcript-only video
  hash    TEXT NOT NULL DEFAULT '',       -- sha1 of body, set by the laptop (it skips what is unchanged)
  size    INTEGER NOT NULL DEFAULT 0,     -- characters of body
  body    TEXT NOT NULL,                  -- JSON text made by the laptop: title, link, transcript, favorites, notes, ...
  updated TEXT NOT NULL                   -- UTC ISO 8601
);

-- favorite images, web-sized by the laptop; base64 JPEG text, stored and returned as sent
CREATE TABLE images (
  item TEXT NOT NULL,
  n    INTEGER NOT NULL,
  data TEXT NOT NULL,
  PRIMARY KEY (item, n)
);

-- the list on the phone: the entries of all videos in a few parts (JSON arrays), so one change rewrites one part
CREATE TABLE chunks (
  n    INTEGER PRIMARY KEY,
  data TEXT NOT NULL
);

-- 'meta': categories, tags, counts and when the laptop last changed something (JSON)
CREATE TABLE meta (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);

-- links pasted on the phone, until the laptop picks them up and puts them in its Run line
CREATE TABLE links (
  id      TEXT PRIMARY KEY,
  url     TEXT NOT NULL,
  mode    TEXT NOT NULL DEFAULT 'video',   -- video | transcripts
  created TEXT NOT NULL
);
