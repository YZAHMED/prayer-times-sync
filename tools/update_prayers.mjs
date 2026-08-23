// Fetch the mosque's timetable and publish it as prayers.json.
//
// Hardened over the original: retries with backoff, request timeouts, schema
// validation, and a date check — because the failure that matters is not an
// error, it is silently publishing yesterday's times or an empty payload and
// leaving every device to act on it.
//
// Providers are pluggable so a new mosque can be added by writing a preset in
// mosques/, without touching this file.

import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const OUT = path.join(ROOT, 'prayers.json');
const TIMEOUT_MS = 25_000;
const RETRIES = 4;

const readJson = (p, d = null) => { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch { return d; } };

// Redact secrets before anything reaches the CI log.
const scrub = (s, secrets) => secrets.filter(Boolean).reduce((acc, v) => acc.split(v).join('***'), String(s));

function ymdIn(tz, offsetDays = 0) {
  const d = new Date(Date.now() + offsetDays * 86_400_000);
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(d);
  const get = t => parts.find(p => p.type === t).value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}

function hmsIn(tz) {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: tz, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(new Date());
}

async function getJson(url, headers, secrets) {
  let lastErr;
  for (let attempt = 1; attempt <= RETRIES; attempt++) {
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), TIMEOUT_MS);
    try {
      const res = await fetch(url, { signal: ac.signal, headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      lastErr = err;
      console.warn(`  attempt ${attempt}/${RETRIES} failed: ${scrub(err.message, secrets)}`);
      if (attempt < RETRIES) await new Promise(r => setTimeout(r, attempt * 3000));
    } finally { clearTimeout(timer); }
  }
  throw new Error(`giving up: ${scrub(lastErr?.message ?? 'unknown', secrets)}`);
}

// --- providers -------------------------------------------------------------

async function fetchMasjidal({ tz }) {
  const apiKey = process.env.PRAYER_API_KEY;
  const baseUrl = process.env.PRAYER_API_BASE_URL;
  if (!apiKey || !baseUrl) throw new Error('PRAYER_API_KEY / PRAYER_API_BASE_URL are not set');
  const secrets = [apiKey, baseUrl];
  const url = `${baseUrl}&day=${ymdIn(tz)}&time=${hmsIn(tz)}`;
  return {
    payload: await getJson(url, {
      accept: '*/*',
      'addin-api-key': apiKey,
      'user-agent': 'prayer-times-sync/2',
    }, secrets),
    secrets,
  };
}

// Public fallback provider: no key, works for any coordinates on earth, so a
// new mosque can be added without an account anywhere.
async function fetchAladhan({ tz, preset }) {
  const { latitude, longitude } = preset.location ?? {};
  if (latitude == null || longitude == null) throw new Error('aladhan provider needs location.latitude/longitude');
  const methodMap = { MWL: 3, ISNA: 2, EGYPT: 5, MAKKAH: 4, KARACHI: 1, TEHRAN: 7, JAFARI: 0 };
  const method = methodMap[(preset.calculation?.method ?? 'ISNA').toUpperCase()] ?? 2;
  const school = (preset.calculation?.asr ?? 'standard').toLowerCase() === 'hanafi' ? 1 : 0;
  const [y, m, d] = ymdIn(tz).split('-');
  const url = `https://api.aladhan.com/v1/timings/${d}-${m}-${y}`
    + `?latitude=${latitude}&longitude=${longitude}&method=${method}&school=${school}`;
  const res = await getJson(url, { accept: 'application/json' }, []);
  const t = res?.data?.timings;
  if (!t) throw new Error('aladhan returned no timings');
  const hm = v => `${String(v).slice(0, 5)}:00`;
  // Reshape into the same envelope the devices already understand.
  return {
    payload: {
      data: {
        name: preset.name ?? preset.id,
        city: preset.city ?? '',
        prayers: null,
        prayerOfDay: {
          prayerDate: `${ymdIn(tz)}T00:00:00`,
          singlePrayers: [
            { prayerName: 'Fajr',    prayerBegins: hm(t.Fajr),    prayerAdhan: hm(t.Fajr),    prayerIqamah: null },
            { prayerName: 'Sunrise', prayerBegins: null,          prayerAdhan: hm(t.Sunrise), prayerIqamah: null },
            { prayerName: 'Dhuhr',   prayerBegins: hm(t.Dhuhr),   prayerAdhan: hm(t.Dhuhr),   prayerIqamah: null },
            { prayerName: 'Asr',     prayerBegins: hm(t.Asr),     prayerAdhan: hm(t.Asr),     prayerIqamah: null },
            { prayerName: 'Sunset',  prayerBegins: null,          prayerAdhan: hm(t.Sunset),  prayerIqamah: null },
            { prayerName: 'Maghrib', prayerBegins: hm(t.Maghrib), prayerAdhan: hm(t.Maghrib), prayerIqamah: null },
            { prayerName: 'Isha',    prayerBegins: hm(t.Isha),    prayerAdhan: hm(t.Isha),    prayerIqamah: null },
          ],
        },
      },
      status: 'ALADHAN',
      message: 'Success',
      source: 'aladhan',
    },
    secrets: [],
  };
}

