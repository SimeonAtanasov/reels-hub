# Get the links from an Instagram chat

Instagram has no "copy all links" for a chat. This script lists every reel and post link in one Instagram chat, oldest first, ready to paste into Reels Hub. It is made for the chat with yourself, where you send reels to keep them, but works on any chat you can open.

It runs in your own browser, on the page you have open, and sends nothing anywhere.

## Use it

1. In Chrome on your computer, open instagram.com and open the chat.
2. Press **F12** and open **Console**. Check that the **Filter** box above the messages is empty: text in it hides the script's messages.
3. The first time, Chrome blocks pasting: type `allow pasting` and press Enter.
4. Paste the script below and press Enter. Leave the page alone while it scrolls up to the first message. The console shows how far back it is ("760 so far, back to 2026-05-26"); a chat of about 1,200 messages takes about two minutes.
5. A box with a green border opens with the links, oldest first. Its first line counts what it found and what it left out. Press **Copy all** (or **Save as .txt**), paste the links into the Reels Hub links box and press **Run**. If some are already in Reels Hub, it asks first: press **Skip**.

## Only the new ones next time

Before pasting, change one of the three lines at the top of the script:

- `AFTER = '...'`: the last link of your previous list (for example the last line of `links-instagram-chat.txt`) gives only the links sent after it. The script then loads back only that far, so it is quick.
- `SINCE = '2026-11-10'`: only links sent on or after that day.
- `NEWEST_FIRST = true`: the newest link first.

## Keep it at hand

In DevTools open **Sources** > **Snippets** > **+ New snippet**, paste the script once and give it a name. Next time: open the chat, right-click the snippet and choose **Run** (change `AFTER` first).

## What it leaves out

- "Message unavailable" cards: the post was deleted or hidden, so there is no link to get.
- A reel sent more than once: it is listed once, at the first time it was sent.
- Links to sites other than Instagram, YouTube, TikTok and X, for example Threads posts. Instagram links that are not a reel or a post (stories, profiles) are counted under instagram.com.

## If it stops working

Instagram changes its page from time to time. If the script says "no chat found" or finds no links:

