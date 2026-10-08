/* Real current settings UI; mocked APIs with non-secret sample values. No config files written. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.argv[2]);
const root=path.resolve(__dirname,'../..'),evidence=path.join((process.env.MOSES_EVIDENCE_ROOT || (process.env.MOSES_EVIDENCE_ROOT || path.join(root, '검증결과'))), '설정한글화59');
fs.mkdirSync(evidence,{recursive:true});
const liveKeys=['TELEGRAM_TOKEN','TELEGRAM_CHAT_ID','TELEGRAM_COMMAND_CHAT_IDS','GEMINI_API_KEY',
 'GEMINI_FALLBACK_ENABLED','GEMINI_MODEL','GEMINI_TIMEOUT_SEC','STAFF_BIND_ENDPOINT','STAFF_ENDPOINT',
 'MANAGER_ALERT_ENDPOINT','ZMQ_TIMEOUT_MS','OZ_COMMAND_FILE','STAFF_PIPE_NAME','STAFF_BARS','STAFF_STALE_SEC',
 'SYMBOLS','STAFF_ALLOWED_TIMEFRAMES','WONBI_SIGMA','ASIA','LONDON','NEWYORK',
 'MAIN_ASIA','MAIN_LONDON','MAIN_NEWYORK','OPENING_ASIA','OPENING_LONDON','OPENING_NEWYORK',
 'CONFIG_RELOAD_SEC','COMPOSER_POLL_SEC','ENGINE_SUBSCRIPTION_REFRESH_SEC','ECONOMY_ENABLED','ECONOMY_FETCH_SEC',
 'ECONOMY_POLL_SEC','POINT_XAUUSD+'];
const secrets=new Set(liveKeys.slice(0,4));
function sample(key) {
 if(secrets.has(key))return '';
 if(key.endsWith('_ENABLED'))return key==='ECONOMY_ENABLED'?'false':'true';
 if(key.endsWith('_SEC')||key.endsWith('_MS')||key.endsWith('_BARS'))return '10';
 if(key==='WONBI_SIGMA')return '2';if(key.startsWith('POINT_'))return '0.01';
 if(key.includes('ENDPOINT'))return 'tcp://127.0.0.1:5555';
 if(key.endsWith('_FILE'))return 'projects/sample.json';
 if(key==='STAFF_PIPE_NAME')return '\\\\.\\pipe\\Sample';
 if(key==='STAFF_ALLOWED_TIMEFRAMES')return '1m,15m,1h';
 if(key==='GEMINI_MODEL')return 'gemini-sample';
 if(key.includes('SYMBOLS'))return 'XAUUSD+,NAS100';
 return '0900-1800';
}
let config={live:liveKeys.map(key=>({key,secret:secrets.has(key),configured:secrets.has(key),value:sample(key)})),
 part2:{cores:null,overlap_trading_days:3,work_size:'MONTH',capture_start:'keyframe',oz_evaluation:'selected',broker_symbols:{'XAUUSD+':'GOLD'}},
 connections:{warehouse:'창고',python_executable:''},ai:{provider:'ollama',model:'qwen3:8b',base_url:'http://127.0.0.1:11434',timeout:90}};
const results=[],requests=[];let rejectSave='';
const pass=name=>results.push({name,passed:true});

(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>route.abort());
  await page.route('http://127.0.0.1:8763/**',async route=>{
   const name=new URL(route.request().url()).pathname;
   if(!name.startsWith('/api/')) {
    const file=name==='/'?'index.html':name.slice(1);
    return route.fulfill({body:fs.readFileSync(path.join(root,'Part3/web',file)),
     contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html; charset=utf-8'});
   }
   const data=route.request().postDataJSON();requests.push({name,data});let body={ok:true};
   if(name==='/api/init')body={connections:{warehouse:''},recent:[]};
   else if(name==='/api/mo/live/status')body={modules:{},lines:[]};
   else if(name==='/api/ai/settings')body={settings:config.ai,base_url:config.ai.base_url};
   else if(name==='/api/ai/models')body={available:true,models:['qwen3.5:4b','qwen3:8b']};
   else if(name==='/api/mo/settings'&&!data)body=structuredClone(config);
   else if(name==='/api/mo/settings/telegram') {
    const row=config.live.find(x=>x.key===data.key);row.configured=data.value!==null;
    body={ok:true,key:data.key,configured:row.configured,message:'확인 및 저장 완료'};
   }
   else if(name==='/api/mo/settings') {
    if(rejectSave)return route.fulfill({status:400,json:{error:rejectSave}});
    if(data.group==='live')for(const [key,value] of Object.entries(data.changes)) {
     const row=config.live.find(x=>x.key===key);row.value=row.secret?'':value;
     if(row.secret)row.configured=value!==null;
    } else Object.assign(config[data.group],data.changes);
    body={ok:true,message:'저장 완료'};
   }
   return route.fulfill({json:body});
  });
  await page.goto('http://127.0.0.1:8763/#token=settings-test');await page.waitForFunction(()=>!!state.meta);
  await page.evaluate(()=>moView('settings'));
  await page.waitForSelector('#ai-model-select:not(:disabled)');
  // These contract checks exercise every field; current settings sections start collapsed.
  await page.evaluate(()=>{
   const expand=()=>document.querySelectorAll('#view-settings details').forEach(section=>{section.open=true;});
   expand();new MutationObserver(expand).observe(document.querySelector('#view-settings'),{childList:true,subtree:true});
  });
  const screen=await page.locator('#view-settings').innerText();
  for(const key of liveKeys)assert.equal(screen.includes(key),false,key);
  for(const key of Object.keys(config.part2))assert.equal(screen.includes(key),false,key);
  for(const title of ['텔레그램 봇 연결 키','경제지표 알림 사용','원비 밴드 폭 배수','아시아 주요 거래 시간',
   '동시에 사용할 작업 수','프로그램과 MT5의 종목명 연결','데이터 보관 폴더','라이브 감시 설정 저장'])assert.ok(screen.includes(title),title);
  for(const raw of ['MONTH','FORTNIGHT','keyframe','beginning','true','false','broker_symbols','{"XAUUSD+"'])assert.equal(screen.includes(raw),false,raw);
  assert.deepEqual(await page.locator('#settings-live-fields [data-setting]').evaluateAll(fields=>fields.map(field=>field.dataset.setting).sort()), [...liveKeys].sort());
  pass('Every supplied live key appears exactly once with Korean names and explanations; canonical values and JSON are not displayed');

  const descriptions=await page.locator('#view-settings [data-setting]:not([type="hidden"])').evaluateAll(fields=>fields.map(field=>{
   const helps=(field.getAttribute('aria-describedby')||'').split(/\s+/).map(id=>document.getElementById(id));return helps.some(help=>!!help?.textContent) && field.closest('label')?.firstChild.textContent.length>0;
  }));assert.ok(descriptions.every(Boolean));
  for(const key of secrets) {
   const field=page.locator(`[data-setting="${key}"]`);
   assert.equal(await field.inputValue(),'');assert.equal(await field.getAttribute('type'),'password');
   assert.match(await field.getAttribute('placeholder'),/変更|변경/);
  }
  pass('Every setting has an accessible explanation; stored secrets remain hidden and are never filled into inputs');

  for(const group of ['live','part2','connections','ai']) {
   await page.locator(`[data-save-settings="${group}"]`).click();
   await page.waitForFunction(()=>document.querySelector('#settings-message').textContent==='변경한 값이 없습니다.');
  }
  assert.equal(requests.filter(x=>x.name==='/api/mo/settings'&&x.data).length,0);
  pass('Opening or saving unchanged settings sends no write request');

  await page.locator('[data-setting="ECONOMY_ENABLED"]').selectOption('true');
  await page.locator('[data-save-settings="live"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-setting="ECONOMY_ENABLED"]').dataset.original==='true');
  assert.deepEqual(requests.filter(x=>x.name==='/api/mo/settings'&&x.data).at(-1).data,{group:'live',changes:{ECONOMY_ENABLED:'true'}});
  assert.deepEqual(await page.locator('[data-setting="ECONOMY_ENABLED"] option').allTextContents(),['켜기','끄기']);
  pass('Korean on/off selection saves the original live key and boolean string without other edits');

  await page.locator('[data-setting="work_size"]').selectOption('WEEK');
  await page.locator('[data-setting="capture_start"]').selectOption('beginning');
  await page.locator('[data-setting="oz_evaluation"]').selectOption('all');
  await page.locator('[data-save-settings="part2"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-setting="work_size"]').dataset.original==='WEEK');
  assert.deepEqual(requests.filter(x=>x.name==='/api/mo/settings'&&x.data).at(-1).data,{group:'part2',changes:{work_size:'WEEK',capture_start:'beginning',oz_evaluation:'all'}});
  assert.deepEqual(await page.locator('[data-setting="work_size"] option').allTextContents(),['자동','한 달씩','2주씩','1주씩','하루씩']);
  pass('Korean work size, reading start and timeframes keep the same stored enum values');

  await page.locator('[data-setting="cores"]').fill('4');await page.locator('[data-setting="overlap_trading_days"]').fill('5');
  await page.locator('[data-setting="WONBI_SIGMA"]').fill('2.5');
  await page.locator('[data-save-settings="part2"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-setting="cores"]').dataset.original==='4');
  assert.deepEqual(requests.filter(x=>x.name==='/api/mo/settings'&&x.data).at(-1).data,{group:'part2',changes:{cores:4,overlap_trading_days:5}});
  // Reload intentionally resets edits in other groups, following the existing save behavior.
  await page.locator('[data-setting="WONBI_SIGMA"]').fill('2.5');await page.locator('[data-save-settings="live"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-setting="WONBI_SIGMA"]').dataset.original==='2.5');
  assert.deepEqual(requests.filter(x=>x.name==='/api/mo/settings'&&x.data).at(-1).data,{group:'live',changes:{WONBI_SIGMA:'2.5'}});
  await page.locator('[data-setting="cores"]').fill('');await page.locator('[data-save-settings="part2"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-setting="cores"]').dataset.original==='');
  assert.equal(requests.filter(x=>x.name==='/api/mo/settings'&&x.data).at(-1).data.changes.cores,null);
  pass('Numbers retain existing live-string/backtest-number types; clearing worker count still saves automatic null');

  await page.locator('.moses-symbol-row').first().getByLabel('MT5 종목명',{exact:true}).fill('GOLD.pro');
  await page.getByRole('button',{name:'종목 연결 추가',exact:true}).click();
  await page.locator('.moses-symbol-row').last().getByLabel('프로그램 종목명',{exact:true}).fill('NAS100');
  await page.locator('.moses-symbol-row').last().getByLabel('MT5 종목명',{exact:true}).fill('US100');
  await page.locator('[data-save-settings="part2"]').click();
  await page.waitForFunction(()=>JSON.parse(document.querySelector('[data-setting="broker_symbols"]').dataset.original).NAS100==='US100');
  assert.deepEqual(requests.filter(x=>x.name==='/api/mo/settings'&&x.data).at(-1).data,{group:'part2',changes:{broker_symbols:{'XAUUSD+':'GOLD.pro',NAS100:'US100'}}});
  pass('Named symbol rows replace JSON editing and save the original logical-to-broker mapping');

  const writes=requests.filter(x=>x.name==='/api/mo/settings'&&x.data).length;
  await page.getByRole('button',{name:'종목 연결 추가',exact:true}).click();
  await page.locator('.moses-symbol-row').last().getByLabel('프로그램 종목명',{exact:true}).fill('NAS100');
  await page.locator('[data-save-settings="part2"]').click();
  await page.waitForFunction(()=>document.querySelector('#settings-message').textContent.includes('모두 입력'));
  await page.locator('.moses-symbol-row').last().getByLabel('MT5 종목명',{exact:true}).fill('US100x');
  await page.locator('[data-save-settings="part2"]').click();
  await page.waitForFunction(()=>document.querySelector('#settings-message').textContent.includes('두 번 연결'));
  assert.equal(requests.filter(x=>x.name==='/api/mo/settings'&&x.data).length,writes);
  await page.locator('.moses-symbol-row').last().getByRole('button',{name:'이 종목 연결 삭제'}).click();
  await page.locator('.moses-symbol-row').last().getByRole('button',{name:'이 종목 연결 삭제'}).click();
  await page.locator('[data-save-settings="part2"]').click();
  await page.waitForFunction(()=>!Object.hasOwn(JSON.parse(document.querySelector('[data-setting="broker_symbols"]').dataset.original),'NAS100'));
  assert.deepEqual(requests.filter(x=>x.name==='/api/mo/settings'&&x.data).at(-1).data.changes.broker_symbols,{'XAUUSD+':'GOLD.pro'});
  pass('Incomplete and duplicate mappings show Korean errors without saving; removal preserves remaining entries');

  await page.locator('[data-setting="TELEGRAM_TOKEN"]').fill('sample-replacement');
  await page.locator('[data-setting="TELEGRAM_TOKEN"]').locator('xpath=ancestor::label').getByRole('button',{name:'텔레그램 봇 연결 키 확인',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[data-setting="TELEGRAM_TOKEN"]').value==='');
  assert.deepEqual(requests.filter(x=>x.name==='/api/mo/settings/telegram').at(-1).data,{key:'TELEGRAM_TOKEN',value:'sample-replacement'});
  const alertField=page.locator('[data-setting="TELEGRAM_CHAT_ID"]').locator('xpath=ancestor::label');
  await alertField.locator('.mo-secret-clear').check();
  await alertField.getByRole('button',{name:'알림을 받을 채팅방 확인',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[data-setting="TELEGRAM_CHAT_ID"]').placeholder==='미설정');
  assert.deepEqual(requests.filter(x=>x.name==='/api/mo/settings/telegram').at(-1).data,{key:'TELEGRAM_CHAT_ID',value:null});
  pass('Telegram replacement and explicit clear use individual confirmation while other settings retain bulk save');

  rejectSave='STAFF_PIPE_NAME: Windows Named Pipe 형식이어야 합니다.';
  await page.locator('[data-setting="STAFF_PIPE_NAME"]').fill('bad');await page.locator('[data-save-settings="live"]').click();
  await page.waitForFunction(()=>document.querySelector('#settings-message').textContent.includes('MT5 데이터 연결 통로'));
  assert.doesNotMatch(await page.locator('#settings-message').innerText(),/STAFF_PIPE_NAME|Named Pipe/);
  rejectSave='';await page.evaluate(()=>moSettingsLoad());
  pass('Settings validation errors use the visible Korean field name while backend validation remains unchanged');

  config.part2.work_size='FUTURE_VALUE';await page.evaluate(()=>moSettingsLoad());
  assert.equal(await page.locator('[data-setting="work_size"]').inputValue(),'FUTURE_VALUE');
  assert.equal(await page.locator('[data-setting="work_size"] option:checked').innerText(),'현재 저장된 값');
  const count=requests.filter(x=>x.name==='/api/mo/settings'&&x.data).length;
  await page.locator('[data-save-settings="part2"]').click();
  await page.waitForFunction(()=>document.querySelector('#settings-message').textContent==='변경한 값이 없습니다.');
  assert.equal(requests.filter(x=>x.name==='/api/mo/settings'&&x.data).length,count);
  config.part2.work_size='WEEK';await page.evaluate(()=>moSettingsLoad());
  pass('An unfamiliar stored value is retained without an automatic conversion or save');

  const special=await page.evaluate(()=>{
   const box=moSpecialCard('SPECIAL1',{name:'전략 1',enabled:true,default_trigger:'일반 브레이커 올존',trigger:'',
    time_filters:{MAIN_ASIA:{enabled:true,start:'0900',end:'1200'},MAIN_LONDON:{enabled:false},MAIN_NEWYORK:{enabled:false}}},
    ['일반 올존','무지성 올존','일반 브레이커 올존','무지성 브레이커 올존']);
   document.querySelector('#live-specials').replaceChildren(box);
   return {text:box.textContent,placeholders:[...box.querySelectorAll('input[type="text"]')].map(x=>x.placeholder),
    canonical:moReadSpecialCards('#live-specials')};
  });
  for(const word of ['전략 1','최종 알림 조건','아시아','런던','뉴욕'])assert.ok(special.text.includes(word),word);
  assert.doesNotMatch(special.text,/전략에 지정된 거래시간/);
  assert.doesNotMatch(special.text,/ASIA|LONDON|NEWYORK|코드 기본값/);
  assert.ok(special.placeholders.every(x=>/시작|종료/.test(x)&&!x.includes('HHMM')));
  assert.equal(special.canonical.SPECIAL1.enabled,true);
  assert.equal(special.canonical.SPECIAL1.time_filters.MAIN_ASIA.start,'0900');
  assert.equal(special.canonical.SPECIAL1.time_filters.MAIN_ASIA.end,'1200');
  pass('Live and backtest strategy-setting cards use Korean sessions and alarm labels with unchanged canonical selection');

  await page.screenshot({path:path.join(evidence,'settings_desktop.png'),fullPage:true});
  await page.locator('#settings-part2-fields').locator('..').screenshot({path:path.join(evidence,'backtest_settings.png')});
  for(const width of [900,600]) {
   await page.setViewportSize({width,height:900});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   assert.equal(await page.getByRole('button',{name:'종목 연결 추가',exact:true}).isVisible(),true);
  }
  await page.screenshot({path:path.join(evidence,'settings_mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  pass('Korean descriptions, symbol rows and save controls fit desktop and narrow screens without horizontal overflow');
  await page.close();
 } finally {
  await browser.close();fs.writeFileSync(path.join(evidence,'ui_tests.json'),JSON.stringify({tests:results.length,results,
   environment:'full current page in isolated Edge; API fixtures contain no real secrets; no setting files changed'},null,2)+'\n');
 }
 console.log(`PASS ${results.length} Korean settings UI tests`);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
