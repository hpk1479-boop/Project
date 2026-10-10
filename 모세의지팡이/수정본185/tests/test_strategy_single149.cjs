/* Current HTML + unified.js in offline Edge; no Part1 settings, engines, jobs, or AI services run. */
'use strict';
const assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path');
const {chromium}=require(process.argv[2]), proof=process.argv[3];
const fixture=JSON.parse(fs.readFileSync(0,'utf8')), root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'Part3/web/index.html'),'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
const checks=[],errors=[],external=[];
(async()=>{
  fs.mkdirSync(proof,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true,timeout:20000});
  try {
    const page=await browser.newPage({viewport:{width:1280,height:1000}});
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',route=>{external.push(route.request().url());return route.abort();});
    await page.setContent(html);
    await page.addStyleTag({content:fs.readFileSync(path.join(root,'Part3/web/style.css'),'utf8')});
    await page.evaluate(fixture=>{
      window.setInterval=()=>0;window.calls=[];
      const names=Object.keys(fixture.profiles);
      const rows=Object.fromEntries(names.map(name=>[name,{name,enabled:true,trigger:null,time_filters:null,
        default_trigger:fixture.profiles[name].oz?'올존':null}]));
      const backtest={symbol:'XAUUSD+',symbols:['XAUUSD+'],start:'2026-09-01',end:'2026-09-08',
        mode:'BAR',warehouse_set:true,specials:names,strategy_names:{},special_settings:rows,
        default_triggers:Object.fromEntries(names.map(name=>[name,rows[name].default_trigger])),
        trigger_choices:['올존'],spread_points:{},virtual_entry:null,virtual_entry_target:null,
        virtual_profiles:fixture.profiles,virtual_ladder:fixture.ladder};
      window.api=async(route,request)=>{
        calls.push({route,write:request!==undefined});
        if(route==='mo/backtest/options') return structuredClone(backtest);
        if(route==='mo/live/specials') return {items:structuredClone(rows),trigger_choices:['올존']};
        if(route.startsWith('mo/live/status')) return {modules:{},lines:[]};
        if(route==='ai/models') return {available:true,models:['offline-test-model']};
        if(route==='ai/gguf-models') return {models:[]};
        if(route.startsWith('ai/gemini-models')) return {available:true,models:[]};
        throw Error('Unexpected API: '+route);
      };
    },fixture);
    for(const name of ['backtest_dashboard.js','unified.js'])
      await page.addScriptTag({content:fs.readFileSync(path.join(root,'Part3/web',name),'utf8')});
    const popup=page.locator('#strategy-settings-dialog');
    const selected=()=>page.locator('#strategy-settings-cards .moses-strategy-toggle input:checked').evaluateAll(
      rows=>rows.map(row=>row.closest('[data-strategy]').dataset.strategy));
    const choose=name=>page.locator(`[data-strategy="${name}"] .moses-strategy-toggle input`).check();
    const open=async()=>{
      await page.locator('#mo-bt-edit-specials').click();
      await page.waitForFunction(()=>document.querySelector('#strategy-settings-dialog').open&&!moStrategyPopup.busy);
    };
    const apply=async()=>{
      await page.locator('#strategy-settings-apply').click();
      await page.waitForFunction(()=>!document.querySelector('#strategy-settings-dialog').open);
    };
    await page.locator('[data-moses-view="backtest"]').click();
    await page.waitForFunction(()=>document.querySelectorAll('#mo-bt-specials [data-special]').length===3);
    assert.deepEqual(await page.evaluate(()=>moBacktestRequest().specials),['SPECIAL9']);
    checks.push('다중 저장값 복원: 체크순서 기록이 없는 목록의 마지막 선택 하나만 유지');
    await page.locator('[data-bt-result="ALERT_ONLY"]').click();await open();
    await choose('SPECIAL8');await choose('SPECIAL1');
    assert.deepEqual(await selected(),['SPECIAL1']);
    assert.equal(await page.locator('#strategy-list-select-all').isDisabled(),true);
    await popup.screenshot({path:path.join(proof,'backtest_alert_single149.png')});
    await apply();
    assert.deepEqual(await page.evaluate(()=>moBacktestRequest().specials),['SPECIAL1']);
    await open();assert.deepEqual(await selected(),['SPECIAL1']);await page.locator('#strategy-settings-cancel').click();
    checks.push('ALERT_ONLY: 마지막 체크 하나만 유지, 전체선택 차단, 적용·재열기·요청까지 같은 전략');
    await page.locator('[data-bt-result="VIRTUAL_ENTRY"]').click();await open();
    await choose('SPECIAL8');await choose('SPECIAL9');assert.deepEqual(await selected(),['SPECIAL9']);await apply();
    assert.deepEqual(await page.evaluate(()=>moBacktestRequest().specials),['SPECIAL9']);
    checks.push('VIRTUAL_ENTRY: 마지막 체크 하나만 유지하고 해당 전략으로 요청');
    await page.locator('[data-bt-result="BUILD_ONLY"]').click();await open();
    await choose('SPECIAL1');await choose('SPECIAL8');assert.deepEqual(await selected(),['SPECIAL8']);await apply();
    await page.evaluate(()=>{
      const first=document.querySelector('#mo-bt-specials [data-special="SPECIAL1"] .mo-special-enabled');
      first.checked=true;first.dispatchEvent(new Event('change',{bubbles:true}));
    });
    assert.deepEqual(await page.evaluate(()=>moBacktestRequest().specials),['SPECIAL1']);
    await page.evaluate(()=>document.querySelectorAll('#mo-bt-specials .mo-special-enabled').forEach(row=>{row.checked=true;}));
    assert.deepEqual(await page.evaluate(()=>moBacktestRequest().specials),['SPECIAL9']);
    checks.push('구축모드·숨은 컨트롤 change·요청 직전 다중값에서도 단일선택 유지');
    await page.locator('[data-moses-view="live"]').click();await page.locator('#live-open-settings').click();
    await page.waitForFunction(()=>document.querySelector('#strategy-settings-dialog').open&&!moStrategyPopup.busy);
    assert.deepEqual(await selected(),['SPECIAL1','SPECIAL8','SPECIAL9']);
    assert.equal(await page.locator('#strategy-list-select-all').isEnabled(),true);
    await page.locator('#strategy-list-select-all').click();assert.deepEqual(await selected(),[]);
    await choose('SPECIAL1');await choose('SPECIAL8');assert.deepEqual(await selected(),['SPECIAL1','SPECIAL8']);
    await page.locator('#strategy-list-select-all').click();assert.deepEqual(await selected(),['SPECIAL1','SPECIAL8','SPECIAL9']);
    await popup.screenshot({path:path.join(proof,'live_multiple149.png')});
    await page.locator('#strategy-settings-cancel').click();
    assert.deepEqual(await page.locator('#live-specials .mo-special-enabled:checked').evaluateAll(
      rows=>rows.map(row=>row.closest('[data-special]').dataset.special)),['SPECIAL1','SPECIAL8','SPECIAL9']);
    checks.push('LIVE: 기존 복수 체크·전체선택·전체해제 유지, 취소로 설정 변경 없음');
    await page.evaluate(()=>{
      moState.view='settings';mo('#view-live').classList.add('hidden');mo('#view-settings').classList.remove('hidden');
    });
    for(const provider of ['local_gguf','gemini','disabled']) {
      await page.evaluate(async provider=>{await moRenderAISettings(mo('#settings-ai-fields'),{provider});},provider);
      assert.equal(await page.evaluate(()=>calls.filter(item=>item.route==='ai/models').length),0);
    }
    const provider=page.locator('#ai-provider-select');
    for(const name of ['local_gguf','gemini','disabled']) await provider.selectOption(name);
    assert.equal(await page.evaluate(()=>calls.filter(item=>item.route==='ai/models').length),0);
    await provider.selectOption('ollama');
    await page.waitForFunction(()=>document.querySelector('#ai-model-list-status')?.textContent==='모델 1개');
    assert.equal(await page.evaluate(()=>calls.filter(item=>item.route==='ai/models').length),1);
    checks.push('공통 AI: GGUF·Gemini·사용 안 함에서는 Ollama 조회0, Ollama 선택 시에만 조회1');
    assert.deepEqual(await page.evaluate(()=>calls.filter(item=>item.write)),[]);
    assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
    const report={passed:checks.length,checks,errors,external_calls:external};
    fs.writeFileSync(path.join(proof,'strategy_single149.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  } finally {await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
