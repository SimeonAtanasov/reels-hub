// The Reels Hub inbox: one Pages Function, routes below. Storage is the D1 binding DB (migrations/).
// A share carries only what the other laptop needs to rebuild it: the link, who sent it (taken from the
// Access sign-in, never from the request), the video's title for the inbox, the category suggestion,
// the favorites (time, text, Corrected mark; the image only where the sender edited it) and My notes.
// The video never comes here: the receiving laptop downloads it itself. A share is deleted as soon as
// the receiving laptop has picked it up, and after KEEP_DAYS if it never is.
//
// Edited images go up one per request and are kept as the base64 text that was sent: the Worker never
// decodes or converts them (the free plan allows 10 ms of CPU per request). A share with images stays
// hidden from the recipient until the sender has uploaded them all and marked it ready.
//
//   GET    /api/whoami                  who is signed in, whom they can share with, may they delete videos
//   POST   /api/shares                  new share {to, url, title, platform, category, notes, favorites:
//                                        [{t, text, corrected, image: true|false}]} -> {id, images}
//   PUT    /api/shares/<id>/image/<n>   the sender uploads edited image n {data: base64 JPEG}
//   POST   /api/shares/<id>/ready       the sender: all images are up, the recipient may see it
//   DELETE /api/shares/<id>             the sender drops a share that never became ready (failed upload)
//   GET    /api/inbox                   ready shares waiting for me (?count=1: only how many)
//   GET    /api/inbox/<id>/image/<n>    an edited image of a share for me (base64 text)
//   DELETE /api/inbox/<id>              picked up: delete a share for me
import { identify, peopleList, HttpError } from '../../lib/auth.js';

const MAX_BODY = 3 * 1024 * 1024; // a share without its images (notes and favorite texts)
const MAX_IMAGE_B64 = 1_800_000; // characters of base64 per edited image (about 1.35 MB); a D1 row holds 2 MB
const MAX_FAVS = 200;
const MAX_IMAGES = 40;
const MAX_PENDING = 300; // shares waiting for one person
const KEEP_DAYS = 30;
const UNREADY_HOURS = 24; // a share whose images never all arrived
const MAX_LEN = { to: 40, url: 2000, title: 300, platform: 40, category: 100, notes: 100000, text: 2000 };
const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const iso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');
const now = () => iso(Date.now());
const json = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
  });

function needText(v, f) {
  const s = v === null || v === undefined ? '' : typeof v === 'string' ? v : String(v);
  if (s.length > MAX_LEN[f]) throw new HttpError(400, `${f} is too long (at most ${MAX_LEN[f]} characters)`);
  return s;
}

async function readJson(request) {
  if (Number(request.headers.get('content-length') || 0) > MAX_BODY) throw new HttpError(413, 'Request too large');
  const text = await request.text();
  if (text.length > MAX_BODY) throw new HttpError(413, 'Request too large');
  let body;
  try {
    body = JSON.parse(text || '{}');
  } catch {
    throw new HttpError(400, 'Body is not JSON');
  }
  if (!body || typeof body !== 'object' || Array.isArray(body)) throw new HttpError(400, 'Body must be an object');
  return body;
}

function cleanFavorites(list) {
  if (list === undefined || list === null) return { favs: [], images: 0 };
  if (!Array.isArray(list)) throw new HttpError(400, 'favorites must be a list');
  if (list.length > MAX_FAVS) throw new HttpError(400, `At most ${MAX_FAVS} favorites per share`);
  const favs = [];
  let images = 0;
  for (const f of list) {
    if (!f || typeof f !== 'object') throw new HttpError(400, 'Bad favorite');
    const t = Number(f.t);
    if (!Number.isFinite(t) || t < 0 || t > 86400) throw new HttpError(400, 'Bad favorite time');
    let image = null;
    if (f.image) {
      if (images >= MAX_IMAGES) throw new HttpError(400, `At most ${MAX_IMAGES} edited images per share`);
      image = images++;
    }
    favs.push({ t: Math.round(t * 1000) / 1000, text: needText(f.text, 'text'), corrected: !!f.corrected, image });
  }
  return { favs, images };
}

