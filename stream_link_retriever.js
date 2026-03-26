import puppeteer from 'puppeteer';
import fs from 'fs';
import process from 'process';

const getStreamUrl = async () => {
    // FIX: Added autoplay bypass, window size, and standard user-agent for GitHub Actions
    const browser = await puppeteer.launch({ 
        args: [
            '--no-sandbox', 
            '--disable-setuid-sandbox',
            '--autoplay-policy=no-user-gesture-required',
            '--window-size=1920,1080'
        ] 
    });
    const page = await browser.newPage();
    
    // Mask the headless browser so it looks like a normal Chrome user
    await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36');

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
            // FIX: Increased timeouts to 8 seconds to account for slower GitHub server connections
            await page.waitForSelector(playButtonSelector, { timeout: 8000 });
            await page.click(playButtonSelector);
            console.log('Play button clicked! Waiting for audio connection...');

            await new Promise(resolve => setTimeout(resolve, 8000));
        } catch (error) {
            console.log('No Play button needed (it might already be playing).');
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