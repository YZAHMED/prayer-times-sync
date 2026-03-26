import puppeteer from 'puppeteer';
import fs from 'fs';
import process from 'process';
import { resolve } from 'path';

const getStreamUrl = async () => {
    const browser = await puppeteer.launch({ args: ['--no-sandbox', '--disable-setuid-sandbox'] });
    const page = await browser.newPage();
    let streamUrl = null;

    // 1. Listen for network requests to capture the stream URL
    page.on('request', request => {
        const url = request.url();
        if (url.includes("listen.mixlr.com")){
            streamUrl = url;
        }
    });



    console.log("Navigating to Mixlr stream page...");
    await page.goto('https://masjid-el-noor.mixlr.com/events/4930085', { waitUntil: 'networkidle2' });

    // 2. If it didn't play automatically, find and click the play button

    if (!streamUrl) {
        console.log('Stream did not auto-play. Looking for the Play button...');
        const playButtonSelector = 'button[aria-label="Play audio"]';

        try {
            await page.waitForSelector(playButtonSelector, { timeout: 5000 });
            await page.click(playButtonSelector);
            console.log('Play button clicked! Waiting for audio connection...');

            // Give the network 4 seconds to fetch the stream
            await new Promise(resolve => setTimeout(resolve, 4000));
        } catch (error) {
            console.log('No Play button needed (it might already be playing). Error: ' + error);
        }
    }

    if (streamUrl) {
        console.log(`Success! Stream URL: ${streamUrl}`);
        fs.writeFileSync('stream_url.txt', streamUrl);

    } else {
        console.error('Failed to intercept stream URL.');
        process.exit(1);
    }

    await browser.close();
};

getStreamUrl();