// --- validation ------------------------------------------------------------

const REQUIRED = ['Fajr', 'Dhuhr', 'Asr', 'Maghrib', 'Isha'];

function validate(payload, tz) {
  const day = payload?.data?.prayerOfDay;
  if (!day) throw new Error('payload has no data.prayerOfDay');

  const list = day.singlePrayers;
  if (!Array.isArray(list) || list.length === 0) throw new Error('singlePrayers is empty');

  const date = String(day.prayerDate ?? '').slice(0, 10);
  const allowed = [ymdIn(tz, -1), ymdIn(tz), ymdIn(tz, 1)];
  if (!allowed.includes(date)) {
    throw new Error(`timetable is for ${date || '(none)'}, expected one of ${allowed.join(', ')}`);
  }
  if (date !== ymdIn(tz)) console.warn(`  note: payload date ${date} is not today (${ymdIn(tz)})`);

  const byName = new Map(list.map(p => [p.prayerName, p]));
  const missing = REQUIRED.filter(n => !byName.has(n));
  if (missing.length) throw new Error(`missing prayers: ${missing.join(', ')}`);

  const timeRe = /^\d{1,2}:\d{2}(:\d{2})?$/;
  for (const n of REQUIRED) {
    const p = byName.get(n);
    const anchors = [p.prayerAdhan, p.prayerBegins, p.prayerIqamah].filter(v => v && timeRe.test(v));
    if (anchors.length === 0) throw new Error(`${n} has no usable time (adhan/begins/iqamah all absent or malformed)`);
  }
  return { date, count: list.length };
}

// --- main ------------------------------------------------------------------

async function main() {
  const config = readJson(path.join(ROOT, 'config.json'), {}) ?? {};
  const mosqueId = process.env.PRAYER_MOSQUE || config.mosque || 'masjid-el-noor';
  const preset = readJson(path.join(ROOT, 'mosques', `${mosqueId}.json`), {}) ?? {};
  const tz = config.timezone ?? preset.timezone ?? 'UTC';
  const provider = preset.timetable?.provider ?? 'masjidal';

  console.log(`mosque=${mosqueId} tz=${tz} provider=${provider} today=${ymdIn(tz)}`);

  const providers = { masjidal: fetchMasjidal, aladhan: fetchAladhan };
  const fn = providers[provider];
  if (!fn) throw new Error(`unknown timetable provider '${provider}'`);

  const { payload, secrets } = await fn({ tz, preset });
  const info = validate(payload, tz);
  console.log(`validated: ${info.count} entries for ${info.date}`);

  const next = JSON.stringify(payload, null, 2) + '\n';
  const prev = fs.existsSync(OUT) ? fs.readFileSync(OUT, 'utf8') : '';
  if (prev === next) {
    console.log('unchanged');
    return;
  }
  // Write via a temp file so an interrupted run cannot leave a truncated
  // prayers.json for every device to download.
  const tmp = `${OUT}.tmp`;
  fs.writeFileSync(tmp, next);
  fs.renameSync(tmp, OUT);
  console.log(`wrote prayers.json for ${info.date}`);
  void scrub;
  void secrets;
}

main().catch(err => { console.error('fatal:', err.message); process.exit(1); });
