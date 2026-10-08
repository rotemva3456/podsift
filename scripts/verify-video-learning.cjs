// Portable browser acceptance: isolated synthetic providers by default; explicit live opt-in only.
const fs = require('fs'), path = require('path'), assert = require('assert/strict'), crypto = require('crypto')
const {spawn} = require('child_process')
const APP = path.resolve(__dirname, '..')
const LIVE = process.env.VERIFY_VIDEO_LIVE === '1'
assert.equal(Boolean(process.env.APP_URL), LIVE, 'Live verification requires both APP_URL and VERIFY_VIDEO_LIVE=1')
const SELF_RUN = !LIVE
const PORT = Number(process.env.VERIFY_PORT || '5401')
assert(Number.isInteger(PORT) && PORT >= 1024 && PORT <= 65535, 'VERIFY_PORT must be a valid unprivileged port')
const BASE = LIVE ? process.env.APP_URL.replace(/\/$/, '') : 'http://127.0.0.1:' + PORT
const OUT = path.resolve(process.env.EVIDENCE_DIR || path.join(APP, 'evidence/video-learning'))
const FILE = LIVE ? path.resolve(APP, process.env.VIDEO_FILE || 'runtime/video-learning/bash-lesson.mp4') : path.join(OUT, 'synthetic-video.mp4')
const OWNER = crypto.randomUUID()
const {chromium} = require(path.join(APP, 'ui/node_modules/@playwright/test'))
fs.mkdirSync(OUT, {recursive: true})

const report = {status: 'RUNNING', self_run: SELF_RUN, live_requested: LIVE,
    inference: SELF_RUN ? 'synthetic deterministic fixture providers; no external AI or media' : 'explicit live provider opt-in',
    checks: [], errors: [], screenshots: []}