- Get the chat from Instagram instead: Accounts Center > Your information and permissions > Export your information > Create export > your account > Export to device, then only **Messages**, **All time**, format **JSON**. It is ready within minutes to 48 hours. The chat is in `messages\inbox\<name>_<number>\` in the zip: ask Claude to pull the links out of it.
- Or ask Claude to fix the script. How it works: shared reels are drawn as clickable cards, not as `<a href>` links, so a script that looks for links finds nothing. The addresses are in the data the page keeps (React and Relay): a hook whose `snapshot.data.as_ig_direct_thread.slide_messages` holds the loaded messages (newest first; `page_info.has_next_page` stays true while older ones are left), and for each message the store record `SlideMessage:<id>` > `content` > `xma` > `target_url`. The script scrolls the message list to the top until `has_next_page` turns false.

Tested on 2026-11-10 in Chrome on a chat of over 1,200 messages: it loaded the whole chat by itself and matched a list pulled by hand.

## The script

```js
// Reels Hub: get every reel and post link from an Instagram chat (made for the chat with yourself).
// Reads your own open page and sends nothing anywhere. How to use it: tools/instagram-chat-links.md
(async () => {
  const SINCE = '';           // e.g. '2026-11-10': only links sent on or after that day ('' = whole chat)
  const AFTER = '';           // e.g. the last link of your previous list: only links sent after it ('' = whole chat)
  const NEWEST_FIRST = false; // true: newest first; false: oldest first

  const wait = ms => new Promise(r => setTimeout(r, ms));
  const day = ms => new Date(+ms).toISOString().slice(0, 10);
  const IG = /(?<![\w-])instagram\.com\/(?:[\w.]+\/)?(reels?|p|tv)\/([\w-]{5,})/;
  const SITES = /^(youtube\.com|youtu\.be|tiktok\.com|x\.com|twitter\.com)$/;
  const norm = s => {                                     // a link -> {key, url, kind}, {skip: site} or null
    let u;
    try { u = new URL(s); if (u.hostname === 'l.instagram.com' && u.searchParams.get('u')) u = new URL(u.searchParams.get('u')); } catch { return null; }
    const m = u.href.match(IG);
    if (m) return m[2] === 'audio' ? null : { key: m[2], url: `https://www.instagram.com/${m[1] === 'reels' ? 'reel' : m[1]}/${m[2]}/`, kind: m[1] === 'p' ? 'posts' : 'reels' };
    const host = u.hostname.replace(/^(www|m|mobile|vm|vt)\./, '');
    if (!SITES.test(host)) return { skip: host };
    if (!/youtu/.test(host)) u.search = '';
    else for (const k of [...u.searchParams.keys()]) if (/^(si|feature|pp|igsh|igshid|fbclid|utm_.*)$/.test(k)) u.searchParams.delete(k);
    return { key: u.href, url: u.href, kind: 'other' };
  };

  // Instagram draws shared reels as clickable cards, not links: the addresses are in the data the page keeps
  const fiberOf = el => { const k = el && Object.keys(el).find(k => k.startsWith('__reactFiber$')); return k ? el[k] : null; };
  const hooks = f => { const out = []; let h = f && f.memoizedState; if (h && typeof h === 'object' && 'queue' in h && 'next' in h) for (let i = 0; h && i < 100; i++, h = h.next) out.push(h.memoizedState); return out; };
  const findEdges = (d, depth = 0) => {
    if (!d || typeof d !== 'object' || depth > 3) return null;
    if (Array.isArray(d.edges) && d.edges[0] && d.edges[0].node && /Message$/.test(d.edges[0].node.__typename || '')) return d;
    for (const k of Object.keys(d)) if (!k.startsWith('__')) { const r = findEdges(d[k], depth + 1); if (r) return r; }
    return null;
  };
  const chat = () => {                                    // the open chat's messages, newest first
    let f = fiberOf([document.querySelector('main'), ...document.body.querySelectorAll('div')].find(el => el && fiberOf(el)));
    while (f && f.return) f = f.return;
    const stack = f ? [f] : []; let best = null, n = 0;
    while (stack.length && n++ < 200000) {
      const x = stack.pop();
      for (const m of hooks(x)) {
        const sm = m && m.environment && m.snapshot && findEdges(m.snapshot.data);
        if (sm && (!best || sm.edges.length > best.edges.length)) best = { edges: sm.edges, more: !!(sm.page_info && sm.page_info.has_next_page), env: m.environment, fiber: x };
      }
      if (x.sibling) stack.push(x.sibling);
      if (x.child) stack.push(x.child);
    }
    return best;
  };
  let c = chat();
  if (!c) return console.warn('Reels Hub: no chat found. Open the chat on instagram.com in a desktop browser and run this again. If the chat is open, Instagram has changed its page: use the data export (see the .md file).');
  const under = (el, target) => { for (let f = fiberOf(el), i = 0; f && i < 150; f = f.return, i++) if (f === target || f.alternate === target) return true; return false; };
  const scrollers = [...document.querySelectorAll('div')].filter(el => /(auto|scroll)/.test(getComputedStyle(el).overflowY) && el.scrollHeight > el.clientHeight + 20 && under(el, c.fiber));
  const pane = scrollers.find(el => getComputedStyle(el).flexDirection === 'column-reverse') || scrollers[0];

  const sinceMs = SINCE ? new Date(SINCE + 'T00:00:00').getTime() : 0;
  const afterKey = AFTER ? (norm(AFTER) || {}).key : '';
  const rec = n => { const s = c.env.getStore().getSource(); return s.get(n.__id) || s.get('SlideMessage:' + n.id); };
  const ref = (r, k) => r && r[k] && r[k].__ref ? c.env.getStore().getSource().get(r[k].__ref) : null;
  const linksOf = r => {                                  // shared reel or post, plus links typed as text
    const content = ref(r, 'content'), xma = ref(content, 'xma'), out = [];
    if (xma && xma.target_url) out.push(xma.target_url);
    for (const t of [r && r.text_body, content && content.xma_text_body]) if (t) out.push(...(String(t).match(/https?:\/\/[^\s"'<>]+/g) || []));
    return { out, gone: !!(xma && /Placeholder/.test(xma.__typename || '')) };
  };
  const hasAfter = e => linksOf(rec(e.node)).out.some(s => (norm(s) || {}).key === afterKey);
  const reached = () => {                                 // loaded far enough back for SINCE / AFTER
    const last = c.edges[c.edges.length - 1];
    return !!((sinceMs && last && +last.node.timestamp_ms < sinceMs) || (afterKey && c.edges.some(hasAfter)));
  };

  // 1. Load the chat back to its first message (or to SINCE / AFTER)
  for (let still = 0, count = -1; pane && c.more && !reached() && still < 6;) {
    pane.scrollTop = -pane.scrollHeight;                  // to the top, where Instagram loads older messages
    await wait(1500);
    c = chat() || c;
    still = c.edges.length === count ? still + 1 : 0;
    count = c.edges.length;
    console.log(`Reels Hub: loading older messages... ${count} so far, back to ${day(c.edges[count - 1].node.timestamp_ms)}`);
  }
  if (pane) pane.scrollTop = pane.scrollHeight;
  if (c.more && !reached()) console.warn('Reels Hub: Instagram stopped loading older messages. The list covers what was loaded: run this again to try for the rest.');

  // 2. Read the links, oldest first, each once
  const all = [...c.edges].reverse();
  let from = 0;
  if (afterKey) {
    const i = all.map(hasAfter).lastIndexOf(true);
    if (i < 0) console.warn('Reels Hub: the AFTER link is not in this chat, so the list has everything.'); else from = i + 1;
  }
  const seen = new Set(), list = [], stat = { reels: 0, posts: 0, other: 0, repeats: 0, unavailable: 0, skipped: {} };
  for (const e of all.slice(from)) {
    if (sinceMs && +e.node.timestamp_ms < sinceMs) continue;
    const r = rec(e.node); if (!r) continue;
    const { out, gone } = linksOf(r), mine = new Set();
    if (gone) stat.unavailable++;
    for (const s of out) {
      const n = norm(s); if (!n) continue;
      if (n.skip) { stat.skipped[n.skip] = (stat.skipped[n.skip] || 0) + 1; continue; }
      if (mine.has(n.key)) continue;
      mine.add(n.key);
      if (seen.has(n.key)) { stat.repeats++; continue; }
      seen.add(n.key); stat[n.kind]++; list.push(n.url);
    }
  }
  if (NEWEST_FIRST) list.reverse();

  // 3. Show them, ready to copy into Reels Hub
  const text = list.join('\n');
  const skipped = Object.entries(stat.skipped).map(([h, n]) => `${n} ${h}`).join(', ');
  const summary = `${list.length} links (${stat.reels} reels, ${stat.posts} posts, ${stat.other} other), ${NEWEST_FIRST ? 'newest' : 'oldest'} first. Left out: ${stat.unavailable} unavailable, ${stat.repeats} repeats${skipped ? ', ' + skipped : ''}.`;
  window.chatLinks = text;
  window.chatLinksInfo = { messages: all.length - from, links: list.length, ...stat };
  if (!list.length) return console.warn('Reels Hub: no links found' + (SINCE || AFTER ? ' for that SINCE / AFTER.' : '.'));
  console.log(`Reels Hub: ${summary}\n${text}`);
  document.getElementById('rh-links')?.remove();
  const el = (tag, css, txt) => { const e = document.createElement(tag); e.style.cssText = css; if (txt) e.textContent = txt; return e; };
  const panel = el('div', 'position:fixed;top:4vh;left:50%;transform:translateX(-50%);width:min(760px,94vw);height:84vh;z-index:2147483647;background:#111;color:#eee;border:2px solid #3b8;border-radius:10px;padding:14px;display:flex;flex-direction:column;gap:10px;font:14px system-ui,sans-serif');
  panel.id = 'rh-links';
  const ta = el('textarea', 'flex:1;background:#000;color:#cfe;border:1px solid #444;padding:8px;font:12px/1.5 Consolas,monospace');
  ta.value = text;
  const row = el('div', 'display:flex;gap:8px;flex-wrap:wrap');
  const btn = (label, fn) => { const b = el('button', 'padding:8px 16px;border:0;border-radius:6px;background:#3b8;color:#000;font-weight:600;cursor:pointer', label); b.onclick = fn; row.append(b); return b; };
  const copy = btn('Copy all', async () => { ta.focus(); ta.select(); try { await navigator.clipboard.writeText(text + '\n'); } catch { document.execCommand('copy'); } copy.textContent = 'Copied'; });
  btn('Save as .txt', () => { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text + '\n'], { type: 'text/plain' })); a.download = `links-instagram-chat-${day(Date.now())}.txt`; a.click(); });
  btn('Close', () => panel.remove());
  panel.append(el('div', 'font-weight:600', summary), ta, row);
  document.body.append(panel);
})();
```
