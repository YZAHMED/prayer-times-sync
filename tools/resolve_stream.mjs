// Resolve the mosque's current live stream URL.
//
// Replaces the old Puppeteer scraper. Mixlr publishes the live broadcast in a
// plain JSON API, so there is no headless browser, no user-agent to spoof, no
// cookie banner, no autoplay policy and no DOM selector to break.
//
// Note this file only refreshes a *fallback*: broadcast ids rotate every time
// the mosque starts a new broadcast, so a URL captured once a day is stale the
// moment they restart. Edge devices resolve the same API at play time; this
// snapshot only exists for devices that are offline when a prayer begins.

import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const OUT = path.join(ROOT, 'stream_url.txt');
const TIMEOUT_MS = 20_000;
const RETRIES = 4;

function readJson(p, fallback = null) {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch { return fallback; }
}

async function getJson(url, { retries = RETRIES } = {}) {
  let lastErr;
  for (let attempt = 1; attempt <= retries; attempt++) {
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), TIMEOUT_MS);
    try {
      const res = await fetch(url, {
        signal: ac.signal,
        headers: { accept: 'application/json', 'user-agent': 'prayer-times-sync/2' },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      lastErr = err;
      if (attempt < retries) await new Promise(r => setTimeout(r, attempt * 2000));
    } finally {
      clearTimeout(timer);
    }
  }
  throw new Error(`${url}: ${lastErr?.message ?? 'unknown error'}`);
}

// Confirm the URL really serves audio before publishing it. A live Icecast
// stream never ends, so judge on the response headers and abort the body.
async function isAudio(url) {
  for (const method of ['HEAD', 'GET']) {
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), 8000);
    try {
      const res = await fetch(url, { method, signal: ac.signal, redirect: 'follow' });
      const ct = (res.headers.get('content-type') || '').toLowerCase();
      if (res.ok && (ct.startsWith('audio/') || ct.includes('ogg') || ct.includes('mpegurl'))) {
        try { await res.body?.cancel(); } catch { /* already closed */ }
        return true;
      }
    } catch { /* try the next method */ } finally { clearTimeout(timer); }
  }
  return false;
}

async function resolveMixlr(slug) {
  const candidates = [];

  try {
    const cv = await getJson(`https://api.mixlr.com/v3/channel_view/${slug}`);
    for (const inc of cv?.included ?? []) {
      const u = inc?.attributes?.progressive_stream_url;
      if (u) candidates.push(u);
    }
    const live = (cv?.included ?? []).some(i => i?.attributes?.live === true);
    console.log(`channel_view: ${candidates.length} candidate(s), live=${live}`);
  } catch (err) {
    console.warn(`channel_view failed: ${err.message}`);
  }

  try {
    const u = await getJson(`https://api.mixlr.com/users/${slug}`);
    for (const id of u?.broadcast_ids ?? []) candidates.push(`https://listen.mixlr.com/${id}`);
    console.log(`users api: is_live=${u?.is_live}`);
  } catch (err) {
    console.warn(`users api failed: ${err.message}`);
  }

  return [...new Set(candidates)];
}

async function main() {
  const config = readJson(path.join(ROOT, 'config.json'), {}) ?? {};
  const mosqueId = process.env.PRAYER_MOSQUE || config.mosque || 'masjid-el-noor';
  const preset = readJson(path.join(ROOT, 'mosques', `${mosqueId}.json`), {}) ?? {};

  const stream = { ...(preset.stream ?? {}), ...(config.stream ?? {}) };
  const provider = stream.provider ?? 'mixlr';
  console.log(`mosque=${mosqueId} provider=${provider}`);

  let candidates = [];
  if (provider === 'mixlr') {
    const slug = stream.mixlr_slug || mosqueId;
    candidates = await resolveMixlr(slug);
  } else if (stream.url) {
    candidates = [stream.url];
  }

  if (candidates.length === 0) {
    console.error('no stream candidates resolved');
    process.exit(1);
  }

  let chosen = null;
  for (const c of candidates) {
    if (await isAudio(c)) { chosen = c; break; }
    console.warn(`rejected (not audio): ${c}`);
  }

  if (!chosen) {
    // Everything failed the audio probe. Keeping the previous value is better
    // than publishing a URL we know is dead.
    const previous = fs.existsSync(OUT) ? fs.readFileSync(OUT, 'utf8').trim() : '';
    console.error(`no candidate served audio; keeping previous value: ${previous || '(none)'}`);
    process.exit(previous ? 0 : 1);
  }

  const previous = fs.existsSync(OUT) ? fs.readFileSync(OUT, 'utf8').trim() : '';
  if (previous === chosen) {
    console.log(`unchanged: ${chosen}`);
  } else {
    fs.writeFileSync(OUT, chosen + '\n');
    console.log(`updated: ${previous || '(none)'} -> ${chosen}`);
  }
}

main().catch(err => { console.error('fatal:', err.message); process.exit(1); });
