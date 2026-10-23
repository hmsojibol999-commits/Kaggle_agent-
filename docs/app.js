/* WordClip Studio - UI. Vanilla JS, no build step. Logic lives in core.js. */
(function () {
  'use strict';
  const W = window.WC;
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const bn = (n) => Number(n).toLocaleString('bn-BD');
  const app = $('#app');

  /* ---------------- storage ---------------- */
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch (e) { return false; } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
  };
  const cfg = {
    get token() { return store.get('wc.token', ''); },
    get repo() { return store.get('wc.repo', '') || W.detectRepo(location); },
    get theme() { return store.get('wc.theme', 'auto'); },
  };
  let gh = null;
  function client() {
    if (!cfg.token || !cfg.repo) return null;
    if (!gh || gh.token !== cfg.token || gh.repo !== cfg.repo) gh = new W.GitHub({ token: cfg.token, repo: cfg.repo });
    return gh;
  }

  /* ---------------- sessions (last 10) ---------------- */
  const sessions = () => store.get('wc.sessions', []);
  function saveSession(meta) {
    const list = sessions().filter((s) => s.id !== meta.id);
    const old = sessions().find((s) => s.id === meta.id) || {};
    list.unshift({ ...old, ...meta });
    list.sort((a, b) => (a.id < b.id ? 1 : -1));
    const keep = list.slice(0, 10);
    list.slice(10).forEach((s) => store.del('wc.ui.' + s.id));
    store.set('wc.sessions', keep);
  }
  const getSession = (id) => sessions().find((s) => s.id === id);
  const newUI = () => ({ active: null, skipped: [], picks: {}, uploads: {}, editing: null });
  const loadUI = (sid) => ({ ...newUI(), ...store.get('wc.ui.' + sid, {}) });
  const saveUI = () => { if (cur) store.set('wc.ui.' + cur.sid, cur.ui); };

  /* ---------------- busy overlay ---------------- */
  let busy = null, busyTimer = null;
  function setBusy(label, url) {
    busy = { label, url: url || (busy && busy.url) || '', t0: busy ? busy.t0 : Date.now() };
    if (!busyTimer) busyTimer = setInterval(paintBusy, 1000);
    paintBusy();
    render(true);
  }
  function clearBusy() { busy = null; clearInterval(busyTimer); busyTimer = null; paintBusy(); render(true); }
  function paintBusy() {
    const el = $('#status');
    if (!busy) { el.hidden = true; return; }
    const s = Math.floor((Date.now() - busy.t0) / 1000);
    el.hidden = false;
    el.innerHTML = `<span class="spin"></span>${esc(busy.label)} <span>${bn(Math.floor(s / 60))}:${bn(String(s % 60).padStart(2, '0'))}</span>` +
      (busy.url ? `<small><a href="${esc(busy.url)}" target="_blank" rel="noopener">GitHub Actions-এ দেখুন</a></small>` : '');
  }

  /* ---------------- helpers ---------------- */
  let cur = null;          // current session {sid, cand, ui, error, build}
  let notice = null;       // transient message {kind, title, text, link}
  function toast(kind, title, text, link) { notice = { kind, title, text, link }; render(true); window.scrollTo({ top: 0, behavior: 'smooth' }); }
  function errFromCode(code, detail, link) {
    const [t, f] = W.mapError(code);
    return { kind: 'bad', title: t, text: f + (detail && code === 'unknown' ? ' (' + detail + ')' : ''), link };
  }
  function errFromGH(e) {
    if (e && e.message === 'run-not-found') return { kind: 'bad', title: 'কাজটি শুরু হয়নি।', text: 'Repo-তে Actions চালু আছে কি দেখুন, তারপর আবার চেষ্টা করুন।' };
    if (e && e.message === 'timeout') return { kind: 'bad', title: 'অনেক সময় লাগছে।', text: 'Actions পেজে গিয়ে দেখুন কাজটি চলছে কি না।' };
    const [t, f] = W.httpMessage(e && e.status);
    return { kind: 'bad', title: t, text: f };
  }
  const needClient = () => {
    const c = client();
    if (!c) toast('warn', 'আগে Settings ঠিক করুন।', 'GitHub token আর repo-র নাম বসাতে হবে।');
    return c;
  };
  const wordsPerVideo = () => (cur && cur.cand && cur.cand.words_per_video) || 3;

  /* ---------------- routing ---------------- */
  function route() {
    const h = location.hash || '#home';
    if (h.startsWith('#s/')) return { name: 'session', sid: h.slice(3) };
    if (h === '#settings') return { name: 'settings' };
    return { name: 'home' };
  }
  window.addEventListener('hashchange', () => { notice = null; enter(); });

  async function enter() {
    const r = route();
    document.querySelectorAll('[data-nav]').forEach((a) => a.setAttribute('aria-current', a.dataset.nav === (r.name === 'session' ? 'home' : r.name) ? 'page' : 'false'));
    if (r.name === 'session') { await openSession(r.sid); }
    else { cur = null; render(); if (r.name === 'home') syncSessionsFromState(); }
    window.scrollTo(0, 0);
  }

  function render(keepScroll) {
    const y = window.scrollY;
    const r = route();
    if (r.name === 'settings') app.innerHTML = viewSettings();
    else if (r.name === 'session') app.innerHTML = viewSession();
    else app.innerHTML = viewHome();
    if (keepScroll) window.scrollTo(0, y);
  }

  const noticeHtml = () => !notice ? '' :
    `<div class="notice ${notice.kind || ''}" role="alert"><strong>${esc(notice.title)}</strong>${esc(notice.text || '')}` +
    (notice.link ? ` <a href="${esc(notice.link)}" target="_blank" rel="noopener">লগ দেখুন</a>` : '') + `</div>`;

  /* ---------------- HOME ---------------- */
  const CATS = ['kitchen', 'tools', 'garden', 'office', 'bathroom', 'clothing', 'furniture', 'toys', 'sports', 'vehicles', 'food', 'electronics'];
  const CAT_BN = { kitchen: 'রান্নাঘর', tools: 'যন্ত্রপাতি', garden: 'বাগান', office: 'অফিস', bathroom: 'বাথরুম', clothing: 'পোশাক', furniture: 'আসবাব', toys: 'খেলনা', sports: 'খেলাধুলা', vehicles: 'যানবাহন', food: 'খাবার', electronics: 'ইলেকট্রনিক্স' };
  const STATUS_CHIP = { preparing: ['চলছে', 'run'], ready: ['প্রস্তুত', ''], building: ['Build চলছে', 'run'], done: ['ভিডিও তৈরি', 'ok'], error: ['ত্রুটি', 'bad'] };
  function sessionTitle(s) {
    const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(s.id);
    if (!m) return s.id;
    const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]));
    return d.toLocaleString('bn-BD', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' }) + (s.category ? ' · ' + (CAT_BN[s.category] || s.category) : '');
  }
  function viewHome() {
    const noTok = !cfg.token || !cfg.repo;
    const list = sessions();
    return `${noticeHtml()}
    ${noTok ? `<div class="notice warn"><strong>প্রথমে Settings ঠিক করুন</strong>GitHub token আর repo-র নাম একবার বসাতে হবে।
       <div class="row" style="margin-top:10px"><a class="btn primary" href="#settings">Settings খুলুন</a></div></div>` : ''}
    <section class="card">
      <h2>নতুন ভিডিও</h2>
      <p class="muted">৩টি শব্দ আর প্রতিটির ৪টি ছবি আনা হবে। প্রায় ১ মিনিট লাগে।</p>
      <label class="field" for="cat">ক্যাটাগরি (ঐচ্ছিক)</label>
      <select id="cat"><option value="">যেকোনো</option>${CATS.map((c) => `<option value="${c}">${CAT_BN[c]} (${c})</option>`).join('')}</select>
      <div style="height:14px"></div>
      <button class="btn primary big" data-act="prepare" ${busy || noTok ? 'disabled' : ''}>Prepare</button>
    </section>
    <section class="card">
      <h2>আগের session</h2>
      ${list.length ? `<ul class="list">${list.map((s) => {
        const [t, k] = STATUS_CHIP[s.status] || ['', ''];
        return `<li><a href="#s/${esc(s.id)}">${esc(sessionTitle(s))}</a><span class="chip ${k}">${t}</span></li>`;
      }).join('')}</ul>` : '<p class="muted">এখনো কিছু নেই। উপরের Prepare চাপুন।</p>'}
      <p class="muted">শেষের ১০টি session থাকে।</p>
    </section>`;
  }

  async function syncSessionsFromState() {
    const c = client();
    if (!c) return;
    try {
      const dir = await c.readStateJson('sessions');
      if (!Array.isArray(dir)) return;
      let changed = false;
      dir.filter((d) => d.type === 'dir' && W.SID_RE.test(d.name)).forEach((d) => {
        if (!getSession(d.name)) { saveSession({ id: d.name, status: 'ready' }); changed = true; }
      });
      if (changed && route().name === 'home') render(true);
    } catch (e) { /* offline or no state yet: ignore */ }
  }

  /* ---------------- run helpers ---------------- */
  async function runWorkflow(file, inputs, titleHas, label) {
    const c = needClient();
    if (!c) return null;
    setBusy(label);
    const run = await c.dispatch(file, inputs, titleHas);
    setBusy(label, run.html_url);
    const done = await c.waitRun(run.id, (r) => { if (r.status === 'queued') setBusy(label + ' (লাইনে আছে)', r.html_url); else setBusy(label, r.html_url); });
    return done;
  }

  async function doPrepare() {
    const c = needClient();
    if (!c || busy) return;
    const sid = W.newSessionId();
    const category = ($('#cat') || {}).value || '';
    saveSession({ id: sid, category, status: 'preparing' });
    notice = null;
    try {
      const run = await runWorkflow('prepare.yml', { session_id: sid, category }, sid, 'শব্দ ও ছবি আনা হচ্ছে…');
      await loadCandidates(sid, run);
      location.hash = '#s/' + sid;
    } catch (e) {
      saveSession({ id: sid, status: 'error' });
      notice = e && e.handled ? e.err : errFromGH(e);
    } finally { clearBusy(); }
  }

  /* read candidates + status after a prepare/more run */
  async function loadCandidates(sid, run) {
    const c = client();
    const st = await c.readStateJson(`sessions/${sid}/status.json`).catch(() => null);
    if ((run && run.conclusion !== 'success') || (st && st.state === 'error')) {
      const err = st && st.code ? errFromCode(st.code, st.detail, run && run.html_url) : errFromCode('unknown', '', run && run.html_url);
      saveSession({ id: sid, status: 'error', error: err });
      throw Object.assign(new Error('prepare-failed'), { handled: true, err });
    }
    const cand = await c.readStateJson(`sessions/${sid}/candidates.json`);
    if (!cand) throw Object.assign(new Error('no-candidates'), { status: 404 });
    saveSession({ id: sid, status: 'ready', error: null });
    return cand;
  }

  /* ---------------- SESSION ---------------- */
  async function openSession(sid) {
    if (!W.SID_RE.test(sid)) { location.hash = '#home'; return; }
    cur = { sid, cand: null, ui: loadUI(sid), error: null, build: null };
    if (!getSession(sid)) saveSession({ id: sid, status: 'ready' });
    render();
    const c = client();
    if (!c) { notice = { kind: 'warn', title: 'Settings ঠিক করুন।', text: 'token ছাড়া session খোলা যায় না।' }; render(); return; }
    try {
      cur.cand = await c.readStateJson(`sessions/${sid}/candidates.json`);
      if (!cur.cand) {
        const st = await c.readStateJson(`sessions/${sid}/status.json`).catch(() => null);
        if (st && st.state === 'error') cur.error = errFromCode(st.code, st.detail);
        else cur.error = { kind: 'warn', title: 'এখনো তৈরি হয়নি।', text: 'Prepare চলছে হতে পারে। একটু পরে পাতা রিফ্রেশ করুন।' };
      } else {
        initActive();
        const bj = await c.readStateJson(`sessions/${sid}/build.json`).catch(() => null);
        if (bj && bj.state === 'done') await showBuild(bj); else if (bj && bj.state === 'error') cur.error = errFromCode(bj.code, bj.detail);
      }
    } catch (e) { cur.error = errFromGH(e); }
    render();
  }

  function initActive() {
    const n = wordsPerVideo();
    const total = cur.cand.words.length;
    if (!cur.ui.active || cur.ui.active.length !== n) {
      cur.ui.active = Array.from({ length: Math.min(n, total) }, (_, i) => i);
      saveUI();
    }
  }
  const spareIdx = () => cur.cand.words.map((_, i) => i).filter((i) => !cur.ui.active.includes(i) && !cur.ui.skipped.includes(cur.cand.words[i].word));

  /* candidate list for one word: own uploads first, then Pixabay photos */
  function thumbsFor(ci) {
    const w = cur.cand.words[ci];
    const ups = (cur.ui.uploads[ci] || []).map((u) => ({ key: 'u:' + u.path, upload: u.path, src: u.preview, thumb: u.preview, w: u.w, h: u.h, mine: true }));
    const ph = w.photos.map((p) => ({ key: 'p:' + p.id, photoId: p.id, src: p.large || p.thumb, thumb: p.thumb, w: p.w, h: p.h, who: p.photographer }));
    return ups.concat(ph);
  }
  const pickOf = (ci) => cur.ui.picks[ci] || null;
  const isReady = () => cur.ui.active.every((ci) => { const p = pickOf(ci); return p && p.focus; });

  function viewSession() {
    if (!cur) return '';
    const s = getSession(cur.sid) || {};
    let html = noticeHtml();
    if (cur.error) html += `<div class="notice ${cur.error.kind || 'bad'}"><strong>${esc(cur.error.title)}</strong>${esc(cur.error.text)}${cur.error.link ? ` <a href="${esc(cur.error.link)}" target="_blank" rel="noopener">লগ দেখুন</a>` : ''}</div>`;
    if (!cur.cand) return html + `<p class="muted">${esc(sessionTitle(s))}</p>` + (busy ? '' : '<div class="row"><button class="btn" data-act="reload">আবার দেখুন</button><a class="btn" href="#home">Home</a></div>');
    if (cur.build) html += viewBuild();
    html += `<div class="card"><h2>${esc(sessionTitle(s))}</h2>
      <p class="hint">১) ছবিতে ট্যাপ করে বাছুন। ২) ছবির জিনিসটির ওপর ট্যাপ করুন, তীর ঠিক সেখানে দেখাবে। ৩) চাইলে আঙুল টেনে ছবি সরান। (পাতা স্ক্রল করতে ছবির বাইরের ফাঁকা অংশে আঙুল রাখুন।)</p></div>`;
    cur.ui.active.forEach((ci, k) => { html += viewWord(ci, k); });
    const done = cur.ui.active.filter((ci) => { const p = pickOf(ci); return p && p.focus; }).length;
    const n = cur.ui.active.length;
    html += `<div class="sticky"><div class="inner">
      <button class="btn primary big" data-act="build" ${!isReady() || busy ? 'disabled' : ''}>Build</button>
      <div class="muted" style="text-align:center;margin-top:4px">${isReady() ? 'সব ঠিক আছে, Build চাপুন' : `${bn(done)}/${bn(n)} শব্দ প্রস্তুত। প্রতিটির ছবি আর ফোকাস দিন।`}</div>
    </div></div>`;
    return html;
  }

  function badgeFor(t) {
    if (W.needsUpscale(t.w, t.h, 1)) return `<span class="badge warn">ছবি ছোট, ঝাপসা হতে পারে</span>`;
    if (t.mine) return `<span class="badge mine">আপনার ছবি</span>`;
    return t.who ? `<span class="badge">${esc(t.who)}</span>` : '';
  }

  function viewWord(ci, k) {
    const w = cur.cand.words[ci];
    const p = pickOf(ci);
    const editing = cur.ui.editing === ci || (!p);
    const head = `<div class="word-head"><span class="no">${bn(k + 1)}</span><h3>${esc(w.word)}</h3><span class="chip">${esc(w.category || '')}</span>${p && p.focus && !editing ? '<span class="chip ok">ফোকাস ✓</span>' : ''}</div>`;   // no chip while editing: it could wrap and make the card jump under the finger
    if (p && !editing) {
      const t = thumbsFor(ci).find((x) => x.key === p.key);
      return `<section class="word card">${head}<div class="row" style="align-items:center">
        <div style="width:90px;flex:none"><button class="thumb" style="width:90px" data-act="edit" data-ci="${ci}" aria-label="ছবি ঠিক করুন">${t ? `<img alt="" src="${esc(t.thumb)}" style="object-position:${p.offset[0] * 100}% ${p.offset[1] * 100}%">` : ''}</button></div>
        <button class="btn" data-act="edit" data-ci="${ci}">ছবি বা ফোকাস বদলান</button></div></section>`;
    }
    let editor = '';
    if (p) {
      const t = thumbsFor(ci).find((x) => x.key === p.key);
      if (t) editor = editorHtml(ci, p, t);
    }
    const grid = thumbsFor(ci).map((t) => `<button class="thumb" data-act="pick" data-ci="${ci}" data-key="${esc(t.key)}" aria-pressed="${p && p.key === t.key}" aria-label="ছবি বাছুন">
        <img alt="" loading="lazy" src="${esc(t.thumb)}">${p && p.key === t.key ? '<span class="tick">✓</span>' : ''}${badgeFor(t)}</button>`).join('');
    const canSkip = spareIdx().length > 0;
    return `<section class="word card">${head}
      ${p ? '' : '<p class="hint">একটি ছবিতে ট্যাপ করুন।</p>'}${editor}
      <div class="grid">${grid}</div>
      <div class="row">
        <button class="btn small" data-act="more" data-ci="${ci}" ${busy ? 'disabled' : ''}>আরও ছবি</button>
        <button class="btn small" data-act="skip" data-ci="${ci}" ${busy || !canSkip ? 'disabled' : ''}>বাদ দিন</button>
        <label class="btn small" style="cursor:pointer">নিজের ছবি<input type="file" accept="image/*" hidden data-up="${ci}"></label>
      </div>
      ${p && p.focus ? `<p class="hint">ছবি ঠিক থাকলে নিচের শব্দে যান।${cur.ui.active.indexOf(ci) < cur.ui.active.length - 1 ? '' : ' তারপর Build চাপুন।'}</p>` : ''}
    </section>`;
  }

  function editorHtml(ci, p, t) {
    const [vx, vy] = W.imageToView(t.w, t.h, p.offset, p.focus ? p.focus[0] : 0.5, p.focus ? p.focus[1] : 0.5);
    const shown = p.focus && vx >= 0 && vx <= 1 && vy >= 0 && vy <= 1;
    return `<div class="phone" data-ci="${ci}">
      <div class="p-title">WHAT’S THIS CALLED?</div>
      <div class="p-card" data-editor="${ci}" role="img" aria-label="ছবির জিনিসটির ওপর ট্যাপ করুন">
        <img alt="" src="${esc(t.src)}" style="object-position:${p.offset[0] * 100}% ${p.offset[1] * 100}%">
        <div class="cross" style="left:${vx * 100}%;top:${vy * 100}%;display:${shown ? 'block' : 'none'}">
          <svg viewBox="0 0 44 44"><circle cx="22" cy="22" r="13" fill="none" stroke="#fff" stroke-width="3"/><circle cx="22" cy="22" r="13" fill="none" stroke="#FF3B2F" stroke-width="1.5"/><path d="M22 2v12M22 30v12M2 22h12M30 22h12" stroke="#fff" stroke-width="3"/><path d="M22 2v12M22 30v12M2 22h12M30 22h12" stroke="#FF3B2F" stroke-width="1.5"/></svg>
        </div>
      </div>
      <div class="p-ans">${esc(cur.cand.words[ci].word)}</div>
      <div class="p-safe"></div>
    </div>
    <p class="focus-note">${p.focus ? 'ফোকাস ঠিক আছে ✓ আবার ট্যাপ করলে সরবে।' : '👆 জিনিসটির ওপর ট্যাপ করুন'}</p>
    ${W.needsUpscale(t.w, t.h, 1) ? '<p class="notice warn">এই ছবি কার্ডের চেয়ে ছোট। ভিডিওতে ঝাপসা দেখাতে পারে। আরেকটি ছবি বাছা ভালো।</p>' : ''}`;
  }

  /* tap = focus, drag = move the crop (pointer events are delegated once on #app) */
  let drag = null;
  app.addEventListener('pointerdown', (e) => {
    const card = e.target.closest('.p-card');
    if (!card || !cur) return;
    const ci = +card.dataset.editor;
    const p = pickOf(ci);
    const t = thumbsFor(ci).find((x) => x.key === p.key);
    if (!t) return;
    const br = card.getBoundingClientRect();     // use the content box (inside the 2 px border): exact mapping
    const r = { left: br.left + card.clientLeft, top: br.top + card.clientTop, width: card.clientWidth, height: card.clientHeight };
    drag = { ci, card, t, p, x0: e.clientX, y0: e.clientY, off0: p.offset.slice(), moved: false, r };
    card.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  app.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x0, dy = e.clientY - drag.y0;
    if (!drag.moved && Math.hypot(dx, dy) < 8) return;
    drag.moved = true;
    const { r, t } = drag;
    const s = Math.max(r.width / t.w, r.height / t.h);       // displayed cover scale
    const slackX = t.w * s - r.width, slackY = t.h * s - r.height;
    const ox = slackX > 1 ? W.clamp(drag.off0[0] - dx / slackX, 0, 1) : drag.off0[0];
    const oy = slackY > 1 ? W.clamp(drag.off0[1] - dy / slackY, 0, 1) : drag.off0[1];
    drag.p.offset = [ox, oy];
    paintEditor(drag);
  });
  const endDrag = (e) => {
    if (!drag) return;
    const d = drag; drag = null;
    if (!d.moved) {                                           // a tap: set the focus point
      const px = W.clamp((e.clientX - d.r.left) / d.r.width, 0, 1);
      const py = W.clamp((e.clientY - d.r.top) / d.r.height, 0, 1);
      d.p.focus = W.viewToImage(d.t.w, d.t.h, d.p.offset, px, py);
      saveUI();
      render(true);
    } else { saveUI(); render(true); }
  };
  app.addEventListener('pointerup', endDrag);
  app.addEventListener('pointercancel', () => { drag = null; });
  function paintEditor(d) {
    const img = d.card.querySelector('img');
    img.style.objectPosition = `${d.p.offset[0] * 100}% ${d.p.offset[1] * 100}%`;
    const cross = d.card.querySelector('.cross');
    if (d.p.focus) {
      const [vx, vy] = W.imageToView(d.t.w, d.t.h, d.p.offset, d.p.focus[0], d.p.focus[1]);
      const ok = vx >= 0 && vx <= 1 && vy >= 0 && vy <= 1;
      cross.style.display = ok ? 'block' : 'none';
      cross.style.left = vx * 100 + '%'; cross.style.top = vy * 100 + '%';
    }
  }

  /* ---------------- per-word actions ---------------- */
  function pickPhoto(ci, key) {
    const t = thumbsFor(ci).find((x) => x.key === key);
    if (!t) return;
    const old = pickOf(ci);
    if (old && old.key === key) return;
    cur.ui.picks[ci] = { key, photoId: t.photoId, upload: t.upload, focus: null, offset: [0.5, 0.5] };
    cur.ui.editing = ci;
    saveUI(); render(true);
    setTimeout(() => { const el = document.querySelector(`.phone[data-ci="${ci}"]`); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' }); }, 60);
  }

  function skipWord(ci) {
    const sp = spareIdx();
    if (!sp.length) { toast('warn', 'আর spare শব্দ নেই।', 'অন্য শব্দ চাইলে নতুন Prepare চালান।'); return; }
    const k = cur.ui.active.indexOf(ci);
    cur.ui.skipped.push(cur.cand.words[ci].word);
    cur.ui.active[k] = sp[0];
    delete cur.ui.picks[ci];
    cur.ui.editing = null;
    saveUI(); render(true);
  }

  async function moreImages(ci) {
    if (busy) return;
    const w = cur.cand.words[ci];
    try {
      const run = await runWorkflow('more_images.yml', { session_id: cur.sid, word: w.word }, cur.sid, 'আরও ছবি আনা হচ্ছে…');
      const ms = await client().readStateJson(`sessions/${cur.sid}/more_status.json`).catch(() => null);
      if ((run && run.conclusion !== 'success') || (ms && ms.state === 'error')) {
        notice = errFromCode(ms && ms.code || 'unknown', ms && ms.detail, run && run.html_url);
      } else {
        cur.cand = await client().readStateJson(`sessions/${cur.sid}/candidates.json`);
        notice = null;
      }
    } catch (e) { notice = errFromGH(e); }
    finally { clearBusy(); }
  }

  /* upload: resize in the browser (<= 3000 px, <= 10 MB), commit to the state branch */
  const MAX_UPLOAD = 10 * 1024 * 1024;
  function loadImg(file) {
    return new Promise((res, rej) => {
      const url = URL.createObjectURL(file);
      const im = new Image();
      im.onload = () => res({ im, url });
      im.onerror = () => { URL.revokeObjectURL(url); rej(new Error('bad image')); };
      im.src = url;
    });
  }
  function toJpeg(im, maxSide, q) {
    const s = Math.min(1, maxSide / Math.max(im.naturalWidth, im.naturalHeight));
    const cv = document.createElement('canvas');
    cv.width = Math.round(im.naturalWidth * s); cv.height = Math.round(im.naturalHeight * s);
    cv.getContext('2d').drawImage(im, 0, 0, cv.width, cv.height);
    return new Promise((res) => cv.toBlob((b) => res({ blob: b, w: cv.width, h: cv.height, cv }), 'image/jpeg', q));
  }
  const blobB64 = (blob) => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(',')[1]); r.onerror = rej; r.readAsDataURL(blob); });
  async function uploadOwn(ci, file) {
    const c = needClient();
    if (!c || !file) return;
    if (file.size > MAX_UPLOAD * 3) { toast('bad', 'ছবি অনেক বড়।', 'ছবিটি ৩০ MB-এর নিচে হতে হবে।'); return; }
    setBusy('ছবি পাঠানো হচ্ছে…');
    try {
      const { im, url } = await loadImg(file);
      let out = await toJpeg(im, 3000, 0.92);
      for (let q = 0.85; out.blob.size > MAX_UPLOAD && q > 0.4; q -= 0.15) out = await toJpeg(im, 3000, q);
      if (out.blob.size > MAX_UPLOAD) throw Object.assign(new Error('too big'), { status: 413 });
      const prev = await toJpeg(im, 900, 0.8);
      const preview = prev.cv.toDataURL('image/jpeg', 0.8);
      URL.revokeObjectURL(url);
      const path = `sessions/${cur.sid}/uploads/${Date.now().toString(36)}.jpg`;
      await c.putState(path, await blobB64(out.blob), `upload ${cur.sid}`);
      (cur.ui.uploads[ci] = cur.ui.uploads[ci] || []).unshift({ path, preview, w: out.w, h: out.h });
      cur.ui.picks[ci] = { key: 'u:' + path, upload: path, focus: null, offset: [0.5, 0.5] };
      cur.ui.editing = ci;
      if (!saveUI()) { cur.ui.uploads[ci][0].preview = ''; }
      notice = null;
    } catch (e) {
      notice = e.status === 413 ? { kind: 'bad', title: 'ছবি ১০ MB-এর বেশি।', text: 'ছোট মাপের ছবি দিন।' } : errFromGH(e);
    } finally { clearBusy(); }
  }

  /* ---------------- build ---------------- */
  async function doBuild() {
    if (busy || !isReady()) return;
    const words = cur.ui.active.map((ci) => ({ word: cur.cand.words[ci].word, pick: cur.ui.picks[ci] }));
    const selection = W.buildSelection({ sid: cur.sid, theme: cfg.theme, words, skipped: cur.ui.skipped });
    if (selection.length > 60000) { toast('bad', 'বাছাই অনেক বড়।', 'পাতা রিফ্রেশ করে আবার চেষ্টা করুন।'); return; }
    saveSession({ id: cur.sid, status: 'building' });
    notice = null; cur.error = null; cur.build = null;
    try {
      const run = await runWorkflow('build.yml', { session_id: cur.sid, selection }, cur.sid, 'ভিডিও বানানো হচ্ছে… (২–৫ মিনিট)');
      const bj = await client().readStateJson(`sessions/${cur.sid}/build.json`).catch(() => null);
      if (run.conclusion === 'success' && bj && bj.state === 'done') {
        await showBuild(bj);
        saveSession({ id: cur.sid, status: 'done', tag: bj.tag });
      } else {
        const code = bj && bj.code ? bj.code : 'unknown';
        cur.error = errFromCode(code, bj && bj.detail, run.html_url);
        saveSession({ id: cur.sid, status: 'error' });
      }
    } catch (e) { cur.error = errFromGH(e); saveSession({ id: cur.sid, status: 'ready' }); }
    finally { clearBusy(); window.scrollTo({ top: 0, behavior: 'smooth' }); }
  }

  async function showBuild(bj) {
    let rel = null;
    try { rel = await client().releaseByTag(bj.tag); } catch (e) { /* show without links */ }
    const asset = (n) => rel && (rel.assets || []).find((a) => a.name === n);
    cur.build = { tag: bj.tag, words: bj.words || [], voices: bj.voices || {}, flagged: bj.flagged || [], rel,
      video: asset('video.mp4'), cover: asset('cover.jpg'), sheet: asset('contact-sheet.jpg'), credits: asset('credits.json') };
  }
  function viewBuild() {
    const b = cur.build;
    const a = (x, label, dl) => x ? `<a class="btn small" href="${esc(x.browser_download_url)}" ${dl ? 'download' : 'target="_blank" rel="noopener"'}>${label}</a>` : '';
    const eng = Object.keys(b.voices).map((k) => `${k} ×${b.voices[k]}`).join(', ');
    return `<section class="card"><h2>ভিডিও তৈরি হয়েছে 🎉</h2>
      <p class="muted">শব্দ: ${esc(b.words.join(', '))}${eng ? ' · ভয়েস: ' + esc(eng) : ''}</p>
      ${b.flagged.length ? `<p class="notice warn">এই শব্দের ছবি ছোট ছিল: ${esc(b.flagged.join(', '))}</p>` : ''}
      ${b.video ? `<video controls playsinline preload="metadata" ${b.cover ? `poster="${esc(b.cover.browser_download_url)}"` : ''} src="${esc(b.video.browser_download_url)}"></video>` : ''}
      <div class="row" style="margin-top:12px">
        ${a(b.video, 'ভিডিও ডাউনলোড করুন', true).replace('btn small', 'btn primary')}
        ${a(b.cover, 'Cover')}${a(b.sheet, 'Contact sheet')}${a(b.credits, 'Credits')}
        ${b.rel ? `<a class="btn small" href="${esc(b.rel.html_url)}" target="_blank" rel="noopener">Release পাতা</a>` : ''}
      </div>
      <p class="muted">ছবি Pixabay থেকে। ফটোগ্রাফারদের নাম Release-এর নোটে আছে। পোস্টের বর্ণনায় দিলে ভালো।</p></section>`;
  }

  /* ---------------- SETTINGS ---------------- */
  let tokenTest = null, setupReport = null, voiceReport = null;
  const THEMES = ['auto', 'yellow', 'blue', 'green', 'purple', 'orange', 'teal'];
  const THEME_BN = { auto: 'অটো (ঘুরিয়ে ঘুরিয়ে)', yellow: 'হলুদ', blue: 'নীল', green: 'সবুজ', purple: 'বেগুনি', orange: 'কমলা', teal: 'টিল' };
  const STATUS_BN = { ok: 'ঠিক আছে', warn: 'সতর্কতা', fail: 'সমস্যা' };

  function viewSettings() {
    const det = W.detectRepo(location);
    const check = (c) => `<div class="check"><span class="dot ${c.status}"></span><div><strong>${esc(c.label)}</strong> <span class="chip ${c.status === 'ok' ? 'ok' : c.status === 'warn' ? 'warn' : 'bad'}">${STATUS_BN[c.status]}</span>
        ${c.detail ? `<div class="d">${esc(c.detail)}</div>` : ''}${c.fix ? `<div class="fix">${esc(c.fix)}</div>` : ''}</div></div>`;
    return `${noticeHtml()}
    <section class="card"><h2>GitHub token</h2>
      <p class="muted">Token শুধু এই ফোনের ব্রাউজারে থাকে, অন্য কোথাও যায় না। শুধু এই repo-র জন্য বানানো fine-grained token দিন।</p>
      <label class="field" for="tok">Token</label>
      <input id="tok" type="password" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="github_pat_…" value="${esc(cfg.token)}">
      <label class="field" for="repo">Repo (owner/repo)</label>
      <input id="repo" type="text" autocapitalize="off" spellcheck="false" placeholder="${esc(det || 'আপনার-নাম/repo-র-নাম')}" value="${esc(store.get('wc.repo', ''))}">
      <p class="muted">${det ? `স্বয়ংক্রিয়ভাবে পাওয়া গেছে: ${esc(det)}` : 'এই সাইট github.io-তে নেই, তাই repo-র নাম নিজে লিখুন।'}</p>
      <div class="row" style="margin-top:12px">
        <button class="btn primary" data-act="save-settings">সংরক্ষণ করুন</button>
        <button class="btn" data-act="test-token" ${busy ? 'disabled' : ''}>Token পরীক্ষা করুন</button>
        <button class="btn danger" data-act="clear-token">Token মুছুন</button>
      </div>
      ${tokenTest ? `<div class="token-test" style="margin-top:12px">${tokenTest.map((t) => `<div class="check"><span class="dot ${t.ok ? 'ok' : 'fail'}"></span><div><strong>${esc(t.label)}</strong>${t.ok ? '' : `<div class="fix">${esc(t.hint)}</div>`}</div></div>`).join('')}</div>` : ''}
    </section>
    <section class="card"><h2>ভিডিওর থিম</h2>
      <select id="theme" aria-label="ভিডিওর থিম">${THEMES.map((t) => `<option value="${t}" ${cfg.theme === t ? 'selected' : ''}>${THEME_BN[t]}</option>`).join('')}</select>
    </section>
    <section class="card"><h2>Setup পরীক্ষা</h2>
      <p class="muted">Key, মডেল, Pixabay, ffmpeg সব ঠিক আছে কি দেখে নেয়। প্রায় ১ মিনিট লাগে।</p>
      <button class="btn primary" data-act="check-setup" ${busy ? 'disabled' : ''}>Setup পরীক্ষা চালান</button>
      ${setupReport ? `<div style="margin-top:12px"><span class="chip ${setupReport.overall === 'green' ? 'ok' : setupReport.overall === 'yellow' ? 'warn' : 'bad'}">${{ green: 'সব সবুজ', yellow: 'চলবে, কিছু সতর্কতা', red: 'সমস্যা আছে' }[setupReport.overall]}</span>
        <div style="margin-top:8px">${setupReport.checks.map(check).join('')}</div></div>` : ''}
    </section>
    <section class="card"><h2>ভয়েস টেস্ট</h2>
      <p class="muted">৩টি মহিলা ও ৩টি পুরুষ ভয়েস শুনে বেছে নিন। প্রায় ১–২ মিনিট লাগে।</p>
      <button class="btn primary" data-act="voice-test" ${busy ? 'disabled' : ''}>ভয়েস টেস্ট চালান</button>
      ${voiceReport ? `<div style="margin-top:12px"><p class="muted">এখন: প্রশ্ন = ${esc(voiceReport.current.question)}, উত্তর = ${esc(voiceReport.current.answer)}. পছন্দের নাম Variables-এ বসান।</p>
        ${voiceReport.samples.map((s) => `<div class="voice"><strong style="min-width:150px">${s.engine === 'groq' ? 'Groq' : 'edge-tts'} · ${esc(s.voice)} (${s.gender === 'female' ? 'মহিলা' : 'পুরুষ'})</strong>
          ${s.ok ? `<audio controls preload="none" src="${esc(s.url)}"></audio>` : `<span class="muted">হয়নি: ${esc(s.error || '')}</span>`}</div>`).join('')}</div>` : ''}
    </section>
    <section class="card"><h2>Pixabay সম্মান</h2>
      <p class="muted">সব ছবি <a href="https://pixabay.com" target="_blank" rel="noopener">Pixabay</a> থেকে। প্রতিটি ভিডিওর Release নোটে ফটোগ্রাফারের নাম আছে।</p></section>`;
  }

  async function checkTokenAction() {
    const c = needClient(); if (!c || busy) return;
    setBusy('Token পরীক্ষা হচ্ছে…');
    try { tokenTest = await c.checkToken(); notice = null; } catch (e) { notice = errFromGH(e); }
    clearBusy();
  }
  async function reportAction(file, title, path, label, setter) {
    const c = needClient(); if (!c || busy) return;
    try {
      const run = await runWorkflow(file, {}, title, label);
      const rep = await c.readStateJson(path).catch(() => null);
      if (rep) setter(rep);
      else notice = { kind: 'bad', title: 'রিপোর্ট পাওয়া যায়নি।', text: 'Actions পেজে গিয়ে লগ দেখুন।', link: run && run.html_url };
      if (run && run.conclusion !== 'success' && rep && rep.overall === 'red') notice = { kind: 'bad', title: 'কিছু জিনিস ঠিক নেই।', text: 'নিচের লাল অংশ ঠিক করে আবার চালান।', link: run.html_url };
    } catch (e) { notice = errFromGH(e); }
    clearBusy();
  }

  /* ---------------- events ---------------- */
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const act = b.dataset.act, ci = b.dataset.ci != null ? +b.dataset.ci : null;
    const H = {
      prepare: doPrepare,
      reload: () => openSession(cur.sid),
      pick: () => pickPhoto(ci, b.dataset.key),
      edit: () => { cur.ui.editing = ci; saveUI(); render(true); },
      skip: () => skipWord(ci),
      more: () => moreImages(ci),
      build: doBuild,
      'save-settings': () => {
        const t = $('#tok').value.trim(), r = $('#repo').value.trim().replace(/^https?:\/\/github\.com\//, '').replace(/\/$/, '');
        store.set('wc.token', t); if (r) store.set('wc.repo', r); else store.del('wc.repo');
        gh = null; tokenTest = null;
        notice = { kind: '', title: 'সংরক্ষণ হয়েছে।', text: 'এবার “Token পরীক্ষা করুন” চাপুন।' }; render(true);
      },
      'clear-token': () => { store.del('wc.token'); gh = null; tokenTest = null; notice = { kind: '', title: 'Token মুছে ফেলা হয়েছে।', text: '' }; render(true); },
      'test-token': checkTokenAction,
      'check-setup': () => reportAction('check_setup.yml', 'Check setup', 'reports/check_setup.json', 'Setup পরীক্ষা হচ্ছে…', (r) => { setupReport = r; notice = null; }),
      'voice-test': () => reportAction('voice_test.yml', 'Voice test', 'reports/voice_test.json', 'ভয়েস বানানো হচ্ছে…', (r) => { voiceReport = r; notice = null; }),
    };
    if (H[act]) { e.preventDefault(); Promise.resolve(H[act]()).then(() => render(true)); }
  });
  document.addEventListener('change', (e) => {
    const f = e.target.closest('[data-up]');
    if (f) { const file = f.files && f.files[0]; f.value = ''; uploadOwn(+f.dataset.up, file).then(() => render(true)); return; }
    if (e.target.id === 'theme') { store.set('wc.theme', e.target.value); }
  });

  if ('serviceWorker' in navigator && location.protocol.startsWith('http')) {
    navigator.serviceWorker.register('sw.js').catch(() => { /* optional */ });
  }
  enter();
})();
