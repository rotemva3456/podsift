// Self-run against the real local app. Does not substitute API response fixtures.
const {chromium}=require('../ui/node_modules/@playwright/test')
const fs=require('fs'),path=require('path'),assert=require('assert/strict')
const ROOT=path.resolve(__dirname,'..'),BASE=process.env.APP_URL||'http://127.0.0.1:5189'
const backend=process.env.PODFETCH_BACKEND||'http://127.0.0.1:18080'
const EVIDENCE=path.resolve(ROOT,process.env.EVIDENCE_DIR||'evidence'),NOTES_DB=path.resolve(ROOT,process.env.COMPANION_DB||'runtime/notes.db')
fs.mkdirSync(EVIDENCE,{recursive:true})
async function json(url,init){const r=await fetch(url,init);assert(r.ok,`${r.status} ${url}`);return r.json()}
;(async()=>{
    let executablePath=process.env.CHROMIUM_BIN
    if(!executablePath && !fs.existsSync(chromium.executablePath())){
        const directory=path.join(process.env.HOME,'.cache/ms-playwright')
        const candidate=fs.readdirSync(directory).filter(x=>x.startsWith('chromium_headless_shell-')).sort().reverse().map(x=>path.join(directory,x,'chrome-headless-shell-linux64/chrome-headless-shell')).find(fs.existsSync)
        if(candidate)executablePath=candidate
    }
    const browser=await chromium.launch({headless:true,executablePath,args:['--no-sandbox']})
    const context=await browser.newContext({viewport:{width:1440,height:1050},permissions:['clipboard-read','clipboard-write']})
    const page=await context.newPage(),errors=[]
    page.on('pageerror',error=>errors.push(error.message))
    const report={checks:[],errors,screenshots:[]}
    const noteText='Browser verification note: '+require('crypto').randomUUID()
    const check=(name,value)=>{assert(value,name);report.checks.push(name)}
    const shot=async name=>{await page.screenshot({path:path.join(EVIDENCE,name)});report.screenshots.push(name)}
    try{
        const timeline=await json(backend+'/api/v1/podcasts/timeline?notListened=false&favoredEpisodes=false')
        const first=timeline.data[0].podcast_episode,second=timeline.data[1].podcast_episode
        await page.goto(BASE+'/ui/home/view')
        await page.getByRole('heading',{name:'Latest episodes',exact:true}).waitFor()
        check('Real imported shows and episodes render',await page.locator('.episode-row').count()>1)
        const queueBefore=await json(backend+'/api/v1/playlist')
        const playlist=queueBefore.find(p=>p.name==='Listen next')
        const previousItems=playlist?.items.map(item=>({episode:item.podcastEpisode.id}))??[]
        report.previousQueue={id:playlist?.id,items:previousItems}
        if(playlist)await json(backend+'/api/v1/playlist/'+playlist.id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'Listen next',items:[]})})
        await page.reload()
        await page.locator('.episode-row').first().waitFor()
        for(const episode of [first,second]){
            const row=page.locator(`[data-episode="${episode.id}"]`)
            const add=row.getByRole('button',{name:/^Add .* to queue$/})
            if(await add.count())await add.click()
            await row.getByRole('button',{name:/^Remove .* from queue$/}).waitFor()
        }
        await page.locator('.listen-sidebar').getByRole('link',{name:'Listen next',exact:true}).click()
        await page.locator('.queue-row').first().waitFor()
        check('Queue is persisted by PodFetch',(await json(backend+'/api/v1/playlist')).some(p=>p.name==='Listen next'&&p.items.length>=2))
        await page.getByRole('button',{name:'Move episode 2 up',exact:true}).click()
        await page.waitForFunction(()=>!Array.from(document.querySelectorAll('.queue-order button')).some(b=>b.disabled && b.getAttribute('aria-label')==='Move episode 2 up'))
        await page.reload()
        await page.locator('.queue-row').first().waitFor()
        check('Queue order survives reload',(await page.locator('.queue-row').first().innerText()).includes(second.name))
        await page.goto(BASE+'/ui/learn?episode='+first.episode_id)
        await page.locator('.passage-time').first().waitFor({timeout:30000})
        const passages=await json(BASE+'/companion/episodes/'+first.episode_id+'/transcript')
        const selected=passages.segments.find(s=>s.start>=600)||passages.segments[5]
        const label=`${Math.floor(selected.start/60)}:${String(Math.floor(selected.start%60)).padStart(2,'0')}`
        await page.getByRole('button',{name:`Play passage at ${label}`,exact:true}).first().click()
        await page.waitForFunction(time=>{const a=document.querySelector('#audio-player');return a.readyState>=2&&!a.paused&&a.currentTime>=time&&a.currentTime<time+12},selected.start,{timeout:45000})
        await page.locator('.listen-player').getByRole('button',{name:'Pause',exact:true}).click()
        const paused=await page.locator('#audio-player').evaluate(a=>a.currentTime)
        check('Publisher audio plays from selected source timestamp',Math.abs(paused-selected.start)<12)
        await page.getByRole('textbox',{name:'Your note',exact:true}).fill(noteText)
        await page.getByRole('button',{name:`Save note at ${label}`,exact:true}).click()
        await page.getByText('Note saved',{exact:true}).waitFor()
        const saved=await json(BASE+'/companion/notes?episode_id='+first.episode_id)
        const note=saved.find(n=>n.text===noteText)
        check('Saved note uses the selected episode and exact source time',note?.position===selected.start&&note.episode_id===first.episode_id)
        report.verificationNoteId=note.id
        await page.getByRole('textbox',{name:'Find in transcript'}).fill('zzzz_no_match_zzzz')
        await page.getByRole('heading',{name:'No matching passages'}).waitFor()
        report.checks.push('Transcript search has a clear no-match state')
        await page.getByRole('textbox',{name:'Find in transcript'}).fill('')
        await page.reload()
        await page.locator('.saved-notes').getByText(noteText,{exact:true}).waitFor()
        await page.waitForFunction(time=>{const a=document.querySelector('#audio-player');return a.readyState>=1&&a.paused&&Math.abs(a.currentTime-time)<3},paused,{timeout:45000})
        report.checks.push('Note and paused playback position survive reload')
        await shot('desktop-learning.png')
        await page.locator('.listen-sidebar').getByRole('link',{name:'Listen now',exact:true}).click()
        await page.getByRole('heading',{name:'Latest episodes',exact:true}).waitFor()
        await shot('desktop-listening.png')
        await page.locator('.listen-sidebar').getByRole('link',{name:'Find podcasts',exact:true}).click()
        await page.getByRole('textbox',{name:'Search podcasts',exact:true}).fill('Networking')
        await page.getByRole('button',{name:'Search',exact:true}).click()
        await page.locator('.discovery-item').first().waitFor({timeout:45000})
        check('Live directory search returns followable podcasts',await page.getByRole('button',{name:'Follow podcast',exact:true}).count()>0)
        await shot('desktop-search.png')
        await page.setViewportSize({width:390,height:844})
        await page.goto(BASE+'/ui/home/view')
        await page.locator('.episode-row').first().waitFor()
        check('Mobile bottom navigation is visible',await page.getByRole('navigation',{name:'Mobile navigation'}).isVisible())
        check('Mobile has no horizontal page overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
        await shot('mobile-listening.png')
        await page.locator('.listen-player').getByRole('link').first().click()
        await page.locator('.passage-time').first().waitFor()
        check('Mobile transcript is the first panel',await page.locator('.transcript-panel').isVisible())
        await page.getByRole('group',{name:'Episode workspace'}).getByRole('button',{name:/^Notes/}).click()
        check('Mobile notes tab is usable',await page.getByRole('textbox',{name:'Your note'}).isVisible())
        check('Mobile episode workspace has no horizontal overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
        await shot('mobile-learning.png')
        check('No uncaught browser errors',errors.length===0)
        report.status='PASS'
    }catch(error){report.status='FAIL';report.failure=error.stack;await shot('failure.png');process.exitCode=1}
    finally{
        if(report.previousQueue){
            const current=(await json(backend+'/api/v1/playlist')).find(p=>p.name==='Listen next')
            if(current)await json(backend+'/api/v1/playlist/'+current.id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'Listen next',items:report.previousQueue.items})})
        }
        if(report.verificationNoteId){
            const {spawnSync}=require('child_process')
            const cleanup=spawnSync('python3',['-c','import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); db.execute("DELETE FROM notes WHERE id=? AND text=?", (sys.argv[2], sys.argv[3])); db.commit()',NOTES_DB,report.verificationNoteId,noteText])
            assert(cleanup.status===0,'Verification note cleanup failed')
        }
        fs.writeFileSync(path.join(EVIDENCE,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)})
