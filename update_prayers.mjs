// update_prayers.mjs
import fs from 'fs';

const apiKey = process.env.PRAYER_API_KEY;
const baseUrl = process.env.PRAYER_API_BASE_URL;

// Format dates for Toronto time
const now = new Date();
const dateOptions = { timeZone: 'America/Toronto', year: 'numeric', month: '2-digit', day: '2-digit' };
const timeOptions = { timeZone: 'America/Toronto', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false };

const formatter = new Intl.DateTimeFormat('en-CA', dateOptions);
const parts = formatter.formatToParts(now);
const today = `${parts.find(p => p.type === 'year').value}-${parts.find(p => p.type === 'month').value}-${parts.find(p => p.type === 'day').value}`;
const time = new Intl.DateTimeFormat('en-CA', timeOptions).format(now);

const url = `${baseUrl}&day=${today}&time=${time}`;

async function updatePrayers() {
  try {
    const response = await fetch(url, {
      headers: {
        'accept': '*/*',
        'addin-api-key': apiKey,
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/'
      }
    });

    if (!response.ok) throw new Error(`Status: ${response.status}`);
    
    const data = await response.json();
    fs.writeFileSync('prayers.json', JSON.stringify(data, null, 2));
    console.log(`Saved timetable for ${today}`);
  } catch (error) {
    console.error('Failed:', error);
    process.exit(1); 
  }
}

updatePrayers();