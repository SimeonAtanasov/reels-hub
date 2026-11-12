// The online library (phase 3): a read-only copy of each person's Reels Hub library for the phone. Each person has
// their own D1 database, bound as LIB_<PERSON> (person from PEOPLE, upper case, other characters as _), so the API
// picks the database from the Access sign-in and nobody can read anybody else's. Videos never come here: the phone
// plays them from their original link.
//
// Only a laptop (its service token) writes the library; a person in a browser reads it and may add or remove links
// for the laptop to process ("links"). The laptop sends finished JSON text and the Worker stores and returns it as
// text, without parsing it (the free plan allows 10 ms of CPU per request); images likewise stay base64 text.
//
//   GET    /api/lib/status              {library, person, name} (cheap: no counting)
//   GET    /api/lib/index               {"meta": {...}, "chunks": [[entries], ...]}: everything the list needs
//   GET    /api/lib/item?id=            one video's full text (transcript, favorites, notes, ...)
//   GET    /api/lib/image?id=&n=        favorite image n of a video (base64 JPEG text)
//   GET    /api/lib/search?q=           ids of videos whose text contains q (transcripts included)
//   GET    /api/lib/links               links waiting for the laptop
//   POST   /api/lib/links               {url, mode: video|transcripts} a link pasted on the phone
//   DELETE /api/lib/links?id=           remove one (the phone), or picked up (the laptop)
// laptop only:
//   GET    /api/lib/ids                 [[id, hash], ...] of every stored video (to compare after a restart)
//   PUT    /api/lib/item?id=&hash=&images=k   body: the video's JSON text; images k and up are removed
//   DELETE /api/lib/item?id=            a video removed on the laptop
//   PUT    /api/lib/image?id=&n=        body: a JSON string with the base64 JPEG
//   PUT    /api/lib/index?n=            body: JSON array, part n of the list entries
//   PUT    /api/lib/meta                body: JSON object (categories, tags, counts, when)
//   DELETE /api/lib/all                 empty the online copy (Settings, "Remove the online copy")
import { HttpError } from './auth.js';

const MAX_ITEM = 1_900_000; // characters of one video's JSON (a D1 row holds 2 MB)
const MAX_CHUNK = 1_900_000;
const MAX_META = 500_000;
const MAX_IMAGE = 700_000; // base64 characters of one web-sized favorite image, quotes included
const CHUNKS = 64;
const MAX_LINKS = 200;
const MAX_URL = 2000;

export function libBinding(person) {
  return 'LIB_' + String(person || '').toUpperCase().replace(/[^A-Z0-9]/g, '_');
}

function libDb(env, user) {
  const db = env[libBinding(user.person)];
  if (!db) throw new HttpError(404, `No online library for ${user.name} yet: the database ${libBinding(user.person)} is missing (DEPLOY.md, online library)`);
  return db;
}

const text = (body, type = 'application/json; charset=utf-8') =>
  new Response(body, { headers: { 'content-type': type, 'cache-control': 'no-store' } });

function laptopOnly(user) {
  if (!user.token) throw new HttpError(403, 'Only Reels Hub on a laptop changes the online library');
}

function needId(url) {
  const id = url.searchParams.get('id') || '';
  if (!id || id.length > 300 || /[\x00-\x1f\x7f]/.test(id)) throw new HttpError(400, 'Bad id');
  return id;
}

function needInt(url, name, max) {
  const v = url.searchParams.get(name) || '';
  if (!/^\d{1,4}$/.test(v) || Number(v) > max) throw new HttpError(400, `Bad ${name}`);
  return Number(v);
}

async function rawBody(request, max, first, last) {
  if (Number(request.headers.get('content-length') || 0) > max) throw new HttpError(413, 'Too large');
  const t = await request.text();
  if (t.length > max) throw new HttpError(413, 'Too large');
  if (t[0] !== first || t[t.length - 1] !== last) throw new HttpError(400, 'Bad body');
  return t;
}

