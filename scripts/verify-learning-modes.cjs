// Self-run desktop/mobile check with synthetic source material and AI disabled.
const fs = require('fs'), path = require('path'), assert = require('assert/strict')
const {spawn} = require('child_process')
const {randomUUID} = require('crypto')
const ROOT = path.resolve(__dirname, '..')
const PORT = Number(process.env.VERIFY_PORT || '5401')
assert(Number.isInteger(PORT) && PORT >= 1024 && PORT <= 65535, 'VERIFY_PORT must be a valid unprivileged port')
const SELF_RUN = !process.env.APP_URL
const BASE = process.env.APP_URL || `http://127.0.0.1:${PORT}`
const EPISODE = process.env.VERIFY_EPISODE_ID || '3f0c2a8e-5b7d-4c1e-9a6f-0d2e4b6c8a10'
const EVIDENCE = path.resolve(ROOT, process.env.EVIDENCE_DIR || 'evidence/learning-modes')
const OWNER = randomUUID()
const {chromium} = require(path.join(ROOT, 'ui/node_modules/@playwright/test'))
fs.mkdirSync(EVIDENCE, {recursive: true})
const report = {status: 'STARTING', started_at: new Date().toISOString(), self_run: SELF_RUN,
    data: SELF_RUN ? 'synthetic networking transcript and silent WAV; real companion and production UI build' : 'explicit APP_URL service',
    episode_id: EPISODE, checks: [], errors: [], screenshots: [], plans: [], ai: 'disabled; manual source selections only'}
fs.writeFileSync(path.join(EVIDENCE, 'report.json'), JSON.stringify(report))
const children = []
let browser, currentPage

function startFixture() {
    const log = fs.openSync(path.join(EVIDENCE, 'fixture.log'), 'w')
    const env = {
        PATH: process.env.PATH, HOME: process.env.HOME, LANG: 'C.UTF-8', PYTHONPATH: ROOT,
        PODFETCH_BACKEND: BASE, PODSIFT_HOSTED: 'false', VERIFY_PORT: String(PORT), VERIFY_OWNER: OWNER,
        EVIDENCE_DIR: EVIDENCE, UI_DIST: path.resolve(process.env.UI_DIST || path.join(ROOT, 'ui/dist')),
    }
    const child = spawn(process.env.APP_PYTHON || 'python3', [
        '-m', 'uvicorn', 'learning_fixture:app', '--app-dir', path.join(ROOT, 'scripts/fixtures'),
        '--host', '127.0.0.1', '--port', String(PORT),
    ], {cwd: ROOT, env, detached: true, stdio: ['ignore', log, log]})
    child.on('error', error => { child.spawnError = error.message })
    children.push(child)
    fs.closeSync(log)
}

const wait = ms => new Promise(resolve => setTimeout(resolve, ms))

async function json(endpoint, init) {
    const response = await fetch(BASE + endpoint, {...init, signal: AbortSignal.timeout(60000)})
    assert(response.ok, `${response.status} ${endpoint}`)
    return response.json()
}

