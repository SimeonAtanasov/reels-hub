-- Reels Hub inbox (Cloudflare D1, SQLite). Applied with:
--   npx wrangler d1 migrations apply DB --remote
-- One row per share. It is deleted as soon as the receiving laptop has picked it up,
-- and after 30 days if it never is (functions/api/[[path]].js, KEEP_DAYS).

CREATE TABLE shares (
  id          TEXT PRIMARY KEY,           -- random UUID
  sender      TEXT NOT NULL,              -- person who sent it, from the Access sign-in (PEOPLE), never from the request
  sender_name TEXT NOT NULL DEFAULT '',
  recipient   TEXT NOT NULL,              -- person it is for
  created     TEXT NOT NULL,              -- UTC ISO 8601
  ready       INTEGER NOT NULL DEFAULT 0, -- 1 once all its edited images are uploaded; only ready shares are listed
  images      INTEGER NOT NULL DEFAULT 0, -- how many edited images belong to it
  url         TEXT NOT NULL,              -- the video link: the receiving laptop downloads the video itself
  title       TEXT NOT NULL DEFAULT '',   -- the video's title, so the inbox can show what it is
  platform    TEXT NOT NULL DEFAULT '',
  category    TEXT NOT NULL DEFAULT '',   -- the sender's category, a suggestion
  notes       TEXT NOT NULL DEFAULT '',   -- the sender's My notes
  favorites   TEXT NOT NULL DEFAULT '[]'  -- JSON [{t, text, corrected, image}]; image = idx in share_images or null
);
CREATE INDEX shares_recipient ON shares (recipient, ready, created);

-- favorite images the sender edited (crop, arrows, text, blur); plain favorites are re-captured from the video.
-- Kept as base64 text, exactly as uploaded and handed back: no conversion in the Worker (CPU limit).
CREATE TABLE share_images (
  share_id TEXT NOT NULL,
  idx      INTEGER NOT NULL,
  data     TEXT NOT NULL,                 -- base64 JPEG, at most 1.8 million characters (about 1.35 MB)
  PRIMARY KEY (share_id, idx)
);