// a YouTube channel or playlist, or a TikTok profile: many videos, so only "Transcripts" takes it from the phone
// (the laptop's Run asks which videos first). The same test as tr_listing in reels_hub.py.
export function isListing(u) {
  u = String(u || '').split('#')[0];
  if (/(?:youtube\.com\/(?:watch\?(?:[^#]*&)?v=|shorts\/|live\/|embed\/|v\/)|youtu\.be\/)[A-Za-z0-9_-]{11}/i.test(u)) return false;
  if (/youtube\.com\/(@[^/?]+|channel\/|c\/|user\/|playlist\?)/i.test(u)) return true;
  return /tiktok\.com\/@[\w.-]+\/?(?:$|[?])/i.test(u);
}

export async function libraryRoute(parts, request, env, user, url, readJson, json) {
  const method = request.method;
  const what = parts[1] || '';
  if (parts.length > 2) throw new HttpError(404, 'No such route');

  if (what === 'status' && method === 'GET') {
    return json({ library: !!env[libBinding(user.person)], person: user.person, name: user.name, laptop: !!user.token });
  }
  const db = libDb(env, user);

  if (what === 'index' && method === 'GET') {
    const meta = await db.prepare("SELECT v FROM meta WHERE k = 'meta'").first();
    const { results } = await db.prepare('SELECT data FROM chunks ORDER BY n').all();
    return text(`{"meta":${meta ? meta.v : 'null'},"chunks":[${results.map((r) => r.data).join(',')}]}`);
  }
  if (what === 'item' && method === 'GET') {
    const row = await db.prepare('SELECT body FROM items WHERE id = ?').bind(needId(url)).first();
    if (!row) throw new HttpError(404, 'Not in the online library (yet)');
    return text(row.body);
  }
  if (what === 'image' && method === 'GET') {
    const row = await db.prepare('SELECT data FROM images WHERE item = ? AND n = ?').bind(needId(url), needInt(url, 'n', 199)).first();
    if (!row) throw new HttpError(404, 'No such image');
    return text(row.data, 'text/plain; charset=us-ascii');
  }
  if (what === 'search' && method === 'GET') {
    const q = (url.searchParams.get('q') || '').trim();
    if (q.length < 2 || q.length > 100) throw new HttpError(400, 'Search for 2 to 100 characters');
    const like = '%' + q.replace(/[\\%_]/g, (c) => '\\' + c) + '%';
    const row = await db.prepare("SELECT json_group_array(id) AS j FROM (SELECT id FROM items WHERE body LIKE ? ESCAPE '\\' LIMIT 1000)").bind(like).first();
    return text(`{"ids":${row && row.j ? row.j : '[]'}}`);
  }

  if (what === 'links') {
    if (method === 'GET') {
      const { results } = await db.prepare('SELECT id, url, mode, created FROM links ORDER BY created').all();
      return json({ links: results });
    }
    if (method === 'POST') {
      const body = await readJson(request);
      const link = String(body.url || '').trim();
      const mode = body.mode === 'transcripts' ? 'transcripts' : 'video';
      if (link.length > MAX_URL || !/^https?:\/\/[^\s]+$/i.test(link)) throw new HttpError(400, 'Paste a link that starts with https://');
      if (mode === 'video' && isListing(link)) {
        throw new HttpError(400, 'A channel, playlist or profile has many videos: choose "Transcripts only", or Run it on the laptop to pick the videos');
      }
      const n = await db.prepare('SELECT COUNT(*) AS n FROM links').first();
      if (n && n.n >= MAX_LINKS) throw new HttpError(429, 'Too many links waiting: start Reels Hub on the laptop');
      const dup = await db.prepare('SELECT id FROM links WHERE url = ? AND mode = ?').bind(link, mode).first();
      if (dup) return json({ ok: true, id: dup.id, already: true });
      const id = crypto.randomUUID();
      await db.prepare('INSERT INTO links (id, url, mode, created) VALUES (?,?,?,?)').bind(id, link, mode, new Date().toISOString()).run();
      return json({ ok: true, id }, 201);
    }
    if (method === 'DELETE') {
      await db.prepare('DELETE FROM links WHERE id = ?').bind(needId(url)).run();
      return json({ ok: true });
    }
  }

  // ---- from here on: the laptop only
  laptopOnly(user);
  if (what === 'ids' && method === 'GET') {
    const row = await db.prepare('SELECT json_group_array(json_array(id, hash)) AS j FROM items').first();
    return text(`{"ids":${row && row.j ? row.j : '[]'}}`);
  }
  if (what === 'item' && method === 'PUT') {
    const id = needId(url);
    const hash = url.searchParams.get('hash') || '';
    if (!/^[0-9a-f]{40}$/.test(hash)) throw new HttpError(400, 'Bad hash');
    const images = needInt(url, 'images', 200);
    const body = await rawBody(request, MAX_ITEM, '{', '}');
    await db.batch([
      db.prepare('INSERT INTO items (id, hash, size, body, updated) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET hash = excluded.hash, size = excluded.size, body = excluded.body, updated = excluded.updated')
        .bind(id, hash, body.length, body, new Date().toISOString()),
      db.prepare('DELETE FROM images WHERE item = ? AND n >= ?').bind(id, images),
    ]);
    return json({ ok: true });
  }
  if (what === 'item' && method === 'DELETE') {
    const id = needId(url);
    await db.batch([db.prepare('DELETE FROM images WHERE item = ?').bind(id), db.prepare('DELETE FROM items WHERE id = ?').bind(id)]);
    return json({ ok: true });
  }
  if (what === 'image' && method === 'PUT') {
    const id = needId(url), n = needInt(url, 'n', 199);
    const t = await rawBody(request, MAX_IMAGE, '"', '"');
    const data = t.slice(1, -1);
    // checked without decoding (CPU): base64 of a JPEG starts with /9j/
    if (!data.startsWith('/9j/') || data.length % 4 !== 0 || !/^[A-Za-z0-9+/]+=*$/.test(data)) throw new HttpError(400, 'Images must be JPEG, base64');
    await db.prepare('INSERT OR REPLACE INTO images (item, n, data) VALUES (?,?,?)').bind(id, n, data).run();
    return json({ ok: true });
  }
  if (what === 'index' && method === 'PUT') {
    const n = needInt(url, 'n', CHUNKS - 1);
    const data = await rawBody(request, MAX_CHUNK, '[', ']');
    await db.prepare('INSERT OR REPLACE INTO chunks (n, data) VALUES (?,?)').bind(n, data).run();
    return json({ ok: true });
  }
  if (what === 'meta' && method === 'PUT') {
    const v = await rawBody(request, MAX_META, '{', '}');
    await db.prepare("INSERT OR REPLACE INTO meta (k, v) VALUES ('meta', ?)").bind(v).run();
    return json({ ok: true });
  }
  if (what === 'all' && method === 'DELETE') {
    await db.batch(['images', 'items', 'chunks', 'meta'].map((t) => db.prepare(`DELETE FROM ${t}`)));
    return json({ ok: true });
  }
  throw new HttpError(404, 'No such route');
}