const children = []
let browser, currentPage
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))
const check = (name, value = true) => {assert(value, name); report.checks.push(name)}
const shot = async name => {await currentPage.screenshot({path: path.join(OUT, name), fullPage: false}); report.screenshots.push(name)}
const start = (cmd, args, env, name, detached = true) => {
    const log = fs.openSync(path.join(OUT, name + '.log'), 'w')
    const child = spawn(cmd, args, {cwd: APP, env, detached, stdio: ['ignore', log, log]})
    child.on('error', error => { child.spawnError = error.message })
    children.push(child)
    fs.closeSync(log)
    return child
}
const run = async (cmd, args, env, name) => {
    const child = start(cmd, args, env, name, false)
    const code = await new Promise((resolve, reject) => {
        child.once('error', reject)
        child.once('exit', (exitCode, signal) => resolve(signal || exitCode))
    })
    assert.equal(code, 0, `${name} failed (${code}); see ${name}.log`)
}
const json = async (endpoint, options, timeout = 90_000) => {
    const response = await fetch(BASE + endpoint, {...options, signal: AbortSignal.timeout(timeout)})
    if (!response.ok) throw Error(`${endpoint}: ${response.status} ${await response.text()}`)
    return response.json()
}
const put = (endpoint, body) => json(endpoint, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
const settled = async id => {
    const deadline = Date.now() + 240_000
    while (Date.now() < deadline) {
        const source = await json(`/companion/videos/${id}`)
        if (!['running', 'queued', 'cancelling'].includes(source.status)) return source
        await wait(500)
    }
    throw Error('Processing did not finish within four minutes')
}
const finished = async id => {
    const source = await settled(id)
    assert.equal(source.status, 'ready', source.error || source.status)
    return source
}

;(async () => {
    assert(LIVE || fs.existsSync(path.resolve(process.env.UI_DIST || path.join(APP, 'ui/dist'), 'index.html')),
        'Build the production UI first or set UI_DIST to its dist directory')
    if (SELF_RUN) {
        await run('ffmpeg', ['-v', 'error', '-nostdin', '-y',
            '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24:duration=45',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=45',
            '-t', '45', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '96k', '-movflags', '+faststart', FILE], process.env, 'ffmpeg')
        const fixtureEnv = {PATH: process.env.PATH, HOME: process.env.HOME, LANG: 'C.UTF-8', PYTHONPATH: APP,
            PODFETCH_BACKEND: BASE, PODSIFT_HOSTED: 'false', VERIFY_PORT: String(PORT), VERIFY_OWNER: OWNER,
            EVIDENCE_DIR: OUT, UI_DIST: path.resolve(process.env.UI_DIST || path.join(APP, 'ui/dist'))}
        start(process.env.APP_PYTHON || 'python3', ['-m', 'uvicorn', 'video_fixture:app', '--app-dir',
            path.join(APP, 'scripts/fixtures'), '--host', '127.0.0.1', '--port', String(PORT)], fixtureEnv, 'fixture')
        const deadline = Date.now() + 60_000
        while (true) {
            try {if ((await json('/verify/owner', undefined, 2_000)).owner === OWNER) break} catch {}
            assert(Date.now() < deadline && children.every(child => child.exitCode === null || child.exitCode === 0) &&
                children.every(child => !child.spawnError), 'fixture starts on its isolated port')
            await wait(300)
        }
    }
    assert(fs.existsSync(FILE), `Video file does not exist: ${FILE}`)

    let executablePath = process.env.CHROMIUM_BIN
    if (!executablePath && !fs.existsSync(chromium.executablePath())) {
        const cache = path.join(process.env.HOME, '.cache/ms-playwright')
        executablePath = (fs.existsSync(cache) ? fs.readdirSync(cache) : []).filter(name => name.startsWith('chromium_headless_shell-')).sort().reverse()
            .map(name => path.join(cache, name, 'chrome-headless-shell-linux64/chrome-headless-shell')).find(fs.existsSync)
    }
    browser = await chromium.launch({headless: true, executablePath, args: ['--no-sandbox'], timeout: 90_000})
    const context = await browser.newContext({viewport: {width: 1366, height: 768}, serviceWorkers: 'block'})
    await context.route('**/health', route => route.fulfill({json: {mode: 'self-hosted'}}))
    const page = await context.newPage()
    currentPage = page
    page.setDefaultTimeout(90_000)
    page.on('pageerror', error => report.errors.push(error.message))

    await page.goto(BASE + '/ui/learn?source=videos', {waitUntil: 'domcontentloaded'})
    await page.getByRole('heading', {name: 'Learn from a video'}).waitFor()
    check('Existing four primary destinations are retained', (await page.locator('.listen-sidebar nav > a').allTextContents()).join('|') === 'Today|Library|Learn|Knowledge')
    await shot('desktop-import.png')
    await page.locator('input[type=file]').setInputFiles(FILE)
    await page.getByRole('button', {name: 'Import video', exact: true}).click()
    await page.waitForURL(url => url.searchParams.has('video'))
    const id = new URL(page.url()).searchParams.get('video')
    report.video_id = id
    const acceptAction = async (button, action, expected = 202) => {
        const accepted = page.waitForResponse(response => response.request().method() === 'POST' &&
            new URL(response.url()).pathname === `/companion/videos/${id}/${action}`)
        accepted.catch(() => {})
        await page.getByRole('button', {name: button, exact: true}).click()
        const response = await accepted
        assert.equal(response.status(), expected, `${action} was not accepted: ${response.status()}`)
    }
    let source = await json(`/companion/videos/${id}`)
    report.duration = source.duration
    report.original_bytes = fs.statSync(FILE).size
    check('Import alone does not start processing', !['running', 'queued'].includes(source.status) && (LIVE || !source.has_transcript))
    const player = page.locator('section[aria-label="Video player"] video')
    await player.waitFor()
    await page.waitForFunction(() => document.querySelector('section[aria-label="Video player"] video')?.readyState >= 1)
    check('Desktop has no horizontal overflow', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    check('Original MP4 is playable through native media requests', await player.evaluate(video => Math.abs(video.duration - 45) < 0.5))
    const downloadEvent = page.waitForEvent('download')
    downloadEvent.catch(() => {})
    await page.locator('a[download]').click()
    const download = await downloadEvent
    const copy = path.join(OUT, 'original-video.mp4')
    await download.saveAs(copy)
    const hash = name => crypto.createHash('sha256').update(fs.readFileSync(name)).digest('hex')
    check('Downloaded video matches the imported bytes', hash(copy) === hash(FILE))

    if (!source.has_transcript) {
        await acceptAction('Transcribe full video', 'transcribe')
        source = await finished(id)
    }
    const doc = await json(`/companion/videos/${id}/transcript?limit=500`)
    check('Full original-clock audio was processed', Math.abs(doc.processed_seconds - source.duration) < 0.05 && doc.coverage_kind === 'full_audio')
    check('Timed synthetic speech passed the real transcription pipeline', doc.segments.length > 2 && doc.text.length > 100)
    check('Speech coverage is distinct from vision', doc.visual_coverage === 'none')
    report.transcript_segments = doc.total_segments
    report.transcript_digest = doc.digest
    report.last_speech_end = doc.segments.at(-1).end

    await page.reload({waitUntil: 'domcontentloaded'})
    await page.getByRole('button', {name: 'Summarize', exact: true}).waitFor()
    if (SELF_RUN) {
        await put('/verify/provider', {mode: 'wrong_provider'})
        await acceptAction('Summarize', 'learn')
        source = await settled(id)
        check('Invalid provider citations fail without replacing saved speech', source.status === 'failed' && source.has_transcript && !source.learning && /outside this video/.test(source.error))
        await put('/verify/provider', {mode: 'normal'})
        await page.reload({waitUntil: 'domcontentloaded'})
    }
    await acceptAction('Summarize', 'learn')
    source = await finished(id)
    check(SELF_RUN ? 'Wrong-provider failure recovers to a source-backed recap' : 'Requested recap contains source-backed points',
        source.learning.task === 'summary' && source.learning.points.length > 0)
    check('Recap generates no bundled watch guide or quiz', source.learning.moments.length === 0)
    await page.reload({waitUntil: 'domcontentloaded'})
    await page.getByRole('heading', {name: 'Your recap', exact: true}).waitFor()
    await shot('desktop-recap.png')

    await page.getByText('Focus & time (optional)', {exact: true}).click()
    await page.locator('#video-goal').fill('Understand Bash conditional tests, including the prerequisites.')
    await page.locator('#video-minutes').fill('1')
    if (SELF_RUN) {
        await put('/verify/provider', {mode: 'block'})
        await acceptAction('What should I watch?', 'learn')
        await page.getByRole('button', {name: 'Cancel processing', exact: true}).waitFor()
        await acceptAction('Cancel processing', 'cancel', 200)
        const cancelDeadline = Date.now() + 10_000
        while (true) {
            source = await json(`/companion/videos/${id}`)
            if (source.status === 'cancelling') break
            assert(Date.now() < cancelDeadline, `Cancel request did not reach the server (status: ${source.status})`)
            await wait(100)
        }
        await json('/verify/provider/release', {method: 'POST'})
        source = await settled(id)
        check('Cancellation preserves the transcript and previous recap', source.status === 'cancelled' && source.has_transcript && source.learning?.task === 'summary')
        await put('/verify/provider', {mode: 'normal'})
        await page.reload({waitUntil: 'domcontentloaded'})
        await page.getByText('Focus & time (optional)', {exact: true}).click()
        await page.locator('#video-goal').fill('Understand Bash conditional tests, including the prerequisites.')
        await page.locator('#video-minutes').fill('1')
    }
    await acceptAction('What should I watch?', 'learn')
    source = await finished(id)
    assert(source.learning?.task === 'watch_plan', 'Acceptance requires a saved watch guide')
    check('Watch guide is source-bound and inside the requested budget', source.learning.transcript_digest === doc.digest && source.learning.watch_seconds <= 60)
    const moment = source.learning.moments[0]
    check('Guidance resolves IDs to valid original-video seconds', moment && moment.start >= 0 && moment.end <= source.duration && moment.start < moment.end)
    report.focus_start = moment.start
    report.focus_end = moment.end
    await page.reload({waitUntil: 'domcontentloaded'})
    await page.getByRole('heading', {name: 'Useful moments to watch', exact: true}).waitFor()
    await page.waitForFunction(() => document.querySelector('section[aria-label="Video player"] video')?.readyState >= 1)
    await player.evaluate((video, start) => {video.pause(); video.currentTime = Math.min(video.duration - 1, start + 5)}, moment.start)
    await page.waitForFunction(() => !document.querySelector('section[aria-label="Video player"] video')?.seeking)
    await player.evaluate(video => {
        window.__verifiedVideoSeek = null
        video.addEventListener('playing', () => {window.__verifiedVideoSeek = video.currentTime; video.pause()}, {once: true})
    })
    await page.getByRole('button', {name: new RegExp(moment.title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))}).click()
    await page.waitForFunction(() => window.__verifiedVideoSeek !== null)
    check('Guidance actually seeks the browser player', Math.abs(await page.evaluate(() => window.__verifiedVideoSeek) - moment.start) < 0.5)
    await shot('desktop-guide.png')

    await page.setViewportSize({width: 390, height: 844})
    await page.goto(BASE + `/ui/learn?video=${id}`, {waitUntil: 'domcontentloaded'})
    await player.waitFor()
    await page.getByRole('heading', {name: 'Useful moments to watch', exact: true}).waitFor()
    check('Mobile has no horizontal overflow', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    const bounds = await player.boundingBox()
    check('Video is visible in the first mobile viewport', bounds && bounds.y >= 0 && bounds.y < 844 && bounds.width <= 390)
    await shot('mobile-video.png')
    await page.getByRole('button', {name: 'Transcript', exact: true}).click()
    await page.locator('section[aria-label="Video words and learning"] ol li').first().waitFor()
    await page.locator('section[aria-label="Video words and learning"]').scrollIntoViewIfNeeded()
    await shot('mobile-transcript.png')
    if (SELF_RUN) {
        const providers = await json('/verify/provider')
        check('Only deterministic fixture inference was used', providers.speech_calls === 1 && providers.learning_calls.every(call => ['wrong_provider', 'normal', 'block'].includes(call.mode)))
        report.provider_calls = providers
    }
    check('Browser has no uncaught exceptions', report.errors.length === 0)
    report.status = 'PASS'
})().catch(async error => {
    report.status = 'FAIL'
    report.failure = String(error.stack || error)
    process.exitCode = 1
    if (currentPage && !currentPage.isClosed()) {
        try {await shot('failure.png'); fs.writeFileSync(path.join(OUT, 'failure-page.txt'), await currentPage.locator('body').innerText())}
        catch (captureError) {report.captureFailure = String(captureError.stack || captureError)}
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
            const group = child.spawnargs[0] !== 'ffmpeg'
            const timer = setTimeout(() => {try {process.kill(group ? -child.pid : child.pid, 'SIGKILL')} catch {}; resolve()}, 5000)
            child.once('exit', () => {clearTimeout(timer); resolve()})
            try {process.kill(group ? -child.pid : child.pid, 'SIGTERM')} catch {clearTimeout(timer); resolve()}
        })))
        fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 2))
        console.log(JSON.stringify({status: report.status, checks: report.checks.length, screenshots: report.screenshots,
            video_id: report.video_id, failure: report.failure, cleanupFailure: report.cleanupFailure}))
    }
})