async function cleanup(DB) {
  const old = iso(Date.now() - KEEP_DAYS * 86400_000), stale = iso(Date.now() - UNREADY_HOURS * 3600_000);
  const gone = 'SELECT id FROM shares WHERE created < ? OR (ready = 0 AND created < ?)';
  await DB.batch([
    DB.prepare(`DELETE FROM share_images WHERE share_id IN (${gone})`).bind(old, stale),
    DB.prepare('DELETE FROM shares WHERE created < ? OR (ready = 0 AND created < ?)').bind(old, stale),
  ]);
}

async function ownShare(DB, id, user) {
  // a share this person sent that is not ready yet (the only kind its sender may still change)
  if (!ID.test(id)) throw new HttpError(404, 'No such share');
  const s = await DB.prepare('SELECT images, ready FROM shares WHERE id = ? AND sender = ?').bind(id, user.person).first();
  if (!s) throw new HttpError(404, 'No such share');
  if (s.ready) throw new HttpError(409, 'That share is already sent');
  return s;
}

async function route(request, env) {
  const url = new URL(request.url);
  const parts = url.pathname.replace(/^\/api\/?/, '').split('/').filter(Boolean);
  const method = request.method;
  const user = await identify(request, env);
  const DB = env.DB;
  if (method !== 'GET') {
    // cross-site requests (a form or text/plain POST from another site riding on the Access cookie) are refused
    const site = request.headers.get('Sec-Fetch-Site');
    if (site && site !== 'same-origin') throw new HttpError(403, 'Cross-site request refused');
    if (method !== 'DELETE' && !(request.headers.get('content-type') || '').startsWith('application/json')) {
      throw new HttpError(415, 'JSON only');
    }
  }

  if (parts[0] === 'whoami' && parts.length === 1 && method === 'GET') {
    return json({ person: user.person, name: user.name, can_delete: user.can_delete, people: peopleList(env) });
  }

  if (parts[0] === 'shares') {
    if (parts.length === 1 && method === 'POST') {
      const body = await readJson(request);
      const to = needText(body.to, 'to');
      const target = peopleList(env).find((p) => p.person === to);
      if (!target) throw new HttpError(400, 'Unknown person');
      if (to === user.person) throw new HttpError(400, 'That is you: share with someone else');
      const link = needText(body.url, 'url').trim();
      if (!/^https?:\/\/[^\s]+$/i.test(link)) throw new HttpError(400, 'The link must start with http:// or https://');
      const { favs, images } = cleanFavorites(body.favorites);
      await cleanup(DB);
      const waiting = await DB.prepare('SELECT COUNT(*) AS n FROM shares WHERE recipient = ?').bind(to).first();
      if (waiting && waiting.n >= MAX_PENDING) throw new HttpError(429, `${target.name}'s inbox is full: ask them to start Reels Hub`);
      const id = crypto.randomUUID();
      await DB.prepare(
        'INSERT INTO shares (id, sender, sender_name, recipient, created, ready, images, url, title, platform, category, notes, favorites) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
      ).bind(id, user.person, user.name, to, now(), images ? 0 : 1, images, link, needText(body.title, 'title'),
        needText(body.platform, 'platform'), needText(body.category, 'category'), needText(body.notes, 'notes'), JSON.stringify(favs)).run();
      return json({ ok: true, id, images, to: target.person, name: target.name }, 201);
    }
    const id = parts[1] || '';
    if (parts.length === 4 && parts[2] === 'image' && /^\d{1,2}$/.test(parts[3]) && method === 'PUT') {
      const s = await ownShare(DB, id, user);
      const n = Number(parts[3]);
      if (n >= s.images) throw new HttpError(400, 'No such image in this share');
      const body = await readJson(request);
      const data = body.data;
      // checked without decoding (CPU): a JPEG in base64 starts with /9j/; the laptop that gets it checks the rest
      if (typeof data !== 'string' || data.length > MAX_IMAGE_B64 || data.length % 4 !== 0 || !data.startsWith('/9j/')) {
        throw new HttpError(400, 'Edited images must be JPEG, base64, at most about 1.35 MB');
      }
      await DB.prepare('INSERT OR REPLACE INTO share_images (share_id, idx, data) VALUES (?,?,?)').bind(id, n, data).run();
      return json({ ok: true });
    }
    if (parts.length === 3 && parts[2] === 'ready' && method === 'POST') {
      const s = await ownShare(DB, id, user);
      const got = await DB.prepare('SELECT COUNT(*) AS n FROM share_images WHERE share_id = ?').bind(id).first();
      if (!got || got.n !== s.images) throw new HttpError(400, `${got ? got.n : 0} of ${s.images} edited images arrived`);
      await DB.prepare('UPDATE shares SET ready = 1 WHERE id = ? AND sender = ?').bind(id, user.person).run();
      return json({ ok: true });
    }
    if (parts.length === 2 && method === 'DELETE') {
      await ownShare(DB, id, user);
      await DB.batch([
        DB.prepare('DELETE FROM share_images WHERE share_id = ?').bind(id),
        DB.prepare('DELETE FROM shares WHERE id = ? AND sender = ? AND ready = 0').bind(id, user.person),
      ]);
      return json({ ok: true });
    }
  }

  if (parts[0] === 'inbox') {
    if (parts.length === 1 && method === 'GET') {
      await cleanup(DB);
      if (url.searchParams.get('count')) {
        const r = await DB.prepare('SELECT COUNT(*) AS n FROM shares WHERE recipient = ? AND ready = 1').bind(user.person).first();
        return json({ count: r ? r.n : 0 });
      }
      const { results } = await DB.prepare(
        'SELECT id, sender, sender_name, created, url, title, platform, category, notes, favorites FROM shares WHERE recipient = ? AND ready = 1 ORDER BY created',
      ).bind(user.person).all();
      return json({
        shares: results.map((r) => ({
          id: r.id, from: r.sender, from_name: r.sender_name, created: r.created, url: r.url, title: r.title,
          platform: r.platform, category: r.category, notes: r.notes, favorites: JSON.parse(r.favorites || '[]'),
        })),
      });
    }
    const id = parts[1] || '';
    if (!ID.test(id)) throw new HttpError(404, 'No such share');
    if (parts.length === 4 && parts[2] === 'image' && /^\d{1,2}$/.test(parts[3]) && method === 'GET') {
      const row = await DB.prepare(
        'SELECT i.data AS data FROM share_images i JOIN shares s ON s.id = i.share_id WHERE i.share_id = ? AND i.idx = ? AND s.recipient = ? AND s.ready = 1',
      ).bind(id, Number(parts[3]), user.person).first();
      if (!row) throw new HttpError(404, 'No such image');
      return new Response(row.data, { headers: { 'content-type': 'text/plain; charset=us-ascii', 'cache-control': 'no-store' } });
    }
    if (parts.length === 2 && method === 'DELETE') {
      const mine = await DB.prepare('SELECT 1 AS ok FROM shares WHERE id = ? AND recipient = ? AND ready = 1').bind(id, user.person).first();
      if (!mine) throw new HttpError(404, 'No such share');
      await DB.batch([
        DB.prepare('DELETE FROM share_images WHERE share_id = ?').bind(id),
        DB.prepare('DELETE FROM shares WHERE id = ? AND recipient = ?').bind(id, user.person),
      ]);
      return json({ ok: true });
    }
  }

  throw new HttpError(404, 'No such route');
}

export async function onRequest({ request, env }) {
  try {
    return await route(request, env);
  } catch (e) {
    if (e instanceof HttpError) return json({ error: e.message }, e.status);
    console.error(e);
    return json({ error: 'Server error' }, 500);
  }
}
