// Real local browser + companion APIs. Uses an isolated copy of the notes database.
// Does not call a model, transcribe, download an episode, or modify the shared queue.
const {chromium} = require('../ui/node_modules/@playwright/test')
const fs = require('fs'), path = require('path'), assert = require('assert/strict')
const ROOT = path.resolve(__dirname, '..')
const BASE = process.env.APP_URL || 'http://127.0.0.1:5219'
const EPISODE = process.env.VERIFY_EPISODE_ID || '307f40e8-92f1-44a0-b58b-fbf0e84951d6'
const EVIDENCE = path.resolve(ROOT, process.env.EVIDENCE_DIR || 'evidence/source-workspace')
fs.mkdirSync(EVIDENCE, {recursive: true})

async function json(endpoint, init) {
    const response = await fetch(BASE + endpoint, {...init, signal: AbortSignal.timeout(90000)})
    assert(response.ok, `${response.status} ${endpoint}`)
    return response.json()
}

;(async () => {
    let executablePath = process.env.CHROMIUM_BIN
    if (!executablePath && !fs.existsSync(chromium.executablePath())) {
        const cache = path.join(process.env.HOME, '.cache/ms-playwright')
        executablePath = fs.readdirSync(cache).filter(name => name.startsWith('chromium_headless_shell-')).sort().reverse()
            .map(name => path.join(cache, name, 'chrome-headless-shell-linux64/chrome-headless-shell')).find(fs.existsSync)
    }
    const browser = await chromium.launch({headless: true, executablePath, args: ['--no-sandbox'], timeout: 90000})
    const context = await browser.newContext({viewport: {width: 1366, height: 768}, permissions: ['clipboard-read', 'clipboard-write']})
    const page = await context.newPage()
    page.setDefaultTimeout(90000)
    const report = {status: 'RUNNING', checks: [], errors: [], screenshots: [], episode_id: EPISODE}
    page.on('pageerror', error => report.errors.push(error.message))
    const check = (name, value = true) => {assert(value, name); report.checks.push(name)}
    const shot = async name => {await page.screenshot({path: path.join(EVIDENCE, name), fullPage: false}); report.screenshots.push(name)}
    try {
        await page.goto(BASE + '/ui/home/view', {waitUntil: 'domcontentloaded'})
        await page.getByRole('heading', {name: 'Today', exact: true}).waitFor()
        const primary = await page.locator('.listen-sidebar nav > a').allTextContents()
        check('Today, Library, Learn, Knowledge are primary destinations', primary.join('|') === 'Today|Library|Learn|Knowledge')
        await page.locator('.listen-sidebar').getByRole('button', {name: /More tools/}).click()
        await page.locator('.listen-sidebar a[href$="/discover"]').waitFor({state: 'visible'})
        await page.locator('.listen-sidebar a[href$="/queue"]').waitFor({state: 'visible'})
        check('Discovery and listening queue remain reachable', await page.locator('.listen-sidebar a[href$="/discover"]').isVisible()
            && await page.locator('.listen-sidebar a[href$="/queue"]').isVisible())
        await shot('desktop-today.png')

        const transcript = await json(`/companion/episodes/${EPISODE}/transcript`)
        assert(transcript.timed && transcript.digest && transcript.segments.length > 6, 'Verification requires an existing timed transcript')
        const selectedIndex = transcript.segments.findIndex(segment => segment.start >= 300 && segment.end > segment.start
            && /\b(BGP|route|routing|protocol|network)\b/i.test(segment.text) && !/sponsor|meter\.com|book a demo|patreon/i.test(segment.text))
        assert(selectedIndex >= 0, 'Verification requires a substantive source passage')
        const selectedPassage = transcript.segments[selectedIndex]
        await page.goto(BASE + `/ui/learn?episode=${EPISODE}`, {waitUntil: 'domcontentloaded'})
        const source = page.locator('.source-workspace')
        await source.locator('[data-passage-index="0"] p').waitFor()
        await page.evaluate(() => document.fonts.ready)
        const firstWords = await source.locator('[data-passage-index="0"] p').boundingBox()
        check('Source words appear in the first small-laptop viewport', firstWords && firstWords.y >= 0 && firstWords.y < 768)
        await source.locator(`[data-passage-index="${selectedIndex}"]`).waitFor()
        await source.locator(`[data-passage-index="${selectedIndex}"]`).getByRole('button', {name: /^Select passage/}).click()
        await source.getByRole('button', {name: 'Copy agent instruction', exact: true}).click()
        await source.getByRole('button', {name: 'Instruction copied', exact: true}).waitFor()
        const copied = await page.evaluate(() => navigator.clipboard.readText())
        check('Agent instruction carries the exact source, digest, and passage ID', copied.includes(EPISODE)
            && copied.includes(transcript.digest) && copied.includes(selectedPassage.id))
        await source.getByRole('button', {name: 'Save idea', exact: true}).click()
        await source.getByRole('link', {name: 'Open Knowledge', exact: true}).waitFor()
        const saved = (await json(`/companion/notes?episode_id=${EPISODE}`)).find(note => note.position === selectedPassage.start)
        check('Selected source words save without requiring a personal note', saved?.quote?.includes(selectedPassage.text)
            && saved.episode_id === EPISODE)
        report.saved_note_id = saved.id
        await source.getByRole('button', {name: 'Keep selected', exact: true}).click()
        await page.waitForURL(url => url.searchParams.has('plan'))
        const planId = new URL(page.url()).searchParams.get('plan')
        const plan = await json(`/companion/plans/${planId}`)
        check('Manual selection uses the shared version-bound agent plan API', plan.mode === 'agent' && plan.spans.length > 0
            && plan.episodes.every(episode => episode.episode_id === EPISODE))
        report.plan_id = planId
        await page.locator('.cut-script').waitFor()
        const speech = await json('/companion/speech/status')
        assert(speech.configured === false, 'This verification exports without paid speech checks')
        await source.getByRole('button', {name: 'Approve and export MP3', exact: true}).click()
        await source.getByRole('button', {name: 'Download MP3', exact: true}).waitFor({timeout: 180000})
        const downloading = page.waitForEvent('download')
        await source.getByRole('button', {name: 'Download MP3', exact: true}).click()
        const download = await downloading
        const audioPath = path.join(EVIDENCE, 'source-selection.mp3')
        await download.saveAs(audioPath)
        check('The browser exports and downloads a real MP3 from the selected original audio', fs.statSync(audioPath).size > 1000)
        report.export_bytes = fs.statSync(audioPath).size
        await shot('desktop-selected-listen.png')
        await page.reload({waitUntil: 'domcontentloaded'})
        await page.locator('.cut-script').waitFor()
        check('Plan selection and its exact script survive reload', new URL(page.url()).searchParams.get('plan') === planId)
        await source.getByRole('button', {name: 'Original transcript', exact: true}).click()
        await source.locator(`[data-passage-index="${selectedIndex}"]`).waitFor()
        check('Full original words remain available after inspecting a selected listen', !new URL(page.url()).searchParams.has('plan'))
        await source.getByRole('textbox', {name: 'Find in transcript'}).fill('zzzz_source_workspace_no_match')
        await source.getByRole('heading', {name: 'No matching passages'}).waitFor()
        check('Exact transcript search has a useful no-match state')
        await source.getByRole('textbox', {name: 'Find in transcript'}).fill('')
        await shot('desktop-transcript.png')

        await page.goto(BASE + '/ui/knowledge', {waitUntil: 'domcontentloaded'})
        await page.locator('.knowledge-quote').first().waitFor()
        await page.getByRole('textbox', {name: 'Search saved ideas'}).fill(selectedPassage.text.slice(0, 30))
        check('Knowledge finds the saved exact source quote', await page.locator('.knowledge-quote').count() > 0)
        await page.reload({waitUntil: 'domcontentloaded'})
        await page.locator('.knowledge-open').first().waitFor()
        await shot('desktop-knowledge.png')
        await page.locator('.knowledge-open').first().click()
        await page.locator('.requested-passage').waitFor()
        check('Saved idea recovers the original source moment after reload', Number(new URL(page.url()).searchParams.get('at')) === saved.position)

        await page.setViewportSize({width: 390, height: 844})
        await page.goto(BASE + `/ui/learn?episode=${EPISODE}`, {waitUntil: 'domcontentloaded'})
        await source.locator('[data-passage-index="0"] p').waitFor()
        await page.evaluate(() => document.fonts.ready)
        const mobileWords = await source.locator('[data-passage-index="0"] p').boundingBox()
        check('Source words appear above the navigation in the first mobile viewport', mobileWords && mobileWords.y >= 0 && mobileWords.y < 780)
        check('Mobile keeps the transcript directly visible', await source.isVisible())
        check('Mobile page has no horizontal overflow', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
        check('Mobile navigation retains the four main destinations', (await page.locator('.listen-mobile-nav > a').allTextContents()).join('|') === 'Today|Library|Learn|Knowledge')
        await page.evaluate(() => window.scrollTo(0, 0))
        await shot('mobile-transcript.png')
        await page.goto(BASE + '/ui/knowledge', {waitUntil: 'domcontentloaded'})
        await page.locator('.knowledge-quote').first().waitFor()
        check('Mobile Knowledge preserves readable source words without overflow', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
        await shot('mobile-knowledge.png')
        check('No uncaught browser exceptions', report.errors.length === 0)
        report.status = 'PASS'
    } catch (error) {
        report.status = 'FAIL'; report.failure = error.stack || String(error)
        await shot('failure.png').catch(() => {})
        throw error
    } finally {
        fs.writeFileSync(path.join(EVIDENCE, 'report.json'), JSON.stringify(report, null, 2))
        console.log(JSON.stringify(report))
        await browser.close()
    }
})().catch(error => {console.error(error); process.exitCode = 1})
