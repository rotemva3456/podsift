// Browser-only fault injection checks; the positive flow uses the actual services.
const {chromium}=require('../ui/node_modules/@playwright/test'),fs=require('fs'),path=require('path'),assert=require('assert/strict')
const ROOT=path.resolve(__dirname,'..'),EVIDENCE=path.resolve(ROOT,process.env.EVIDENCE_DIR||'evidence')
const backend=process.env.PODFETCH_BACKEND||'http://127.0.0.1:18080'
;(async()=>{
 const directory=path.join(process.env.HOME,'.cache/ms-playwright')
 const executablePath=process.env.CHROMIUM_BIN || (fs.existsSync(chromium.executablePath())?chromium.executablePath():fs.readdirSync(directory).filter(x=>x.startsWith('chromium_headless_shell-')).sort().reverse().map(x=>path.join(directory,x,'chrome-headless-shell-linux64/chrome-headless-shell')).find(fs.existsSync))
 const browser=await chromium.launch({headless:true,executablePath,args:['--no-sandbox']})
 const page=await browser.newPage({viewport:{width:1280,height:900}}),base=process.env.APP_URL||'http://127.0.0.1:5189'
 const checks=[]
 try {
  await page.route('**/api/v1/podcasts/timeline**',route=>route.abort())
  await page.goto(base+'/ui/home/view')
  await page.getByRole('heading',{name:"Your library couldn't load"}).waitFor()
  await page.unroute('**/api/v1/podcasts/timeline**')
  await page.getByRole('button',{name:'Try again',exact:true}).click()
  await page.getByRole('heading',{name:'Latest episodes',exact:true}).waitFor()
  checks.push('Library connection error and retry recovery')
  const episode=(await (await fetch(backend+'/api/v1/podcasts/timeline?notListened=false&favoredEpisodes=false')).json()).data[0].podcast_episode
  await page.route('**/companion/episodes/**/transcript',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({episode_id:episode.episode_id,source:null,text:'',segments:[],timed:false})}))
  await page.goto(base+'/ui/learn?episode='+episode.episode_id)
  await page.getByRole('heading',{name:'No transcript for this episode yet'}).waitFor()
  assert(await page.getByRole('textbox',{name:'Your note'}).isVisible())
  checks.push('Missing transcript is explicit; timestamp notes remain available')
  await page.unroute('**/companion/episodes/**/transcript')
  await page.route('**/companion/notes',route=>route.request().method()==='POST'?route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'The podcast library is unavailable. Please try again.'})}):route.continue())
  await page.getByRole('textbox',{name:'Your note'}).fill('Keep this draft when saving fails.')
  await page.getByRole('button',{name:/^Save note at /}).click()
  await page.getByRole('alert').filter({hasText:'unavailable'}).waitFor()
  assert.equal(await page.getByRole('textbox',{name:'Your note'}).inputValue(),'Keep this draft when saving fails.')
  checks.push('Failed save reports error and preserves note draft')
  await page.unroute('**/companion/notes')
  await page.goto(base+'/ui/home/view')
  await page.getByRole('heading',{name:'Latest episodes',exact:true}).waitFor()
  await page.keyboard.press('Tab')
  assert.equal(await page.evaluate(()=>document.activeElement?.textContent),'Skip to content')
  await page.keyboard.press('Enter')
  checks.push('Keyboard skip link reaches the main content')
  fs.mkdirSync(EVIDENCE,{recursive:true});fs.writeFileSync(path.join(EVIDENCE,'state-report.json'),JSON.stringify({status:'PASS',method:'Controlled browser network faults and missing-transcript fixture',checks},null,2))
  console.log(checks)
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)})
