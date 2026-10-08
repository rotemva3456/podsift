// Real library context and source seeking; generation uses an explicit local fixture.
// It plays audio, which moves resume points: run it against your own dev copy (APP_URL), not the shared PodFetch.
const {chromium} = require('../ui/node_modules/@playwright/test')
const fs = require('fs'), path = require('path'), assert = require('assert/strict')
const {spawn} = require('child_process')
const ROOT = path.resolve(__dirname, '..'), BASE = process.env.APP_URL || 'http://127.0.0.1:5189'
const EVIDENCE = path.resolve(ROOT, process.env.EVIDENCE_DIR || 'evidence')
const FIXTURE = 'http://127.0.0.1:' + (process.env.ANSWER_FIXTURE_PORT || '18182')
const pause = ms => new Promise(resolve => setTimeout(resolve, ms))

;(async () => {
    const report = {status: 'FAIL', method: 'Real library and playback; deterministic local provider fixture, no inference', checks: [], errors: []}
    const fixture = spawn(process.env.APP_PYTHON || 'python3', ['scripts/answer_fixture.py'], {cwd: ROOT, stdio: 'ignore'})
    let browser
    try {
        let ready = false
        for (let attempt = 0; attempt < 80; attempt++) {
            if (fixture.exitCode !== null) throw new Error('Answer fixture exited before startup')
            try {ready = (await fetch(FIXTURE + '/companion/health')).ok} catch {}
            if (ready) break
            await pause(250)
        }
        assert(ready, 'Answer fixture starts')
        let executablePath = process.env.CHROMIUM_BIN
        if (!executablePath && !fs.existsSync(chromium.executablePath())) {
            const directory = path.join(process.env.HOME, '.cache/ms-playwright')
            executablePath = fs.readdirSync(directory).filter(x => x.startsWith('chromium_headless_shell-')).sort().reverse()
                .map(x => path.join(directory, x, 'chrome-headless-shell-linux64/chrome-headless-shell')).find(fs.existsSync)
        }
        browser = await chromium.launch({headless: true, executablePath, args: ['--no-sandbox']})
        const page = await browser.newPage({viewport: {width: 1440, height: 1050}})
        page.on('pageerror', error => report.errors.push(error.message))
        const timeline = await (await fetch(BASE + '/api/v1/podcasts/timeline?notListened=false&favoredEpisodes=false')).json()
        const first = timeline.data[0].podcast_episode, second = timeline.data[1].podcast_episode
        const doc = await (await fetch(BASE + '/companion/episodes/' + first.episode_id + '/transcript')).json()
        const selected = doc.segments.find(s => s.start >= 600) || doc.segments[5]
        const stamp = time => `${Math.floor(time / 60)}:${String(Math.floor(time % 60)).padStart(2, '0')}`
        const open = () => page.goto(`${BASE}/ui/learn?episode=${first.episode_id}&at=${selected.start}`)
        await open()
        await page.getByText("AI answers are off.", {exact: false}).waitFor()
        const question = page.getByRole('textbox', {name: 'Question about this episode', exact: true})
        await question.fill('What was just explained?')
        await page.getByRole('button', {name: `Find passages at ${stamp(selected.start)}`, exact: true}).click()
        await page.getByText('Source passages found. No AI answer was generated.', {exact: true}).waitFor()
        assert(await page.getByRole('button', {name: `Play question source at ${stamp(selected.start)}`, exact: true}).count())
        report.checks.push('Unconfigured service returns actual source passages and no invented answer')

        // Route only answer endpoints to a real temporary API using a labelled fixture provider.
        const forward = async route => {
            const request = route.request()
            const url = new URL(request.url())
            const response = await fetch(FIXTURE + url.pathname, {method: request.method(),
                headers: {'Content-Type': 'application/json'}, body: request.method() === 'POST' ? request.postData() : undefined})
            await route.fulfill({status: response.status, contentType: 'application/json', body: await response.text()})
        }
        await page.route('**/companion/answers/status', forward)
        await page.route('**/companion/episodes/*/answer', forward)
        await open()
        const askButton = () => page.getByRole('button', {name: /^Ask at /})
        const ask = async text => {
            await question.fill(text)
            const response = page.waitForResponse(r => r.url().endsWith('/answer'))
            await askButton().click()
            return (await response).json()
        }
        const result = await ask('What was just explained?')
        await page.getByText(/^AI answer ·/).waitFor()
        assert.equal(result.episode_id, first.episode_id)
        assert.equal(result.position, selected.start)
        assert(result.passages.every(p => p.episode_id === first.episode_id))
        const source = result.passages.find(p => p.id === result.claims[0].citations[0])
        assert.equal(source.start, selected.start)
        await page.getByRole('button', {name: `Play answer source at ${stamp(source.start)}`, exact: true}).click()
        await page.waitForFunction(time => {
            const audio = document.querySelector('#audio-player')
            return audio?.readyState >= 2 && !audio.paused && Math.abs(audio.currentTime - time) < 12
        }, source.start, {timeout: 45000})
        await page.locator('.listen-player').getByRole('button', {name: 'Pause', exact: true}).click()
        assert(await page.getByText(`“What was just explained?” · Asked at ${stamp(selected.start)}`, {exact: true}).isVisible())
        report.checks.push('Configured answer uses the selected episode and fractional source time; citation plays original audio')
        await page.locator('.ask-panel').screenshot({path: path.join(EVIDENCE, 'answers-desktop.png')})

        await ask('Simulate a service failure')
        await page.getByRole('alert').filter({hasText: 'answer service is unavailable'}).waitFor()
        assert.equal(await question.inputValue(), 'Simulate a service failure')
        report.checks.push('Provider failure preserves the question and allows retry')
        const invalid = await ask('Simulate an invalid citation')
        assert(invalid.detail.includes('could not be linked'))
        assert.equal(await page.locator('.answer-claim').count(), 0)
        report.checks.push('Invented citation is rejected by the real API and never shown as an answer')
        await ask('Simulate insufficient evidence')
        await page.getByText(/These passages do not provide enough evidence/).waitFor()
        report.checks.push('Insufficient evidence remains explicit')

        await page.setViewportSize({width: 390, height: 844})
        await page.getByRole('group', {name: 'Episode workspace'}).getByRole('button', {name: 'Ask', exact: true}).click()
        await ask('What was just explained?')
        await page.getByText(/^AI answer ·/).waitFor()
        assert(await question.isVisible())
        assert.equal(await page.locator('.transcript-panel').isVisible(), false)
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
        const mobileSource = page.getByRole('button', {name: `Play answer source at ${stamp(source.start)}`, exact: true})
        await mobileSource.click()
        await page.waitForFunction(() => !document.querySelector('#audio-player').paused)
        await page.locator('.listen-player').getByRole('button', {name: 'Pause', exact: true}).click()
        await page.locator('.ask-panel').getByRole('button', {name: 'Use current time', exact: true}).click()
        await mobileSource.scrollIntoViewIfNeeded()
        await page.screenshot({path: path.join(EVIDENCE, 'answers-mobile.png')})
        report.checks.push('Mobile Ask tab and source playback work without overflow; selected time can follow playback again')

        await question.fill('Simulate a slow answer')
        const pending = page.waitForRequest(r => r.url().endsWith('/answer'))
        await askButton().click()
        await pending
        // SPA navigation while the old episode request is in flight.
        await page.evaluate(id => {
            history.pushState({}, '', '/ui/learn?episode=' + id)
            dispatchEvent(new PopStateEvent('popstate'))
        }, second.episode_id)
        await page.waitForFunction(name => document.querySelector('.workspace-heading h1')?.textContent === name, second.name)
        await pause(2400)
        assert.equal(await page.locator('.episode-answer').count(), 0)
        assert.equal(await question.inputValue(), '')
        report.checks.push('Changing episodes clears question state and discards the previous late answer')
        assert.deepEqual(report.errors, [])
        report.status = 'PASS'
    } catch (error) {report.failure = error.stack; process.exitCode = 1}
    finally {
        await browser?.close()
        fixture.kill('SIGTERM')
        fs.mkdirSync(EVIDENCE, {recursive: true})
        fs.writeFileSync(path.join(EVIDENCE, 'answers-report.json'), JSON.stringify(report, null, 2))
        console.log(JSON.stringify(report, null, 2))
    }
})().catch(error => {console.error(error); process.exit(1)})
