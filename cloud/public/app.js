// Reels Hub online: the read-only copy of your library that your laptop keeps here (no videos: they play from
// their original link), plus a box for links your laptop processes the next time Reels Hub runs.
// Everything shown comes from your own laptop, but it is still text from the internet (titles, transcripts):
// it is only ever put on the page as text (textContent), never as HTML.
(() => {
  const $ = (s) => document.querySelector(s);
  const PAGE = 60;
  const NONE = '__unassigned__';                 // the Category option for Unassigned
  const PLATFORMS = ['YouTube', 'Instagram', 'TikTok', 'X', 'Web'];
  let items = [], meta = null, byId = new Map(), shown = PAGE, deepIds = null, deepQ = '', deepTimer = 0, listScroll = 0;
  let F = { q: '', deep: false, k: '', p: '', g: null, tg: '', c: '' };
  try { Object.assign(F, JSON.parse(localStorage.getItem('rh-phone') || '{}')); } catch (e) {}
  const save = () => { try { localStorage.setItem('rh-phone', JSON.stringify(F)); } catch (e) {} };

  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') n.className = v; else if (k === 'text') n.textContent = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v === true ? '' : v);
    }
    for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) n.append(c.nodeType ? c : String(c));
    return n;
  }
  async function api(path, opts) {
    let res;
    try { res = await fetch(path, { credentials: 'same-origin', ...opts }); }
    catch (e) { throw new Error('Can not reach Reels Hub online. If you were signed out, reload the page.'); }
    const type = res.headers.get('content-type') || '';
    if (!type.includes('json') && !type.startsWith('text/plain')) throw new Error('Your sign-in has expired: reload the page.');
    if (type.startsWith('text/plain')) { if (!res.ok) throw new Error(`Error ${res.status}`); return res.text(); }
    const j = await res.json();
    if (!res.ok || j.error) throw new Error(j.error || `Error ${res.status}`);
    return j;
  }
  const mmss = (t) => { t = Math.max(0, Math.floor(Number(t) || 0)); const h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = t % 60;
    return (h ? `${h}:${String(m).padStart(2, '0')}` : `${m}`) + ':' + String(s).padStart(2, '0'); };
  const httpUrl = (u) => (/^https?:\/\//i.test(String(u || '')) ? String(u) : null);
  const fold = (s) => String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  function ytId(u) {
    const m = String(u || '').match(/(?:youtube\.com\/(?:watch\?(?:[^#]*&)?v=|shorts\/|live\/|embed\/)|youtu\.be\/)([A-Za-z0-9_-]{11})/);
    return m ? m[1] : '';
  }
  function showErr(msg) { const e = $('#err'); e.textContent = msg || ''; e.hidden = !msg; }

  // ---------------------------------------------------------------- loading
  async function load() {
    try {
      const who = await api('/api/whoami');
      $('#who').textContent = who.name;
      if (!who.library) throw new Error(`No online library for ${who.name} yet: it is set up on Cloudflare first (DEPLOY.md, online library).`);
      const idx = await api('/api/lib/index');
      meta = idx.meta || {};
      items = (idx.chunks || []).flat().filter((x) => x && x.i);
      byId = new Map(items.map((x) => [x.i, x]));
      $('#state').hidden = !!items.length;
      $('#state').textContent = items.length ? '' : 'Nothing here yet: switch on "Online copy" in Reels Hub\'s Settings on your laptop.';
      header();
      options();
      render();
      api('/api/inbox?count=1').then((j) => header(j.count)).catch(() => {});
    } catch (e) {
      $('#state').hidden = true;
      showErr(e.message);
    }
    loadLinks();
    route();
  }
  function header(shares) {
    if (shares !== undefined) header.shares = shares;
    const c = (meta && meta.counts) || {}, n = header.shares || 0;
    const parts = [];
    if (c.v !== undefined) parts.push(`${Number(c.v).toLocaleString()} videos`);
    if (c.t) parts.push(`${Number(c.t).toLocaleString()} transcripts`);
    if (meta && meta.sending) parts.push(`still arriving from your laptop: ${Number(meta.sending.done).toLocaleString()} / ${Number(meta.sending.total).toLocaleString()}`);
    if (meta && meta.at) parts.push(`updated ${new Date(meta.at).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}`);
    if (n) parts.push(`${n} share${n === 1 ? '' : 's'} waiting for Reels Hub`);
    $('#counts').textContent = parts.join(' · ');
  }

  // ---------------------------------------------------------------- filters and list
  const catOf = (x) => x.g || '';
  function matches(x, skip) {
    if (skip !== 'k' && F.k && x.k !== F.k) return false;
    if (skip !== 'p' && F.p && x.p !== F.p) return false;
    if (skip !== 'g' && F.g !== null && F.g !== undefined && catOf(x) !== F.g) return false;
    if (skip !== 'tg' && F.tg && !(x.tg || []).includes(F.tg)) return false;
    if (skip !== 'c' && F.c && x.c !== F.c) return false;
    if (skip === 'q') return true;
    if (F.deep && deepIds && F.q.trim().length >= 2) return deepIds.has(x.i);
    const words = fold(F.q).split(/\s+/).filter(Boolean);
    if (!words.length) return true;
    const hay = fold([x.t, x.cn, x.g, (x.tg || []).join(' '), x.p].join(' '));
    return words.every((w) => hay.includes(w));
  }
  function counted(skip, keyOf) {
    const m = new Map();
    for (const x of items) if (matches(x, skip)) for (const k of [].concat(keyOf(x))) m.set(k, (m.get(k) || 0) + 1);
    return m;
  }
  function fill(sel, first, opts, value) {
    sel.replaceChildren(el('option', { value: '' , text: first }), ...opts.map((o) => el('option', { value: o.v, text: `${o.label} (${o.n})` })));
    sel.value = value;
    if (sel.value !== value) sel.value = '';
    sel.classList.toggle('on', !!sel.value);
  }
  function options() {
    const cats = counted('g', catOf), tg = counted('tg', (x) => x.tg || []), ch = counted('c', (x) => x.c || ''), pl = counted('p', (x) => x.p || '');
    const kinds = counted('k', (x) => x.k);
    const order = (meta && meta.categories) || [];
    const catList = ['', ...order, ...[...cats.keys()].filter((c) => c && !order.includes(c)).sort()];
    fill($('#fcat'), 'All categories', catList.filter((c) => cats.get(c) || c === F.g)
      .map((c) => ({ v: c === '' ? NONE : c, label: c || 'Unassigned', n: cats.get(c) || 0 })), F.g === null || F.g === undefined ? '' : F.g === '' ? NONE : F.g);
    fill($('#ftag'), 'All tags', [...tg.keys()].sort().filter((t) => tg.get(t) || t === F.tg).map((t) => ({ v: t, label: t, n: tg.get(t) || 0 })), F.tg);
    const names = new Map(items.map((x) => [x.c || '', x.cn || x.c || 'Unknown channel']));
    fill($('#fchan'), 'All channels', [...ch.keys()].filter((c) => c).sort((a, b) => ch.get(b) - ch.get(a) || names.get(a).localeCompare(names.get(b)))
      .map((c) => ({ v: c, label: names.get(c), n: ch.get(c) })), F.c);
    fill($('#fplat'), 'All sources', PLATFORMS.filter((p) => pl.get(p) || p === F.p).map((p) => ({ v: p, label: p, n: pl.get(p) || 0 })), F.p);
    const ks = $('#fkind');
    ks.options[1].textContent = `Videos (${kinds.get('v') || 0})`; ks.options[2].textContent = `Transcripts (${kinds.get('t') || 0})`;
    ks.value = F.k; ks.classList.toggle('on', !!F.k);
    $('#q').value = F.q;
    $('#deep').setAttribute('aria-pressed', String(!!F.deep));
    $('#fclear').hidden = !(F.k || F.p || (F.g !== null && F.g !== undefined) || F.tg || F.c || F.q);
  }
  function render() {
    const list = items.filter((x) => matches(x)).sort((a, b) => String(b.d || '').localeCompare(String(a.d || '')) || (b.a || 0) - (a.a || 0));
    const deepNote = F.deep && F.q.trim().length >= 2 ? (deepIds ? ' (searched inside the texts too)' : ' (searching inside the texts...)') : '';
    $('#found').textContent = items.length ? `${list.length.toLocaleString()} of ${items.length.toLocaleString()}${deepNote}` : '';
    const ul = $('#list');
    ul.replaceChildren(...list.slice(0, shown).map((x) => el('li', {},
      el('a', { href: '#v=' + encodeURIComponent(x.i) },
        el('span', { class: 't' }, el('span', { class: `badge ${x.k === 't' ? 'tr' : x.p}`, text: x.k === 't' ? 'Transcript' : x.p || 'Web' }), x.t || x.i),
        el('span', { class: 's' }, [x.cn, x.g || 'Unassigned', x.d, (x.tg || []).join(' ')].filter(Boolean).join(' · '),
          x.f ? el('span', { class: 'star', text: ` · ★ ${x.f}` }) : null)))));
    $('#more').hidden = list.length <= shown;
    $('#more').textContent = `Show more (${(list.length - shown).toLocaleString()} left)`;
  }
  function changed() { shown = PAGE; save(); options(); render(); }
  function deepSearch() {
    clearTimeout(deepTimer);
    const q = F.q.trim();
    if (!F.deep || q.length < 2) { deepIds = null; deepQ = ''; return; }
    if (q === deepQ && deepIds) return;
    deepIds = null;
    deepTimer = setTimeout(async () => {
      try {
        const j = await api('/api/lib/search?q=' + encodeURIComponent(q));
        if (F.q.trim() !== q) return;
        deepIds = new Set(j.ids); deepQ = q; showErr('');
      } catch (e) { showErr(e.message); deepIds = new Set(); }
      options(); render();
    }, 400);
  }

  // ---------------------------------------------------------------- one video
  function lazyImage(img, id, n) {
    const go = async () => {
      try { img.src = 'data:image/jpeg;base64,' + (await api(`/api/lib/image?id=${encodeURIComponent(id)}&n=${n}`)).trim(); }
      catch (e) { img.alt = 'Image not online yet'; }
    };
    if (!('IntersectionObserver' in window)) return go();
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { io.disconnect(); go(); } }, { rootMargin: '400px' });
    io.observe(img);
  }
  function highlight(node, q) {
    if (!q) return node;
    const text = node.textContent, low = fold(text), f = fold(q);
    if (!f || low.length !== text.length || !low.includes(f)) return node;
    const out = document.createDocumentFragment();
    let i = 0, j;
    while ((j = low.indexOf(f, i)) >= 0) { out.append(text.slice(i, j), el('mark', { text: text.slice(j, j + f.length) })); i = j + f.length; }
    out.append(text.slice(i));
    node.replaceChildren(out);
    return node;
  }
  async function openItem(id) {
    $('#listview').hidden = true; $('#itemview').hidden = false;
    const box = $('#item');
    const x = byId.get(id) || {};
    box.replaceChildren(el('h2', { text: x.t || 'Loading...' }), el('p', { class: 'meta', text: 'Loading...' }));
    window.scrollTo(0, 0);
    let it;
    try { it = await api('/api/lib/item?id=' + encodeURIComponent(id)); }
    catch (e) { box.replaceChildren(el('h2', { text: x.t || '' }), el('p', { class: 'err', text: e.message })); return; }
    if (location.hash !== '#v=' + encodeURIComponent(id)) return;         // gone back meanwhile
    const url = httpUrl(it.url), yt = ytId(url), q = F.deep ? F.q.trim() : '';
    const player = el('div', { class: 'player' + (/\/shorts\//.test(url || '') ? ' tall' : '') });
    const play = (t) => {
      player.replaceChildren(el('iframe', { src: `https://www.youtube-nocookie.com/embed/${yt}?autoplay=1&playsinline=1&start=${Math.floor(t || 0)}`,
        allow: 'autoplay; encrypted-media; picture-in-picture; fullscreen', allowfullscreen: true, title: 'YouTube video',
        referrerpolicy: 'origin' }));                     // YouTube refuses embeds without a referrer (only the address goes)
      if (!player.isConnected) acts.after(player);
      player.scrollIntoView({ behavior: 'smooth', block: 'center' });
    };
    const acts = el('div', { class: 'acts' },
      yt ? el('button', { class: 'btn primary', type: 'button', onclick: () => play(0), text: '▶ Play here' }) : null,
      url ? el('a', { class: 'btn', href: url, target: '_blank', rel: 'noopener noreferrer', text: `Open in ${it.platform || 'the browser'}` }) : null);
    const metaLine = [it.channel || it.author, it.date, it.duration ? mmss(it.duration) : '', it.slides ? `${it.slides} slides` : '',
      it.kind === 'transcript' ? 'transcript only' : '', it.lang ? `language ${it.lang}` : ''].filter(Boolean).join(' · ');
    const kids = [el('h2', { text: it.title || x.t || id }), el('p', { class: 'meta', text: metaLine }),
      el('div', { class: 'chips' }, el('span', { class: 'chip', text: it.category || 'Unassigned' }), ...(it.tags || []).map((t) => el('span', { class: 'chip', text: '#' + t }))),
      acts];
    const favs = Array.isArray(it.favorites) ? it.favorites : [];
    if (favs.length) {
      kids.push(el('h3', { text: `Favorites (${favs.length})` }), el('div', { class: 'favs' }, favs.map((f) => {
        const img = f.img !== null && f.img !== undefined ? el('img', { alt: '', loading: 'lazy' }) : null;
        if (img) lazyImage(img, id, f.img);
        return el('div', { class: 'fav' }, img, it.slides ? null : el('div', { class: 'tm', text: mmss(f.t) }), f.text ? highlight(el('p', { text: f.text }), q) : null);
      })));
    }
    if (it.notes) kids.push(el('h3', { text: 'My notes' }), highlight(el('div', { class: 'text', text: it.notes }), q));
    if (it.caption) kids.push(el('h3', { text: it.kind === 'transcript' ? 'Description' : 'Caption' }), highlight(el('div', { class: 'text', text: it.caption }), q));
    const lines = (rows, title) => {
      if (!Array.isArray(rows) || !rows.length) return;
      kids.push(el('h3', { text: title }), el('ul', { class: 'lines' }, rows.map(([t, s]) => el('li', {},
        yt ? el('button', { class: 'tm', type: 'button', onclick: () => play(t), text: mmss(t), title: 'Play from here' }) : el('span', { class: 'tm', text: it.slides ? '' : mmss(t) }),
        highlight(el('span', { text: s }), q)))));
    };
    lines(it.transcript, 'Transcript');
    lines(it.ocr, 'On-screen text');
    box.replaceChildren(...kids);
    const first = box.querySelector('mark');
    if (first) first.scrollIntoView({ block: 'center' });
  }
  function route() {
    const m = location.hash.match(/^#v=(.+)$/);
    if (m) {
      if (!$('#itemview').hidden && $('#item').dataset.id === m[1]) return;
      if ($('#itemview').hidden) listScroll = window.scrollY;
      $('#item').dataset.id = m[1];
      openItem(decodeURIComponent(m[1]));
    } else {
      $('#item').dataset.id = '';
      $('#item').replaceChildren();                                    // stops a playing video
      $('#itemview').hidden = true; $('#listview').hidden = false;
      window.scrollTo(0, listScroll);
    }
  }

  // ---------------------------------------------------------------- links for the laptop
  async function loadLinks() {
    let j;
    try { j = await api('/api/lib/links'); } catch (e) { return; }
    const ul = $('#links'), n = j.links.length;
    $('#waitn').hidden = !n; $('#waitn').textContent = `${n} waiting`;
    ul.replaceChildren(...j.links.map((l) => el('li', {},
      el('span', {}, l.mode === 'transcripts' ? 'Transcripts: ' : '', l.url),
      el('button', { class: 'x', type: 'button', title: 'Remove', 'aria-label': 'Remove', text: '×', onclick: async () => {
        try { await api('/api/lib/links?id=' + encodeURIComponent(l.id), { method: 'DELETE' }); } catch (e) { alert(e.message); }
        loadLinks();
      } }))));
  }
  $('#addform').addEventListener('submit', async (e) => {
    e.preventDefault();
    const err = $('#adderr'), b = e.submitter || $('#addform button');
    err.hidden = true; b.disabled = true;
    try {
      const j = await api('/api/lib/links', { method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ url: $('#addurl').value.trim(), mode: $('#addmode').value }) });
      $('#addurl').value = '';
      err.textContent = j.already ? 'That link is already waiting.' : 'Added: your laptop takes it the next time Reels Hub runs.';
      err.className = 'muted small'; err.hidden = false;
    } catch (ex) { err.textContent = ex.message; err.className = 'err'; err.hidden = false; }
    finally { b.disabled = false; }
    loadLinks();
  });

  // ---------------------------------------------------------------- events
  let qTimer = 0;
  $('#q').addEventListener('input', () => { clearTimeout(qTimer); qTimer = setTimeout(() => { F.q = $('#q').value; deepSearch(); changed(); }, 150); });
  $('#deep').addEventListener('click', () => { F.deep = !F.deep; deepSearch(); changed(); });
  $('#fkind').addEventListener('change', (e) => { F.k = e.target.value; changed(); });
  $('#fplat').addEventListener('change', (e) => { F.p = e.target.value; changed(); });
  $('#fcat').addEventListener('change', (e) => { const v = e.target.value; F.g = v === '' ? null : v === NONE ? '' : v; changed(); });
  $('#ftag').addEventListener('change', (e) => { F.tg = e.target.value; changed(); });
  $('#fchan').addEventListener('change', (e) => { F.c = e.target.value; changed(); });
  $('#fclear').addEventListener('click', () => { F = { q: '', deep: F.deep, k: '', p: '', g: null, tg: '', c: '' }; deepSearch(); changed(); });
  $('#more').addEventListener('click', () => { shown += PAGE * 2; render(); });
  $('#back').addEventListener('click', () => { if (history.length > 1 && route.fromList) history.back(); else location.hash = ''; });
  $('#home').addEventListener('click', (e) => { e.preventDefault(); location.hash = ''; F.q = ''; changed(); });
  $('#list').addEventListener('click', (e) => { if (e.target.closest('a')) route.fromList = true; });
  window.addEventListener('hashchange', route);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) loadLinks(); });
  deepSearch();
  load();
})();
