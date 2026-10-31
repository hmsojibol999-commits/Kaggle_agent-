/* WordClip Studio - core logic (no DOM). Loaded by index.html, tested in Node. */
(function (root) {
  'use strict';

  /* ---------- geometry: must mirror engine/geometry.py + engine/config.py ---------- */
  const VIDEO = { W: 1080, H: 1920 };
  const CARD = { X: 90, Y: 330, W: 900, H: 1000 };
  const SAFE = { x0: 60, x1: 960, y0: 160, y1: 1560 };
  const KB_END = 1.12;

  const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
  const coverScale = (iw, ih) => Math.max(CARD.W / iw, CARD.H / ih);
  const needsUpscale = (iw, ih, zoom = 1) => coverScale(iw, ih) * zoom > 1 + 1e-9;

  function baseBox(iw, ih, off = [0.5, 0.5]) {
    const s = coverScale(iw, ih);
    const bw = CARD.W / s, bh = CARD.H / s;
    const x0 = (iw - bw) * clamp(off[0], 0, 1);
    const y0 = (ih - bh) * clamp(off[1], 0, 1);
    return [x0, y0, x0 + bw, y0 + bh];
  }
  /* card fractions (0..1) -> image fractions */
  function viewToImage(iw, ih, off, px, py) {
    const [x0, y0, x1, y1] = baseBox(iw, ih, off);
    return [clamp((x0 + px * (x1 - x0)) / iw, 0, 1), clamp((y0 + py * (y1 - y0)) / ih, 0, 1)];
  }
  /* image fractions -> card fractions (may fall outside 0..1 if cropped away) */
  function imageToView(iw, ih, off, fx, fy) {
    const [x0, y0, x1, y1] = baseBox(iw, ih, off);
    return [(fx * iw - x0) / (x1 - x0), (fy * ih - y0) / (y1 - y0)];
  }
  const round3 = (v) => Math.round(v * 1000) / 1000;

  /* ---------- ids / repo ---------- */
  function newSessionId(now = new Date(), rnd = Math.random) {
    const p = (n, l = 2) => String(n).padStart(l, '0');
    const d = `${now.getUTCFullYear()}${p(now.getUTCMonth() + 1)}${p(now.getUTCDate())}`;
    const t = `${p(now.getUTCHours())}${p(now.getUTCMinutes())}${p(now.getUTCSeconds())}`;
    const r = Math.floor(rnd() * 36 ** 4).toString(36).padStart(4, '0');
    return `${d}-${t}-${r}`;
  }
  const SID_RE = /^[A-Za-z0-9_-]{6,48}$/;

  /* github pages: https://owner.github.io/repo/ -> owner/repo */
  function detectRepo(loc) {
    const m = /^([a-z0-9-]+)\.github\.io$/i.exec(loc.hostname || '');
    if (!m) return '';
    const seg = (loc.pathname || '/').split('/').filter(Boolean)[0];
    return seg ? `${m[1]}/${seg}` : `${m[1]}/${m[1]}.github.io`;
  }

  /* ---------- selection sent to build.yml ---------- */
  function buildSelection({ sid, theme = 'auto', words, skipped = [] }) {
    const w = words.map((x) => {
      const o = { w: x.word };
      if (x.pick.upload) o.u = x.pick.upload; else o.p = x.pick.photoId;
      if (x.pick.focus) o.f = [round3(x.pick.focus[0]), round3(x.pick.focus[1])];   // no f = no arrow
      const off = x.pick.offset || [0.5, 0.5];
      if (Math.abs(off[0] - 0.5) > 1e-3 || Math.abs(off[1] - 0.5) > 1e-3) o.o = [round3(off[0]), round3(off[1])];
      return o;
    });
    const out = { v: 1, sid, theme, w };
    if (skipped.length) out.x = skipped.slice(0, 30);
    return JSON.stringify(out);
  }

  /* ---------- Bengali messages ---------- */
  const ERROR_MESSAGES = {
    groq_no_key: ['AI-এর চাবি (Cloudflare) নেই বা ভুল।', 'Settings → Secrets-এ CLOUDFLARE_API_TOKEN ও CLOUDFLARE_ACCOUNT_ID ঠিক করে আবার চেষ্টা করুন।'],
    groq_rate_limit: ['AI-এর ফ্রি সীমা আপাতত শেষ।', 'কয়েক মিনিট পরে আবার চেষ্টা করুন।'],
    groq_model_missing: ['মডেল পাওয়া যায়নি।', 'Settings থেকে “Setup পরীক্ষা” চালান, তালিকা দেখে Variables-এ মডেলের নাম ঠিক করুন।'],
    groq_terms: ['AI মডেলের শর্ত এখনো মানা হয়নি।', 'ভিডিও তবুও হবে, তবে edge-tts ভয়েসে।'],
    groq_bad_response: ['AI ঠিক উত্তর দেয়নি।', 'আবার Prepare চাপুন। শব্দ তখন আমাদের বাছাই করা তালিকা থেকে আসবে।'],
    pixabay_no_key: ['Pixabay key নেই বা ভুল।', 'Settings → Secrets-এ PIXABAY_API_KEY ঠিক করুন।'],
    pixabay_rate_limit: ['Pixabay সীমা শেষ।', 'প্রায় এক মিনিট পরে আবার চেষ্টা করুন।'],
    pixabay_empty: ['যথেষ্ট ভালো ছবি পাওয়া যায়নি।', 'অন্য ক্যাটাগরি দিয়ে আবার Prepare চাপুন।'],
    selection_bad: ['বাছাই করা তথ্যে ভুল আছে।', 'পাতা রিফ্রেশ করে আবার বাছাই করুন।'],
    image_missing: ['ছবি নামানো যায়নি।', 'অন্য ছবি বাছুন অথবা “নিজের ছবি” দিন।'],
    image_low_res: ['ছবির মান খুব কম।', 'বড় মাপের অন্য ছবি বাছুন।'],
    image_mismatch: ['বাছা ছবি শব্দের সঙ্গে মিলছে না (নিচের বিবরণ দেখুন)।', 'ওই শব্দের অন্য ছবি বাছুন, না মিললে “বাদ দিন” চাপুন।'],
    tts_failed: ['ভয়েস তৈরি হয়নি (সব ভয়েস ইঞ্জিন ব্যর্থ)।', 'কিছুক্ষণ পরে আবার Build করুন।'],
    render_failed: ['ভিডিও রেন্ডার ব্যর্থ হয়েছে।', 'Actions লগ দেখুন (নিচের লিঙ্ক), তারপর আবার চেষ্টা করুন।'],
    quality_gate_failed: ['ভিডিওর মান-পরীক্ষায় আটকে গেছে।', 'Actions লগ দেখুন (নিচের লিঙ্ক)। ছবি বা শব্দ বদলে আবার Build করুন।'],
    state_branch_failed: ['state ব্রাঞ্চে লেখা যায়নি।', 'Token-এ Contents: Read and write আছে কি দেখুন, আর একসঙ্গে দুটো কাজ চালাবেন না।'],
    release_failed: ['Release তৈরি হয়নি।', 'Repo Settings → Actions → General → Workflow permissions-এ “Read and write” দিন।'],
    unknown: ['অজানা সমস্যা হয়েছে।', 'Actions লগ দেখুন (নিচের লিঙ্ক)।'],
  };
  function mapError(code) { return ERROR_MESSAGES[code] || ERROR_MESSAGES.unknown; }

  function httpMessage(status) {
    if (status === 401) return ['Token ভুল বা মেয়াদ শেষ।', 'Settings-এ নতুন token বসান।'];
    if (status === 403) return ['Token-এর অনুমতি কম।', 'Token-এ Actions ও Contents (Read and write) অনুমতি দিন।'];
    if (status === 404) return ['Repo বা ফাইল পাওয়া যায়নি।', 'Settings-এ repo-এর নাম (owner/repo) দেখুন; token অবশ্যই এই repo-র জন্য হতে হবে।'];
    if (status === 409 || status === 422) return ['GitHub কাজটি নিতে পারেনি।', 'একটু পরে আবার চেষ্টা করুন।'];
    return ['GitHub-এর সঙ্গে যোগাযোগ হয়নি।', 'ইন্টারনেট দেখে আবার চেষ্টা করুন।'];
  }

  /* ---------- base64 helpers ---------- */
  function decodeB64Utf8(b64) {
    const bin = atob(b64.replace(/\s/g, ''));
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new TextDecoder('utf-8').decode(bytes);
  }

  /* ---------- GitHub API client ---------- */
  class GHError extends Error {
    constructor(status, message, body) { super(message); this.status = status; this.body = body; }
  }

  class GitHub {
    constructor({ token, repo, fetchFn, branch }) {
      this.token = token; this.repo = repo; this.f = fetchFn || (typeof fetch !== 'undefined' ? fetch.bind(root) : null);
      this.branch = branch || ''; this.base = 'https://api.github.com';
    }
    async req(path, opts = {}) {
      const headers = { Accept: 'application/vnd.github+json', Authorization: `Bearer ${this.token}` };
      if (opts.body) headers['Content-Type'] = 'application/json';
      let res;
      try {
        res = await this.f(this.base + path, { method: opts.method || 'GET', headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
      } catch (e) { throw new GHError(0, 'network', String(e)); }
      if (res.status === 204) return null;
      let data = null;
      try { data = await res.json(); } catch (e) { /* empty */ }
      if (!res.ok) throw new GHError(res.status, (data && data.message) || `HTTP ${res.status}`, data);
      return data;
    }
    async defaultBranch() {
      if (this.branch) return this.branch;
      const r = await this.req(`/repos/${this.repo}`);
      this.branch = r.default_branch || 'main';
      return this.branch;
    }
    async lastRunId(file) {
      const r = await this.req(`/repos/${this.repo}/actions/workflows/${file}/runs?event=workflow_dispatch&per_page=5`);
      return (r.workflow_runs || []).reduce((m, x) => Math.max(m, x.id), 0);
    }
    /* dispatch and return the new run (found by id > lastRunId and matching title) */
    async dispatch(file, inputs = {}, titleHas = '', { tries = 15, waitMs = 2000, sleep } = {}) {
      const sl = sleep || ((ms) => new Promise((r) => setTimeout(r, ms)));
      const ref = await this.defaultBranch();
      const before = await this.lastRunId(file);
      await this.req(`/repos/${this.repo}/actions/workflows/${file}/dispatches`, { method: 'POST', body: { ref, inputs } });
      for (let i = 0; i < tries; i++) {
        await sl(waitMs);
        const r = await this.req(`/repos/${this.repo}/actions/workflows/${file}/runs?event=workflow_dispatch&per_page=10`);
        const hit = (r.workflow_runs || [])
          .filter((x) => x.id > before && (!titleHas || (x.display_title || x.name || '').includes(titleHas)))
          .sort((a, b) => a.id - b.id)[0];
        if (hit) return hit;
      }
      throw new GHError(0, 'run-not-found', 'The workflow was dispatched but no run appeared');
    }
    async getRun(id) { return this.req(`/repos/${this.repo}/actions/runs/${id}`); }
    async waitRun(id, onTick, { intervalMs = 4000, timeoutMs = 45 * 60 * 1000, sleep } = {}) {
      const sl = sleep || ((ms) => new Promise((r) => setTimeout(r, ms)));
      const t0 = Date.now();
      for (;;) {
        const run = await this.getRun(id);
        if (onTick) onTick(run, Date.now() - t0);
        if (run.status === 'completed') return run;
        if (Date.now() - t0 > timeoutMs) throw new GHError(0, 'timeout', 'run took too long');
        await sl(intervalMs);
      }
    }
    async readState(path) {
      try {
        const r = await this.req(`/repos/${this.repo}/contents/${path}?ref=state&_=${Date.now()}`);
        if (Array.isArray(r)) return r;
        return decodeB64Utf8(r.content || '');
      } catch (e) { if (e.status === 404) return null; throw e; }
    }
    async readStateJson(path) {
      const t = await this.readState(path);
      if (t == null) return null;
      return Array.isArray(t) ? t : JSON.parse(t);
    }
    async putState(path, base64, message, tries = 3) {
      let last;
      for (let i = 0; i < tries; i++) {
        try {
          return await this.req(`/repos/${this.repo}/contents/${path}`, { method: 'PUT', body: { message, content: base64, branch: 'state' } });
        } catch (e) { last = e; if (e.status !== 409 && e.status !== 422) throw e; await new Promise((r) => setTimeout(r, 800 * (i + 1))); }
      }
      throw last;
    }
    async releaseByTag(tag) { return this.req(`/repos/${this.repo}/releases/tags/${encodeURIComponent(tag)}`); }
    async checkToken() {
      const out = [];
      const step = async (label, fn, hint) => {
        try { await fn(); out.push({ label, ok: true }); }
        catch (e) { out.push({ label, ok: false, hint: hint || httpMessage(e.status || 0).join(' '), status: e.status }); }
      };
      await step('Repo খোঁজা', () => this.req(`/repos/${this.repo}`));
      await step('Actions (workflow তালিকা)', () => this.req(`/repos/${this.repo}/actions/workflows`),
        'Token-এ Actions: Read and write অনুমতি দিন।');
      await step('Contents (ফাইল পড়া)', () => this.req(`/repos/${this.repo}/contents/`),
        'Token-এ Contents: Read and write অনুমতি দিন।');
      return out;
    }
  }

  const api = { VIDEO, CARD, SAFE, KB_END, clamp, coverScale, needsUpscale, baseBox, viewToImage, imageToView,
    newSessionId, SID_RE, detectRepo, buildSelection, ERROR_MESSAGES, mapError, httpMessage, decodeB64Utf8,
    GitHub, GHError, round3 };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.WC = api;
})(typeof window !== 'undefined' ? window : globalThis);
