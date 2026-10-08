// Self-run desktop/mobile check. Both processes and all writes are isolated fixtures.
const fs = require('fs'), path = require('path'), assert = require('assert/strict')
const {spawn} = require('child_process')
const APP = path.resolve(__dirname, '..')
const EVIDENCE = path.resolve(process.env.EVIDENCE_DIR || path.join(APP, 'evidence/skipping'))
fs.mkdirSync(EVIDENCE, {recursive: true})
const PORT = Number(process.env.VERIFY_PORT || '5399')
assert(Number.isInteger(PORT) && PORT >= 1024 && PORT <= 65535, 'VERIFY_PORT must be a valid unprivileged port')
const OWNER = require('crypto').randomUUID()
const {chromium} = require(path.join(APP, 'ui/node_modules/@playwright/test'))
const BASE = 'http://127.0.0.1:' + PORT
const FIXTURE = BASE
const EPISODE = '3f0c2a8e-5b7d-4c1e-9a6f-0d2e4b6c8a10'
const report = {status: 'RUNNING', self_run: true, data: 'synthetic transcript and silent WAV with Range support; real companion detector and production UI build', checks: [], screenshots: [], errors: []}
const children = []
let browser, currentPage
const check = (name, value) => {assert(value, name); report.checks.push(name)}
const start = (cmd, args, cwd, env, name) => {
    const log = fs.openSync(path.join(EVIDENCE, name + '.log'), 'w')
    const child = spawn(cmd, args, {cwd, env, detached: true, stdio: ['ignore', log, log]})
    child.on('error', error => { child.spawnError = error.message })
    children.push(child); fs.closeSync(log); return child
}
const put = async (path, body) => {const response = await fetch(FIXTURE + path, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}); assert(response.ok, path); return response.json()}
const json = async path => {const response = await fetch(FIXTURE + path); assert(response.ok, path); return response.json()}
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))
;(async () => {
    const env = {PATH: process.env.PATH, HOME: process.env.HOME, LANG: 'C.UTF-8', PYTHONPATH: APP,
        PODFETCH_BACKEND: FIXTURE, PODSIFT_HOSTED: 'false', VERIFY_PORT: String(PORT), VERIFY_OWNER: OWNER,
        EVIDENCE_DIR: EVIDENCE, UI_DIST: path.resolve(process.env.UI_DIST || path.join(APP, 'ui/dist'))}
    start(process.env.APP_PYTHON || 'python3', ['-m', 'uvicorn', 'skipping_fixture:app', '--app-dir', path.join(APP, 'scripts/fixtures'), '--host', '127.0.0.1', '--port', String(PORT)], APP, env, 'fixture')
    const backendDeadline = Date.now() + 60_000
    while (true) {
        try {if ((await fetch(FIXTURE + '/verify/owner')).ok && (await json('/verify/owner')).owner === OWNER) break} catch {}
        assert(Date.now() < backendDeadline && children.every(child => child.exitCode === null && !child.spawnError), 'fixture starts on its own port')
        await wait(300)
    }
    const cache = path.join(process.env.HOME, '.cache/ms-playwright')
    const executablePath = fs.existsSync(chromium.executablePath()) ? undefined : (fs.existsSync(cache) ? fs.readdirSync(cache) : [])
        .filter(name => name.startsWith('chromium_headless_shell-')).sort().reverse()
        .map(name => path.join(cache, name, 'chrome-headless-shell-linux64/chrome-headless-shell')).find(fs.existsSync)
    browser = await chromium.launch({headless: true, executablePath, args: ['--no-sandbox']})
    for (const [device, viewport] of [['desktop', {width: 1366, height: 900}], ['mobile', {width: 390, height: 844}]]) {
        const context = await browser.newContext({viewport, serviceWorkers: 'block'})
        await context.route('**/health', route => route.fulfill({json: {mode: 'self-hosted'}}))
        const page = await context.newPage()
        currentPage = page
        page.setDefaultTimeout(20_000)
        page.on('pageerror', error => report.errors.push(error.message))
        await page.goto(BASE + '/ui/learn?episode=' + EPISODE + '&at=0', {waitUntil: 'domcontentloaded'})
        await page.getByRole('button', {name: 'Play from 0:00', exact: true}).click()
        const toggle = page.getByRole('button', {name: 'Skip sponsors', exact: true})
        await page.waitForFunction(() => document.querySelector('button[aria-label="Skip sponsors"]')?.title.includes('2 sponsor parts'))
        check(device + ': native ranges appear in the player', await page.locator('[data-range="sponsor"]').count() === 2)
        const detected = await json('/companion/episodes/' + EPISODE + '/skip-segments')
        check(device + ': sponsor body and CTA join with source IDs', detected.spans.some(span => span.category === 'sponsor' && span.start === 5 && span.end === 20 && span.segment_ids.length === 3))
        check(device + ': lesson mentioning a vendor stays outside every skip', detected.spans.every(span => span.end <= 20 || span.start >= 40))
        await page.waitForFunction(() => document.getElementById('audio-player').readyState >= 2)
        await page.evaluate(() => {const audio = document.getElementById('audio-player'); audio.pause(); audio.currentTime = 8; audio.dispatchEvent(new Event('timeupdate'))})
        await wait(1000)
        report.playback = await page.evaluate(() => {
            const audio = document.getElementById('audio-player')
            return {title: document.querySelector('button[aria-label="Skip sponsors"]')?.title,
                media: {src: audio.src, currentSrc: audio.currentSrc, readyState: audio.readyState, duration: audio.duration,
                    currentTime: audio.currentTime, paused: audio.paused, seekable: Array.from({length: audio.seekable.length}, (_, i) => [audio.seekable.start(i), audio.seekable.end(i)])}}
        })
        fs.writeFileSync(path.join(EVIDENCE, 'playback-state.json'), JSON.stringify(report.playback, null, 2))
        await page.waitForFunction(() => document.getElementById('audio-player').currentTime >= 20)
        check(device + ': seeking into an ad jumps to the next lesson', await page.locator('.cut-notice').innerText().then(text => /sponsor|skipped/i.test(text)))
        await page.getByRole('button', {name: 'Undo skip', exact: true}).click()
        await wait(700)
        check(device + ': Undo returns to the skipped audio without immediately skipping again', await page.evaluate(() => Math.abs(document.getElementById('audio-player').currentTime - 8) < .1))
        await page.evaluate(() => document.getElementById('audio-player').dispatchEvent(new Event('timeupdate')))
        await wait(400)
        check(device + ': the replay survives subsequent media checks', await page.evaluate(() => Math.abs(document.getElementById('audio-player').currentTime - 8) < .1))
        await toggle.focus()
        check(device + ': switch supports keyboard focus', await toggle.evaluate(button => button === document.activeElement))
        await page.screenshot({path: path.join(EVIDENCE, device + '-player.png')})
        report.screenshots.push(device + '-player.png')
        await toggle.click()
        await page.waitForFunction(() => document.querySelector('button[aria-label="Skip sponsors"]')?.getAttribute('aria-pressed') === 'false')
        check(device + ': off clears the ranges', await page.locator('[data-range="sponsor"]').count() === 0)
        await page.evaluate(() => {const audio = document.getElementById('audio-player'); audio.currentTime = 8; audio.dispatchEvent(new Event('timeupdate'))})
        await wait(900)
        check(device + ': off leaves an ad seek untouched', await page.evaluate(() => Math.abs(document.getElementById('audio-player').currentTime - 8) < .1))
        await page.goto(BASE + '/ui/settings/sponsors', {waitUntil: 'domcontentloaded'})
        await page.getByRole('heading', {name: 'Skip sponsors', exact: true}).waitFor()
        check(device + ': explains own transcript detection', (await page.locator('.cut-settings').innerText()).includes('timed transcript'))
        await page.locator('label[for="cut-sponsors-enabled"]').getByRole('checkbox').click()
        await page.locator('label[for="cut-category-interaction"]').getByRole('checkbox').click()
        await page.waitForFunction(() => document.querySelector('label[for="cut-category-interaction"] [role="checkbox"]')?.getAttribute('aria-checked') === 'true')
        check(device + ': all eight independent category choices remain', await page.locator('.cut-categories [role="checkbox"]').count() === 8)
        check(device + ': category preference persists', (await json('/api/v1/settings/sponsorblock')).skipInteraction)
        await page.screenshot({path: path.join(EVIDENCE, device + '-settings.png')})
        report.screenshots.push(device + '-settings.png')
        check(device + ': settings fit the viewport', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
        await page.getByRole('combobox', {name: 'Sponsor skip mode', exact: true}).click()
        await page.getByRole('option', {name: 'Ask before skipping', exact: true}).click()
        await page.waitForFunction(async () => (await fetch('/companion/settings/skipping').then(r => r.json())).manual_categories.includes('sponsor'))
        check(device + ': manual sponsor preference persists on the server', (await json('/companion/settings/skipping')).manual_categories.includes('sponsor'))
        await page.getByRole('combobox', {name: 'Ignore passages shorter than', exact: true}).click()
        await page.getByRole('option', {name: '10 seconds', exact: true}).click()
        await page.waitForFunction(async () => (await fetch('/companion/settings/skipping').then(r => r.json())).minimum_seconds === 10)
        check(device + ': minimum skip length persists on the server', (await json('/companion/settings/skipping')).minimum_seconds === 10)
        await page.reload({waitUntil: 'domcontentloaded'})
        check(device + ': settings survive a browser reload', await page.getByRole('combobox', {name: 'Sponsor skip mode', exact: true}).innerText().then(text => text.includes('Ask before skipping')))
        await page.screenshot({path: path.join(EVIDENCE, device + '-settings-upgraded.png')})
        report.screenshots.push(device + '-settings-upgraded.png')
        const openEpisode = async () => {
            await page.goto(BASE + '/ui/learn?episode=' + EPISODE + '&at=0', {waitUntil: 'domcontentloaded'})
            await page.getByRole('button', {name: 'Play from 0:00', exact: true}).click()
            await page.waitForFunction(() => document.getElementById('audio-player').readyState >= 2)
        }
        const seekAd = async () => {
            await page.evaluate(() => {const audio = document.getElementById('audio-player'); audio.pause(); audio.currentTime = 8; audio.dispatchEvent(new Event('timeupdate'))})
            await wait(700)
        }
        await openEpisode()
        await page.waitForFunction(() => document.querySelector('button[aria-label="Skip sponsors"]')?.title.includes('manual skipping'))
        await seekAd()
        check(device + ': manual mode keeps the sponsor playing until clicked', await page.evaluate(() => Math.abs(document.getElementById('audio-player').currentTime - 8) < .1))
        check(device + ': short self-promotion and interaction cues are filtered by the minimum', await page.locator('[data-range="sponsor"]').count() === 1)
        await page.getByRole('button', {name: 'Skip this part', exact: true}).click()
        await page.waitForFunction(() => document.getElementById('audio-player').currentTime >= 20)
        check(device + ': the manual button seeks to the real range end', true)
        await page.getByRole('button', {name: 'Undo skip', exact: true}).click()
        await wait(600)
        check(device + ': the manual skip is also reversible', await page.evaluate(() => Math.abs(document.getElementById('audio-player').currentTime - 8) < .1))
        await put('/companion/settings/skipping', {manual_categories: [], minimum_seconds: 0})
        await put('/verify/source', {origin: 'feed'})
        await openEpisode()
        await page.waitForFunction(() => document.querySelector('button[aria-label="Skip sponsors"]')?.title.includes('Choose skips manually'))
        await seekAd()
        check(device + ': unchecked publisher timing never jumps automatically', await page.evaluate(() => Math.abs(document.getElementById('audio-player').currentTime - 8) < .1))
        check(device + ': unchecked timing still offers a manual skip', await page.getByRole('button', {name: 'Skip this part', exact: true}).count() === 1)
        await put('/verify/source', {origin: 'mismatch'})
        await openEpisode()
        await page.waitForFunction(() => document.querySelector('button[aria-label="Skip sponsors"]')?.title.includes('do not match'))
        await seekAd()
        check(device + ': mismatched timing leaves playback untouched and clears every marker', await page.evaluate(() => Math.abs(document.getElementById('audio-player').currentTime - 8) < .1) && await page.locator('[data-range="sponsor"]').count() === 0)
        check(device + ': the timing warning links to the existing transcript workspace', await page.getByRole('link', {name: 'Open transcript', exact: true}).count() === 1)
        await put('/verify/source', {origin: 'missing'})
        await openEpisode()
        await page.waitForFunction(() => document.querySelector('button[aria-label="Skip sponsors"]')?.title.includes('add timed words first'))
        check(device + ': missing timing has an explicit recovery action', await page.getByRole('link', {name: 'Open transcript', exact: true}).count() === 1)
        await put('/verify/source', {origin: 'generated'})
        let failNextScan = true
        await context.route('**/companion/episodes/' + EPISODE + '/skip-segments', route => {
            if (failNextScan) {failNextScan = false; return route.fulfill({status: 503, json: {detail: 'Fixture transient failure'}})}
            return route.continue()
        })
        await openEpisode()
        await page.getByRole('button', {name: 'Try again', exact: true}).click()
        await page.waitForFunction(() => document.querySelector('button[aria-label="Skip sponsors"]')?.title.includes('2 sponsor parts'))
        check(device + ': a failed scan recovers through the visible retry button', await page.locator('[data-range="sponsor"]').count() === 2)
        await context.unroute('**/companion/episodes/' + EPISODE + '/skip-segments')
        // Restore the shared fixture's preferences for the next device.
        await fetch(FIXTURE + '/api/v1/settings/sponsorblock', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled: true, skipInteraction: false})})
        await context.close()
    }
    const calls = await json('/verify/requests')
    report.requests = calls
    check('No SponsorBlock timing request from player or companion', !calls.http.some(call => /\/episodes\/[^/]+\/sponsorblock/.test(call.path)) && !calls.podfetch.some(([, path]) => path.includes('sponsorblock')))
    check('No uncaught browser errors', report.errors.length === 0)
    report.status = 'PASS'
})().catch(async error => {
    report.status = 'FAIL'
    report.failure = String(error.stack || error)
    process.exitCode = 1
    if (currentPage && !currentPage.isClosed()) {
        try {
            await currentPage.screenshot({path: path.join(EVIDENCE, 'failure.png')})
            report.screenshots.push('failure.png')
            fs.writeFileSync(path.join(EVIDENCE, 'failure-page.txt'), await currentPage.locator('body').innerText())
        } catch (captureError) {
            report.captureFailure = String(captureError)
        }
    }
}).finally(async () => {
    try {
        if (browser) await browser.close()
    } catch (error) {
        report.status = 'FAIL'
        report.cleanupFailure = String(error.stack || error)
        process.exitCode = 1
    } finally {
        await Promise.all(children.map(child => new Promise(resolve => {
            if (!child.pid || child.exitCode !== null || child.signalCode !== null) return resolve()
            const timer = setTimeout(() => { try { process.kill(-child.pid, 'SIGKILL') } catch {} }, 5000)
            child.once('exit', () => { clearTimeout(timer); resolve() })
            try { process.kill(-child.pid, 'SIGTERM') } catch { clearTimeout(timer); resolve() }
        })))
        fs.writeFileSync(path.join(EVIDENCE, 'browser.json'), JSON.stringify(report, null, 2))
        console.log(JSON.stringify({status: report.status, checks: report.checks.length, screenshots: report.screenshots, failure: report.failure, cleanupFailure: report.cleanupFailure}))
    }
})
