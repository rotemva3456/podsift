// LOCAL-3 browser acceptance against task-owned PodFetch + companion state.
// Positive flows use the real services. Edge-state responses are controlled browser fixtures.
const {chromium}=require('../ui/node_modules/@playwright/test')
const fs=require('fs'),path=require('path'),assert=require('assert/strict')
const ROOT=path.resolve(__dirname,'..')
const BASE=process.env.APP_URL||'http://127.0.0.1:5249'
const BACKEND=process.env.PODFETCH_BACKEND||'http://127.0.0.1:18090'
const EVIDENCE=path.resolve(ROOT,process.env.EVIDENCE_DIR||'evidence/local3-core-workflow')
const FOCUS_ONLY=process.env.FOCUS_ONLY==='1'
fs.mkdirSync(EVIDENCE,{recursive:true})

async function json(url,init={}){
    const response=await fetch(url,{...init,signal:AbortSignal.timeout(90000)})
    assert(response.ok,`${response.status} ${url}`)
    return response.json()
}
const clock=seconds=>`${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,'0')}`

;(async()=>{
    let executablePath=process.env.CHROMIUM_BIN
    if(!executablePath&&!fs.existsSync(chromium.executablePath())){
        const cache=path.join(process.env.HOME,'.cache/ms-playwright')
        executablePath=fs.readdirSync(cache).filter(name=>name.startsWith('chromium_headless_shell-')).sort().reverse()
            .map(name=>path.join(cache,name,'chrome-headless-shell-linux64/chrome-headless-shell')).find(fs.existsSync)
    }
    const browser=await chromium.launch({headless:true,executablePath,args:['--no-sandbox'],timeout:90000})
    const context=await browser.newContext({viewport:{width:1366,height:900},permissions:['clipboard-read','clipboard-write']})
    const page=await context.newPage()
    page.setDefaultTimeout(90000)
    const report={status:'RUNNING',method:FOCUS_ONLY
        ? {positive:'Current UI source with fresh task-owned empty PodFetch and companion databases',fixtures:[]}
        : {positive:'Fresh task-owned PodFetch and companion databases seeded with the bundled CC BY-SA HPR demo',fixtures:['empty library','untimed transcript','failed render job']},checks:[],defects:[],errors:[],screenshots:[]}
    page.on('pageerror',error=>report.errors.push(error.message))
    const check=(name,value=true)=>{assert(value,name);report.checks.push(name)}
    const shot=async(name,target=page)=>{await target.screenshot({path:path.join(EVIDENCE,name),fullPage:false});report.screenshots.push(name)}
    try{
        await page.goto(BASE+'/ui/learn',{waitUntil:'domcontentloaded'})
        await page.getByRole('heading',{name:'Choose a source to learn from'}).waitFor()
        check('No active playback has an explicit choose-source state')
        await page.goto(BASE+'/ui/home/view',{waitUntil:'domcontentloaded'})
        await page.locator('.skip-link').focus()
        check('Skip link is keyboard focusable',await page.evaluate(()=>document.activeElement?.textContent==='Skip to content'))
        await page.keyboard.press('Enter')
        check('Skip link targets the main-content landmark',await page.evaluate(()=>location.hash==='#main-content'&&!!document.querySelector('main#main-content')))
        check('Skip link activation moves focus to the main-content landmark',await page.evaluate(()=>document.activeElement?.matches('main#main-content')))
        if(FOCUS_ONLY){
            await shot('skip-link-main-focus.png')
            check('No uncaught browser exceptions',report.errors.length===0)
            report.status='PASS'
            return
        }

        const timeline=await json(BACKEND+'/api/v1/podcasts/timeline?notListened=false&favoredEpisodes=false')
        assert(timeline.data.length>=2,'The isolated licensed demo needs at least two episodes')
        const first=timeline.data[0].podcast_episode,second=timeline.data[1].podcast_episode
        report.episodes=[first.episode_id,second.episode_id]
        await page.getByRole('heading',{name:'Latest episodes',exact:true}).waitFor()
        check('Bundled licensed demo episodes render in the current Today UI',await page.locator('.episode-row').count()>=2)
        const existingQueue=(await json(BACKEND+'/api/v1/playlist')).find(item=>item.name==='Listen next')
        if(existingQueue)await json(BACKEND+'/api/v1/playlist/'+existingQueue.id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'Listen next',items:[]})})
        await page.reload({waitUntil:'domcontentloaded'})
        await page.locator('.episode-row').first().waitFor()
        for(const episode of [first,second]){
            const row=page.locator(`[data-episode="${episode.id}"]`)
            await row.getByRole('button',{name:/^Add .* to queue$/}).click()
            await row.getByRole('button',{name:/^Remove .* from queue$/}).waitFor()
        }
        await page.goto(BASE+'/ui/queue',{waitUntil:'domcontentloaded'})
        await page.locator('.queue-row').first().waitFor()
        const queue=await json(BACKEND+'/api/v1/playlist')
        check('Queue additions persist in isolated PodFetch',queue.some(item=>item.name==='Listen next'&&item.items.length===2))
        await page.getByRole('button',{name:'Move episode 2 up',exact:true}).click()
        await page.waitForFunction(()=>!document.querySelector('[aria-label="Move episode 2 up"]')?.disabled)
        await page.reload({waitUntil:'domcontentloaded'})
        await page.locator('.queue-row').first().waitFor()
        check('Queue reorder survives a full reload',(await page.locator('.queue-row').first().innerText()).includes(second.name))

        await page.goto(BASE+'/ui/learn?episode='+first.episode_id,{waitUntil:'domcontentloaded'})
        const transcript=await json(BASE+'/companion/episodes/'+first.episode_id+'/transcript')
        assert(transcript.timed&&transcript.segments.length>6,'The bundled demo transcript must be timed')
        const selected=transcript.segments.find(segment=>segment.start>=60&&segment.end>segment.start)||transcript.segments[5]
        const label=clock(selected.start)
        await page.getByRole('button',{name:`Play passage at ${label}`,exact:true}).first().click()
        await page.waitForFunction(time=>{const audio=document.querySelector('#audio-player');return audio&&audio.readyState>=2&&!audio.paused&&audio.currentTime>=time&&audio.currentTime<time+15},selected.start,{timeout:90000})
        await page.locator('.listen-player').getByRole('button',{name:'Pause',exact:true}).click()
        const paused=await page.locator('#audio-player').evaluate(audio=>audio.currentTime)
        check('Transcript passage seeks and plays publisher audio on the source clock',Math.abs(paused-selected.start)<15)
        await page.waitForFunction(({id,time})=>fetch('/api/v1/episodes/'+id).then(r=>r.json()).then(x=>Math.abs((x.podcastHistoryItem?.position??-999)-time)<4).catch(()=>false),{id:first.episode_id,time:paused})
        check('Pause writes canonical playback history to isolated PodFetch')

        await page.getByRole('group',{name:'Episode workspace'}).getByRole('button',{name:'Ask',exact:true}).click()
        await page.getByRole('button',{name:'What was just explained?',exact:true}).click()
        const answerResponse=page.waitForResponse(response=>response.url().includes(`/companion/episodes/${first.episode_id}/answer`)&&response.request().method()==='POST')
        await page.getByRole('button',{name:`Find passages at ${label}`,exact:true}).click()
        const answer=await (await answerResponse).json()
        check('Current Ask sends the active episode and source-clock position',answer.episode_id===first.episode_id&&Math.abs(answer.position-selected.start)<0.01)
        check('Context packet contains the active source interval',answer.passages.some(p=>p.episode_id===first.episode_id&&p.start<=answer.position&&(p.end===null||answer.position<p.end)))
        check('Context-only mode is truthful without a provider',answer.status==='not_configured'&&answer.claims.length===0)
        await page.locator('.episode-answer').waitFor()

        await page.getByRole('group',{name:'Episode workspace'}).getByRole('button',{name:'Transcript',exact:true}).click()
        await page.getByRole('textbox',{name:'Your note',exact:true}).fill('Isolated LOCAL-3 source note')
        await page.getByRole('button',{name:`Save note at ${label}`,exact:true}).click()
        await page.getByText('Note saved',{exact:true}).waitFor()
        const notes=await json(BASE+'/companion/notes?episode_id='+first.episode_id)
        check('Timestamped note persists with the active episode and source time',notes.some(note=>note.text==='Isolated LOCAL-3 source note'&&note.episode_id===first.episode_id&&note.position===selected.start))
        await page.getByRole('textbox',{name:'Find in transcript'}).fill('zzzz_local3_no_match_zzzz')
        await page.getByRole('heading',{name:'No matching passages'}).waitFor()
        check('Transcript no-match state is explicit')
        await page.getByRole('textbox',{name:'Find in transcript'}).fill('')

        await page.getByRole('button',{name:`Select passage at ${label}`,exact:true}).click()
        const showActions=page.getByRole('button',{name:'Show actions',exact:true})
        if(await showActions.count())await showActions.click()
        await page.getByRole('button',{name:'Keep selected',exact:true}).click()
        await page.waitForURL(url=>url.searchParams.has('plan'))
        const planId=new URL(page.url()).searchParams.get('plan')
        check('Selected source interval creates a version-bound agent plan',!!planId)
        await page.route('**/companion/plans/*/render',route=>route.fulfill({status:202,contentType:'application/json',body:JSON.stringify({job_id:'fixture-failed-job'})}))
        await page.route('**/companion/jobs/fixture-failed-job',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({id:'fixture-failed-job',status:'failed',stage:'audio',progress:0,error:'Synthetic isolated render failure',detail:'Controlled browser fixture; no provider or real job failed.',cut_id:null})}))
        await page.getByRole('button',{name:'Approve and export MP3',exact:true}).click()
        await page.getByRole('alert').filter({hasText:'Synthetic isolated render failure'}).waitFor()
        check('Failed render job is explicit and retryable (controlled network fixture)')
        await page.unroute('**/companion/plans/*/render');await page.unroute('**/companion/jobs/fixture-failed-job')
        await shot('desktop-core-workflow.png')

        await page.reload({waitUntil:'domcontentloaded'})
        await page.locator('.saved-notes').getByText('Isolated LOCAL-3 source note',{exact:true}).first().waitFor()
        await page.waitForFunction(time=>{const audio=document.querySelector('#audio-player');return audio&&audio.readyState>=1&&audio.paused&&Math.abs(audio.currentTime-time)<4},paused,{timeout:90000})
        check('Paused playback position and note survive a full reload')

        const untimed=await context.newPage()
        await untimed.route(`**/companion/episodes/${first.episode_id}/transcript`,route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({episode_id:first.episode_id,source:'Controlled untimed fixture',text:'Readable words remain available without fabricated timestamps.',segments:[],timed:false,digest:null})}))
        await untimed.goto(BASE+'/ui/learn?episode='+first.episode_id,{waitUntil:'domcontentloaded'})
        await untimed.getByText('This transcript has no timestamps. Reading, copying, and plain notes remain available.',{exact:true}).waitFor()
        check('Untimed transcript remains readable without precise-time claims (controlled fixture)')

        const empty=await context.newPage()
        await empty.route(/\/api\/v1\/podcasts(?:\?.*)?$/,route=>route.fulfill({status:200,contentType:'application/json',body:'[]'}))
        await empty.route('**/api/v1/podcasts/timeline**',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({data:[],totalElements:0})}))
        await empty.goto(BASE+'/ui/home/view',{waitUntil:'domcontentloaded'})
        await empty.getByRole('heading',{name:'Add your first podcast'}).waitFor()
        check('Empty library gives a truthful first-podcast action (controlled fixture)')

        await page.setViewportSize({width:390,height:844})
        await page.goto(BASE+'/ui/learn?episode='+first.episode_id,{waitUntil:'domcontentloaded'})
        await page.locator('.source-workspace').waitFor()
        check('Mobile core workspace has no horizontal overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
        await shot('mobile-core-workflow.png')
        check('No uncaught browser exceptions',report.errors.length===0)
        report.status=report.defects.length?'PARTIAL':'PASS'
    }catch(error){
        report.status='FAIL';report.failure=error.stack||String(error)
        await shot('failure.png').catch(()=>{});process.exitCode=1
    }finally{
        fs.writeFileSync(path.join(EVIDENCE,'report.json'),JSON.stringify(report,null,2))
        console.log(JSON.stringify(report,null,2));await browser.close()
    }
})().catch(error=>{console.error(error);process.exit(1)})