;(async () => {
    if (SELF_RUN) {
        startFixture()
        const deadline = Date.now() + 60_000
        while (true) {
            try {
                const response = await fetch(BASE + '/verify/owner', {signal: AbortSignal.timeout(2000)})
                if (response.ok && (await response.json()).owner === OWNER) break
            } catch {}
            assert(Date.now() < deadline && children.every(child => child.exitCode === null && !child.spawnError),
                'fixture starts on its own port and returns this run owner token')
            await wait(300)
        }
    }
    let executablePath = process.env.CHROMIUM_BIN
    if (!executablePath && !fs.existsSync(chromium.executablePath())) {
        const cache = path.join(process.env.HOME, '.cache/ms-playwright')
        executablePath = fs.readdirSync(cache).filter(name => name.startsWith('chromium_headless_shell-')).sort().reverse()
            .map(name => path.join(cache, name, 'chrome-headless-shell-linux64/chrome-headless-shell')).find(fs.existsSync)
    }
    browser = await chromium.launch({headless: true, executablePath, args: ['--no-sandbox'], timeout: 60000})
    report.status = 'RUNNING'
    const check = (name, result) => {assert(result, name); report.checks.push(name); fs.writeFileSync(path.join(EVIDENCE, 'report.json'), JSON.stringify(report, null, 2))}
    try {
        if (SELF_RUN) check('Fixture owner token belongs to this verification run', (await json('/verify/owner')).owner === OWNER)
        assert(!(await json('/companion/settings/ai')).configured, 'Use an isolated companion with AI disabled')
        const source = await json(`/companion/episodes/${EPISODE}/transcript`)
        assert(source.timed && source.digest && source.segments.length > 6, 'An existing timed source is required')
        if (SELF_RUN) check('Synthetic networking passages fit the synthetic audio', source.segments.length === 10
            && Math.max(...source.segments.map(segment => segment.end)) <= 120)
        const indexes = source.segments.map((segment, index) => ({segment, index})).filter(({segment}) => segment.start >= 0
            && /\b(BGP|route|routing|protocol|network)\b/i.test(segment.text)
            && !/sponsor|meter\.com|book a demo|patreon/i.test(segment.text)).slice(0, 2).map(item => item.index)
        assert(indexes.length === 2, 'Two substantive source passages are required')
        for (const [device, viewport] of [['desktop', {width: 1366, height: 768}], ['mobile', {width: 390, height: 844}]]) {
            const context = await browser.newContext({viewport, permissions: ['clipboard-read', 'clipboard-write'], serviceWorkers: 'block'})
            const page = await context.newPage()
            currentPage = page
            page.setDefaultTimeout(60000)
            page.on('pageerror', error => report.errors.push(error.message))
            await page.goto(`${BASE}/ui/learn?episode=${EPISODE}`, {waitUntil: 'domcontentloaded'})
            const workspace = page.locator('.source-workspace')
            await workspace.locator('[data-passage-index="0"] p').waitFor()
            check(`${device}: effort choices stay out of ordinary transcript reading`, await workspace.getByRole('button', {name: 'Focus', exact: true}).count() === 0)
            for (const [offset, effort] of ['focus', 'chill'].entries()) {
                const label = effort === 'focus' ? 'Focus' : 'Chill'
                await workspace.locator(`[data-passage-index="${indexes[offset]}"]`).getByRole('button', {name: /^Select passage/}).click()
                await workspace.getByRole('button', {name: label, exact: true}).click()
                await workspace.getByRole('button', {name: 'Copy agent instruction', exact: true}).click()
                await workspace.getByRole('button', {name: 'Instruction copied', exact: true}).waitFor()
                const copied = await page.evaluate(() => navigator.clipboard.readText())
                report.last_instruction = {effort, url: page.url(), text: copied, digest: source.digest, passage_id: source.segments[indexes[offset]].id}
                check(`${device}: ${label} instruction retains exact source and effort`, copied.includes(`learning_mode=${effort}`)
                    && copied.includes(source.digest) && copied.includes(source.segments[indexes[offset]].id))
                await workspace.getByRole('button', {name: 'Keep selected', exact: true}).click()
                await page.waitForURL(url => url.searchParams.has('plan'))
                const planId = new URL(page.url()).searchParams.get('plan')
                const plan = await json(`/companion/plans/${planId}`)
                check(`${device}: ${label} manual plan persists its effort`, plan.mode === 'agent' && plan.learning_mode === effort && plan.spans.length > 0)
                report.plans.push({device, effort, id: planId, source_passage_id: source.segments[indexes[offset]].id})
                await workspace.locator('.cut-script').waitFor()
                check(`${device}: saved ${label} label is visible`, (await workspace.locator('.cut-plan-head').innerText()).includes(label))
                await page.reload({waitUntil: 'domcontentloaded'})
                await workspace.locator('.cut-script').waitFor()
                check(`${device}: ${label} and its script survive reload`, (await workspace.locator('.cut-plan-head').innerText()).includes(label))
                await workspace.locator('.cut-plan-head').scrollIntoViewIfNeeded()
                const screenshot = `${device}-${effort}-saved.png`
                await page.screenshot({path: path.join(EVIDENCE, screenshot)})
                report.screenshots.push(screenshot)
                await page.goto(`${BASE}/ui/learn?episode=${EPISODE}`, {waitUntil: 'domcontentloaded'})
                await workspace.locator('[data-passage-index="0"] p').waitFor()
            }
            await page.getByRole('button', {name: 'Cut', exact: true}).click()
            const tool = page.locator('.episode-tool')
            await tool.getByRole('button', {name: 'Chill', exact: true}).waitFor()
            await tool.getByRole('button', {name: 'Chill', exact: true}).click()
            await tool.getByRole('textbox', {name: 'What do you want to hear?'}).fill('BGP recap')
            check(`${device}: effort planning stays unavailable without AI`, await tool.getByRole('button', {name: 'Find the parts', exact: true}).isDisabled())
            await tool.getByRole('button', {name: 'Copy agent instructions', exact: true}).click()
            await tool.getByRole('button', {name: 'Instructions copied', exact: true}).waitFor()
            const fallback = await page.evaluate(() => navigator.clipboard.readText())
            check(`${device}: connected-agent fallback includes effort and goal`, fallback.includes('learning_mode=chill') && fallback.includes('My goal: BGP recap'))
            await tool.getByRole('button', {name: 'Chill', exact: true}).focus()
            check(`${device}: effort buttons support keyboard focus`, await tool.getByRole('button', {name: 'Chill', exact: true}).evaluate(button => button === document.activeElement))
            const screenshot = `${device}-chill-planner.png`
            await page.screenshot({path: path.join(EVIDENCE, screenshot)})
            report.screenshots.push(screenshot)
            await tool.getByRole('button', {name: 'Any effort', exact: true}).click()
            check(`${device}: Any effort restores keyword planning`, await tool.getByRole('button', {name: 'Find the parts', exact: true}).isEnabled())
            check(`${device}: app AI planning remains disabled without a configured provider`, await tool.getByRole('radio', {name: /^AI\b/}).isDisabled())
            check(`${device}: layout fits viewport`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
            await context.close()
            currentPage = undefined
        }
        const focused = await json('/companion/plans?learning_mode=focus')
        const chilled = await json('/companion/plans?learning_mode=chill')
        check('Saved Focus and Chill listens can be retrieved separately', focused.length > 0 && chilled.length > 0
            && focused.every(plan => plan.learning_mode === 'focus') && chilled.every(plan => plan.learning_mode === 'chill'))
        check('No uncaught browser errors', report.errors.length === 0)
        report.status = 'PASS'
    } catch (error) {
        report.status = 'FAIL'; report.failure = String(error.stack || error); throw error
    } finally {
        fs.writeFileSync(path.join(EVIDENCE, 'report.json'), JSON.stringify(report, null, 2))
    }
})().catch(async error => {
    report.status = 'FAIL'
    report.failure ||= String(error.stack || error)
    fs.writeFileSync(path.join(EVIDENCE, 'report.json'), JSON.stringify(report, null, 2))
    console.error(error)
    process.exitCode = 1
    if (currentPage && !currentPage.isClosed()) {
        try { await currentPage.screenshot({path: path.join(EVIDENCE, 'failure.png')}) } catch {}
    }
}).finally(async () => {
    let cleanupFailure
    try {
        if (browser) await browser.close()
    } catch (error) {
        cleanupFailure = String(error.stack || error)
        process.exitCode = 1
    } finally {
        await Promise.all(children.map(child => new Promise(resolve => {
            if (!child.pid || child.exitCode !== null || child.signalCode !== null) return resolve()
            const timer = setTimeout(() => { try { process.kill(-child.pid, 'SIGKILL') } catch {} }, 5000)
            child.once('exit', () => { clearTimeout(timer); resolve() })
            try { process.kill(-child.pid, 'SIGTERM') } catch { clearTimeout(timer); resolve() }
        })))
    }
    const reportPath = path.join(EVIDENCE, 'report.json')
    if (cleanupFailure) {report.status = 'FAIL'; report.cleanupFailure = cleanupFailure}
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2))
    console.log(JSON.stringify({status: report.status, checks: report.checks?.length || 0, screenshots: report.screenshots || [], failure: report.failure, cleanupFailure}))
})
