// Who is calling. Cloudflare Access sits in front of the whole site: a person in a browser signs in with
// an email code, each laptop's Reels Hub sends its own service token. Either way Access adds its signed
// token (Cf-Access-Jwt-Assertion) to the request; it is verified here against the team's public keys,
// the audience and expiry, so the API also refuses anything that did not come through Access.
// A service token is named by its Client ID (the token's common_name), a person by their email. PEOPLE can
// also say who may not delete videos. There is no local bypass: the tests put a stand-in for Access in front
// that signs real tokens.
//
// Settings (secrets: npx wrangler pages secret put NAME --project-name <project>):
//   ACCESS_TEAM_DOMAIN  e.g. yourteam.cloudflareaccess.com
//   ACCESS_AUD          the Application Audience (AUD) tag of the Access application
//   PEOPLE              JSON, one entry per login email and one per laptop service token (its Client ID):
//                       {"you@example.com": {"person": "anna", "name": "Anna"},
//                        "0123abcd.access":  {"person": "anna", "name": "Anna"},
//                        "him@example.com": {"person": "ben", "name": "Ben", "delete": false},
//                        "4567cdef.access":  {"person": "ben", "name": "Ben", "delete": false}}
//                       "delete": false = that person's Reels Hub hides and refuses "Delete reel".

let jwksCache = { at: 0, keys: null, base: '' };

function b64urlToBytes(s) {
  s = s.replace(/-/g, '+').replace(/_/g, '/');
  while (s.length % 4) s += '=';
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

// https://<team>.cloudflareaccess.com from the setting. http:// only for 127.0.0.1 / localhost: the
// stand-in for Access used by the workspace tests (in Cloudflare such an address can never answer).
function teamBase(team) {
  const t = String(team || '').trim().replace(/\/+$/, '');
  if (/^https:\/\/[a-z0-9.-]+$/i.test(t)) return t;
  if (/^http:\/\/(127\.0\.0\.1|localhost)(:\d{2,5})?$/i.test(t)) return t;
  if (/^[a-z0-9.-]+$/i.test(t)) return `https://${t}`;
  throw new HttpError(500, 'ACCESS_TEAM_DOMAIN is not a host name');
}

async function getKeys(base, force = false) {
  const now = Date.now();
  if (!force && jwksCache.keys && jwksCache.base === base && now - jwksCache.at < 3600_000) return jwksCache.keys;
  const res = await fetch(`${base}/cdn-cgi/access/certs`);
  if (!res.ok) throw new Error('Could not fetch Access certificates');
  const { keys } = await res.json();
  jwksCache = { at: now, keys, base };
  return keys;
}

async function verifyAccessJwt(token, env) {
  const base = teamBase(env.ACCESS_TEAM_DOMAIN);
  const [h, p, s, extra] = token.split('.');
  if (!h || !p || !s || extra !== undefined) throw new Error('Malformed token');
  const header = JSON.parse(new TextDecoder().decode(b64urlToBytes(h)));
  const payload = JSON.parse(new TextDecoder().decode(b64urlToBytes(p)));
  if (header.alg !== 'RS256') throw new Error('Unexpected algorithm');
  let jwk = (await getKeys(base)).find((k) => k.kid === header.kid);
  if (!jwk) jwk = (await getKeys(base, true)).find((k) => k.kid === header.kid); // keys rotated
  if (!jwk) throw new Error('Unknown signing key');
  const key = await crypto.subtle.importKey('jwk', jwk, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify']);
  const ok = await crypto.subtle.verify('RSASSA-PKCS1-v1_5', key, b64urlToBytes(s), new TextEncoder().encode(`${h}.${p}`));
  if (!ok) throw new Error('Bad signature');
  const aud = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  if (!aud.includes(env.ACCESS_AUD)) throw new Error('Wrong audience');
  if (payload.iss !== base) throw new Error('Wrong issuer');
  if (!payload.exp || payload.exp * 1000 < Date.now()) throw new Error('Expired');
  if (payload.nbf && payload.nbf * 1000 > Date.now() + 60_000) throw new Error('Not yet valid');
  // a person signed in with an email code, or a laptop with its service token (common_name = its Client ID)
  return String(payload.email || payload.common_name || '').toLowerCase();
}

function readPeople(env) {
  let people;
  try {
    people = JSON.parse(env.PEOPLE || '{}');
  } catch {
    throw new HttpError(500, 'PEOPLE setting is not valid JSON');
  }
  if (!people || typeof people !== 'object' || Array.isArray(people)) throw new HttpError(500, 'PEOPLE must be a JSON object');
  return Object.fromEntries(
    Object.entries(people)
      .filter(([, v]) => v && typeof v === 'object' && typeof v.person === 'string' && v.person)
      .map(([k, v]) => [k.trim().toLowerCase(), v]),
  );
}

// Everyone the inbox knows, once each: [{person, name}]. These are the people you can share with.
export function peopleList(env) {
  const seen = new Map();
  for (const v of Object.values(readPeople(env))) if (!seen.has(v.person)) seen.set(v.person, { person: v.person, name: v.name || v.person });
  return [...seen.values()];
}

export async function identify(request, env) {
  const people = readPeople(env);
  if (!env.ACCESS_TEAM_DOMAIN || !env.ACCESS_AUD) throw new HttpError(500, 'Not configured: ACCESS_TEAM_DOMAIN and ACCESS_AUD are required');
  const token = request.headers.get('Cf-Access-Jwt-Assertion');
  if (!token) throw new HttpError(401, 'Not signed in through Cloudflare Access');
  let key;
  try {
    key = await verifyAccessJwt(token, env);
  } catch (e) {
    console.error('Access token rejected:', e.message); // the reason stays in the log, the client gets a fixed text
    throw new HttpError(401, 'Not signed in through Cloudflare Access');
  }
  const who = key && people[key];
  if (!who) throw new HttpError(403, `${key || 'This sign-in'} is not in PEOPLE`);
  // a person may not delete videos if any of their entries (email or laptop) says "delete": false
  const canDelete = !Object.values(people).some((v) => v.person === who.person && v.delete === false);
  return { key, person: who.person, name: who.name || who.person, can_delete: canDelete };
}

export class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}
