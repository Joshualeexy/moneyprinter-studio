const path = require('path');
require('dotenv').config({ path: path.resolve(__dirname, '.env') });
require('dotenv').config({ path: path.resolve(__dirname, '../../.env') });
require('dotenv').config();

const express = require('express');
const cors = require('cors');
const { launchStealthBrowser } = require('./lib/browser');

const app = express();
const PORT = process.env.SCRAPER_PORT || 4050;

app.use(cors());
app.use(express.json());

// Global persistent browser pool
let persistentBrowser = null;
let isInitializing = false;

/**
 * Initialize or retrieve the persistent stealth browser.
 */
async function getBrowser() {
    if (persistentBrowser) return persistentBrowser;
    if (isInitializing) {
        while (isInitializing) {
            await new Promise(r => setTimeout(r, 200));
        }
        return persistentBrowser;
    }

    isInitializing = true;
    try {
        console.log('[Scraper] Initializing persistent stealth browser pool with CapSolver protection...');
        persistentBrowser = await launchStealthBrowser({
            headless: true, // uses --headless=new for extensions
            userDataDir: path.resolve(__dirname, 'google_profile'),
            capsolverApiKey: process.env.CAPSOLVER_API_KEY || ''
        });
        console.log('[Scraper] Persistent stealth browser pool is ready!');
        return persistentBrowser;
    } catch (err) {
        console.error('[Scraper] Failed to launch browser pool:', err);
        throw err;
    } finally {
        isInitializing = false;
    }
}

// ── Health Check ─────────────────────────────────────────────────────────────
app.get('/health', (req, res) => {
    res.json({
        status: 'ok',
        browserReady: !!persistentBrowser,
        port: PORT
    });
});

// ── Search Intelligence Endpoint ─────────────────────────────────────────────
app.get('/api/search', async (req, res) => {
    const query = req.query.q || req.query.query;
    const limit = parseInt(req.query.limit || '5', 10);

    if (!query) {
        return res.status(400).json({ error: 'Missing required query parameter "q"' });
    }

    let page = null;
    try {
        const browser = await getBrowser();
        page = await browser.newPage();

        const searchUrl = `https://www.google.com/search?q=${encodeURIComponent(query)}&hl=en&num=10`;
        console.log(`[Scraper] Querying intelligence: "${query}" (limit: ${limit})`);

        await page.goto(searchUrl, { waitUntil: 'domcontentloaded', timeout: 25000 });

        // Auto-dismiss GDPR cookie banner if presented
        try {
            const consentSelectors = [
                'button:has-text("Accept all")',
                'button:has-text("I agree")',
                '#L2AGLb',
                'button:has-text("Alle akzeptieren")',
                'button:has-text("Tout accepter")'
            ];
            for (const sel of consentSelectors) {
                const btn = await page.$(sel);
                if (btn) {
                    await btn.click().catch(() => null);
                    await page.waitForTimeout(500);
                    break;
                }
            }
        } catch (_) {}

        // Auto-resolve verification checkpoint if encountered
        if (page.url().includes('sorry/index')) {
            console.log('[Scraper] Verifying access checkpoint with CapSolver...');
            await page.waitForTimeout(2000);
            const anchorFrame = page.frames().find(f => f.url().includes('/recaptcha/enterprise/anchor') || f.url().includes('/recaptcha/api2/anchor'));
            if (anchorFrame) {
                const anchor = await anchorFrame.$('#recaptcha-anchor');
                if (anchor) {
                    await anchor.click().catch(() => null);
                }
            }
            await page.waitForURL(url => !url.href.includes('sorry/index'), { timeout: 15000 }).catch(() => null);
        }

        // Wait for results container
        await page.waitForSelector('#search, h3, .g', { timeout: 8000 }).catch(() => null);

        // Extract high-authority search intelligence
        const extraction = await page.evaluate((maxResults) => {
            // 1. Featured Snippet / Direct Answer
            let answerBox = '';
            const answerEl = document.querySelector('.hgKElc, [data-attrid="wa:/description"], .kp-header, .Z0LcW, .LGOjdf');
            if (answerEl) {
                answerBox = answerEl.innerText.trim();
            }

            // 2. Organic Search Results (hybrid multi-selector parsing)
            const organic = [];
            const h3s = Array.from(document.querySelectorAll('h3'));

            for (const h of h3s) {
                const title = h.innerText.trim();
                if (!title) continue;

                let container = h.closest('.g, [data-hveid]');
                let url = '';
                let snippet = '';

                if (container) {
                    const a = container.querySelector('a[href^="http"]');
                    if (a) url = a.href;

                    const snipEl = container.querySelector('.VwiC3b, .yXK7lf, .MUxGbd, [style*="-webkit-line-clamp"]');
                    if (snipEl) {
                        snippet = snipEl.innerText.trim();
                    } else {
                        const allText = container.innerText;
                        const lines = allText.split('\n').map(l => l.trim()).filter(l => l.length > 25 && l !== title);
                        if (lines.length > 0) snippet = lines[0];
                    }
                } else {
                    const a = h.closest('a') || (h.parentElement ? h.parentElement.querySelector('a') : null);
                    if (a) url = a.href;
                    let parent = h.parentElement;
                    for (let i = 0; i < 5 && parent; i++) {
                        const text = parent.innerText;
                        if (text && text.length > title.length + 30) {
                            snippet = text.replace(title, '').trim().slice(0, 200);
                            break;
                        }
                        parent = parent.parentElement;
                    }
                }

                if (title && url && !url.includes('google.com/search')) {
                    if (!organic.some(r => r.title === title)) {
                        organic.push({ title, snippet, url });
                    }
                }

                if (organic.length >= maxResults) break;
            }

            // 3. People Also Ask questions
            const paa = [];
            const paaEls = document.querySelectorAll('.related-question-pair, div[data-q]');
            for (const el of paaEls) {
                const qText = el.innerText ? el.innerText.split('\n')[0].trim() : '';
                if (qText && qText.endsWith('?')) {
                    paa.push(qText);
                }
                if (paa.length >= 4) break;
            }

            return {
                answer_box: answerBox,
                results: organic,
                people_also_ask: paa
            };
        }, limit);

        console.log(`[Scraper] Retrieved ${extraction.results.length} results, ${extraction.people_also_ask.length} PAA (Answer Box: ${extraction.answer_box ? 'YES' : 'NO'})`);

        res.json({
            status: 'ok',
            query,
            answer_box: extraction.answer_box,
            results: extraction.results,
            people_also_ask: extraction.people_also_ask
        });

    } catch (err) {
        console.error(`[Scraper] Search failed for "${query}":`, err.message);
        res.status(500).json({
            status: 'error',
            query,
            error: err.message,
            results: []
        });
    } finally {
        if (page) {
            await page.close().catch(() => null);
        }
    }
});

// ── Server Bootstrap ─────────────────────────────────────────────────────────
app.listen(PORT, '127.0.0.1', async () => {
    console.log(`[Scraper API] Stealth search microservice listening on http://127.0.0.1:${PORT}`);
    try {
        await getBrowser();
    } catch (err) {
        console.warn(`[Scraper API] Background browser pre-warm warning: ${err.message}`);
    }
});

// Graceful shutdown
process.on('SIGTERM', async () => {
    console.log('[Scraper API] Shutting down...');
    if (persistentBrowser) {
        await persistentBrowser.close().catch(() => null);
    }
    process.exit(0);
});
process.on('SIGINT', async () => {
    console.log('[Scraper API] Interrupted...');
    if (persistentBrowser) {
        await persistentBrowser.close().catch(() => null);
    }
    process.exit(0);
});
