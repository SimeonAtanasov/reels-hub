# Get the links from a TikTok chat

TikTok has no "copy all links" for a chat. This script lists every video link in one TikTok chat, oldest first, ready to paste into Reels Hub. It is made for the chat with yourself, where you send videos to keep them, but works on any chat you can open.

It runs in your own browser, on the page you have open, and sends nothing anywhere.

## Use it

1. In Chrome on your computer, open tiktok.com/messages and open the chat. The chat with yourself is the one with your own name.
2. Press **F12** and open **Console**. Check that the **Filter** box above the messages is empty: text in it hides the script's messages.
3. The first time, Chrome blocks pasting: type `allow pasting` and press Enter.
4. Paste the script below and press Enter. It loads the older messages itself, 20 at a time, and the console shows how far back it is. A chat of about 400 messages takes about 20 seconds.
5. A box with a green border opens with the links, oldest first. Its first line counts what it found and what it left out. Press **Copy all** (or **Save as .txt**), paste the links into the Reels Hub links box and press **Run**. If some are already in Reels Hub, it asks first: press **Skip**.

## The links

TikTok's chat keeps the number of each video but not the creator's handle, so the links look like `https://www.tiktok.com/@_/video/7482780205514280214`. The `@_` stands for any creator: TikTok opens the right video (it goes on to the creator's own address) and yt-dlp writes links the same way when it does not know the creator. Reels Hub takes the creator's name from the video itself.

## Photo posts

Photo posts (a slideshow of pictures) are kept out of the list, because Reels Hub cannot take TikTok photo posts yet. The box has a **Copy the N photo posts** button to copy them on their own. `PHOTOS = true` at the top puts them in the main list.

## Only the new ones next time

Before pasting, change one of the lines at the top of the script:

- `AFTER = '...'`: the last link of your previous list (for example the last line of `links-tiktok-chat.txt`) gives only the links sent after it. The script then loads back only that far, so it is quick.
- `SINCE = '2026-11-10'`: only links sent on or after that day.
- `NEWEST_FIRST = true`: the newest link first.

## Keep it at hand

In DevTools open **Sources** > **Snippets** > **+ New snippet**, paste the script once and give it a name. Next time: open the chat, right-click the snippet and choose **Run** (change `AFTER` first).

## What it leaves out

- Photo posts (see above).
- A video sent more than once: it is listed once, at the first time it was sent.
- Messages without a video or a link (text, stickers), and links to sites other than TikTok, Instagram, YouTube and X. The summary says how many.

Videos deleted or made private since you sent them are still listed (the chat keeps the share); Reels Hub reports them as failed.

## If it stops working

TikTok changes its page from time to time. If the script says "no chat found" or finds no links:

- TikTok's own data download (Settings and privacy > Account > Download your data) includes your direct messages: ask Claude to pull the links out of it.
- Or ask Claude to fix the script. How it works: shared videos are drawn as clickable cards (`data-e2e="dm-new-shared-video"`) without links, so a script that looks for links finds nothing. The page's React data holds the open chat: a hook state with `loadMoreMessages()`, `hasMore` and `instance.getMessageList()` (its `stateStore.isActive` is true for the chat on screen). Each message's `content` is JSON with `itemId` (the video's number) and `aweType` (800 a video, 810 a photo post). The script calls `loadMoreMessages()` until `hasMore` turns false, the same as scrolling up.

Tested on 2026-11-10 in Chrome on a chat of about 400 messages: it loaded the whole chat by itself in about 20 seconds, and the links open the right videos.

## The script

```js
// Reels Hub: get every video link from a TikTok chat (made for the chat with yourself).
// Reads your own open page and sends nothing anywhere. How to use it: tools/tiktok-chat-links.md
(async () => {
  const SINCE = '';           // e.g. '2026-11-10': only links sent on or after that day ('' = whole chat)
  const AFTER = '';           // e.g. the last link of your previous list: only links sent after it ('' = whole chat)
  const PHOTOS = false;       // true: photo posts go in the list too (Reels Hub cannot take TikTok photo posts yet)
  const NEWEST_FIRST = false; // true: newest first; false: oldest first

  const wait = ms => new Promise(r => setTimeout(r, ms));
  const day = d => new Date(d).toISOString().slice(0, 10);
  const parse = c => { if (c && typeof c === 'object') return c; try { return JSON.parse(c); } catch { return { text: String(c || '') }; } };
  const typed = s => {                                    // a link typed as text -> {key, url, kind}, {skip: site} or null
    let u; try { u = new URL(s); } catch { return null; }
    const host = u.hostname.replace(/^(www|m|mobile|vm|vt)\./, '');
    const tt = host === 'tiktok.com' && u.pathname.match(/\/(video|photo)\/(\d+)/);
    if (tt) return { key: tt[2], url: `https://www.tiktok.com/@_/${tt[1]}/${tt[2]}`, kind: tt[1] === 'photo' ? 'photos' : 'videos' };
    const ig = u.href.match(/(?<![\w-])instagram\.com\/(?:[\w.]+\/)?(reels?|p|tv)\/([\w-]{5,})/);
    if (ig) return { key: ig[2], url: `https://www.instagram.com/${ig[1] === 'reels' ? 'reel' : ig[1]}/${ig[2]}/`, kind: 'other' };
    if (!/^(tiktok\.com|youtube\.com|youtu\.be|x\.com|twitter\.com)$/.test(host)) return { skip: host };
    if (/youtu/.test(host)) { for (const k of [...u.searchParams.keys()]) if (/^(si|feature|pp|utm_.*)$/.test(k)) u.searchParams.delete(k); } else u.search = '';
    return { key: u.href, url: u.href, kind: 'other' };
  };

  // TikTok draws shared videos as clickable cards, not links: the video ids are in the chat the page keeps
  const fiberOf = el => { const k = el && Object.keys(el).find(k => k.startsWith('__reactFiber$')); return k ? el[k] : null; };
  const hooks = f => { const out = []; let h = f && f.memoizedState; if (h && typeof h === 'object' && 'queue' in h && 'next' in h) for (let i = 0; h && i < 120; i++, h = h.next) out.push(h.memoizedState); return out; };
  const chat = () => {                                    // the open chat: loadMoreMessages, hasMore, instance.getMessageList()
    let f = fiberOf([...document.body.querySelectorAll('div')].find(el => fiberOf(el)));
    while (f && f.return) f = f.return;
    const stack = f ? [f] : [], found = new Set(); let n = 0;
    while (stack.length && n++ < 300000) {
      const x = stack.pop();
      for (const m of hooks(x)) if (m && typeof m.loadMoreMessages === 'function' && m.instance && typeof m.instance.getMessageList === 'function') found.add(m);
      if (x.sibling) stack.push(x.sibling);
      if (x.child) stack.push(x.child);
    }
    return [...found].find(m => m.stateStore && m.stateStore.isActive) || null;   // only the chat that is open
  };
  let c = chat();
  if (!c) return console.warn('Reels Hub: no chat found. Open the chat on tiktok.com/messages in a desktop browser and run this again. If the chat is open, TikTok has changed its page (see the .md file).');
  const msgs = () => [...c.instance.getMessageList()].sort((a, b) => +new Date(a.createdAt) - +new Date(b.createdAt));
  const keyOf = m => { const x = parse(m.content); return x.itemId ? String(x.itemId) : null; };
  const sinceMs = SINCE ? new Date(SINCE + 'T00:00:00').getTime() : 0;
  const afterKey = AFTER ? ((AFTER.match(/\/(?:video|photo)\/(\d+)/) || [])[1] || '') : '';
  const reached = () => {                                 // loaded far enough back for SINCE / AFTER
    const l = msgs();
    return !!((sinceMs && l.length && +new Date(l[0].createdAt) < sinceMs) || (afterKey && l.some(m => keyOf(m) === afterKey)));
  };

  // 1. Load the chat back to its first message (or to SINCE / AFTER), 20 messages at a time
  for (let still = 0, count = -1; c.hasMore && !reached() && still < 5;) {
    if (!c.isLoading) await c.loadMoreMessages();       // what scrolling up in the chat does
    await wait(700);
    c = chat() || c;
    const n = c.instance.getMessageList().length;
    still = n === count ? still + 1 : 0;
    count = n;
    console.log(`Reels Hub: loading older messages... ${n} so far, back to ${day(msgs()[0].createdAt)}`);
  }
  if (c.hasMore && !reached()) console.warn('Reels Hub: TikTok stopped loading older messages. The list covers what was loaded: run this again to try for the rest.');

  // 2. Read the links, oldest first, each once
  const all = msgs();
  let from = 0;
  if (afterKey) {
    const i = all.map(m => keyOf(m) === afterKey).lastIndexOf(true);
    if (i < 0) console.warn('Reels Hub: the AFTER link is not in this chat, so the list has everything.'); else from = i + 1;
  }
  const seen = new Set(), list = [], photos = [], stat = { videos: 0, photos: 0, other: 0, repeats: 0, nolink: 0, skipped: {} };
  for (const m of all.slice(from)) {
    if (sinceMs && +new Date(m.createdAt) < sinceMs) continue;
    const x = parse(m.content), found = [];
    if (x.itemId) {
      const photo = Number(x.aweType) === 810 || /photo/.test(m.scene || '');
      found.push({ key: String(x.itemId), url: `https://www.tiktok.com/@_/${photo ? 'photo' : 'video'}/${x.itemId}`, kind: photo ? 'photos' : 'videos' });
    } else for (const s of String(x.text || '').match(/https?:\/\/[^\s"'<>]+/g) || []) { const l = typed(s); if (l) found.push(l); }
    if (!found.length) { stat.nolink++; continue; }
    for (const l of found) {
      if (l.skip) { stat.skipped[l.skip] = (stat.skipped[l.skip] || 0) + 1; continue; }
      if (seen.has(l.key)) { stat.repeats++; continue; }
      seen.add(l.key); stat[l.kind]++;
      (l.kind === 'photos' && !PHOTOS ? photos : list).push(l.url);
    }
  }
  if (NEWEST_FIRST) { list.reverse(); photos.reverse(); }

  // 3. Show them, ready to copy into Reels Hub
  const text = list.join('\n');
  const skipped = Object.entries(stat.skipped).map(([h, n]) => `${n} ${h}`).join(', ');
  const summary = `${list.length} links (${stat.videos} videos${PHOTOS ? `, ${stat.photos} photo posts` : ''}, ${stat.other} other), ${NEWEST_FIRST ? 'newest' : 'oldest'} first, from ${all.length - from} messages. Left out: ${PHOTOS ? '' : `${photos.length} photo posts, `}${stat.repeats} repeats, ${stat.nolink} messages without a link${skipped ? ', ' + skipped : ''}.`;
  window.chatLinks = text;
  window.chatPhotoLinks = photos.join('\n');
  window.chatLinksInfo = { messages: all.length - from, links: list.length, photoPosts: photos.length, ...stat, oldest: all.length ? day(all[0].createdAt) : null };
  if (!list.length && !photos.length) return console.warn('Reels Hub: no links found' + (SINCE || AFTER ? ' for that SINCE / AFTER.' : '.'));
  console.log(`Reels Hub: ${summary}\n${text}`);
  document.getElementById('rh-links')?.remove();
  const el = (tag, css, txt) => { const e = document.createElement(tag); e.style.cssText = css; if (txt) e.textContent = txt; return e; };
  const panel = el('div', 'position:fixed;top:4vh;left:50%;transform:translateX(-50%);width:min(760px,94vw);height:84vh;z-index:2147483647;background:#111;color:#eee;border:2px solid #3b8;border-radius:10px;padding:14px;display:flex;flex-direction:column;gap:10px;font:14px system-ui,sans-serif');
  panel.id = 'rh-links';
  const ta = el('textarea', 'flex:1;background:#000;color:#cfe;border:1px solid #444;padding:8px;font:12px/1.5 Consolas,monospace');
  ta.value = text;
  const row = el('div', 'display:flex;gap:8px;flex-wrap:wrap');
  const btn = (label, fn) => { const b = el('button', 'padding:8px 16px;border:0;border-radius:6px;background:#3b8;color:#000;font-weight:600;cursor:pointer', label); b.onclick = fn; row.append(b); return b; };
  const copier = (label, body) => { const b = btn(label, async () => { ta.value = body; ta.focus(); ta.select(); try { await navigator.clipboard.writeText(body + '\n'); } catch { document.execCommand('copy'); } b.textContent = 'Copied'; }); };
  copier('Copy all', text);
  btn('Save as .txt', () => { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text + '\n'], { type: 'text/plain' })); a.download = `links-tiktok-chat-${day(Date.now())}.txt`; a.click(); });
  if (photos.length) copier(`Copy the ${photos.length} photo posts`, photos.join('\n'));
  btn('Close', () => panel.remove());
  panel.append(el('div', 'font-weight:600', summary), ta, row);
  document.body.append(panel);
})();
```
