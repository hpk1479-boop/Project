/* Actual ai_chat + editor DOM, offline replies from the real Python draft checker (수정본148). */
'use strict';
const assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path');
const {chromium}=require(process.argv[2]), proof=process.argv[3];
const fixture=JSON.parse(fs.readFileSync(0,'utf8')), root=path.resolve(__dirname,'..');
const checks=[], errors=[], external=[];
const html='<!doctype html><html lang="ko"><meta charset="utf-8">'+
  ['send','confirm','cancel','reset','research-strategy','research-chat'].map(id=>`<button id="ai-${id}">${id}</button>`).join('')+
  '<textarea id="ai-text"></textarea><div id="ai-pending"><p id="ai-pending-text"></p></div>'+
  '<div id="ai-messages"></div><p id="ai-mode"></p><p id="ai-description"></p><main id="extra"></main></html>';
(async()=>{
  fs.mkdirSync(proof,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true,timeout:20000});
  try {
    const page=await browser.newPage({viewport:{width:1280,height:1100}});
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',route=>route.request().url()==='http://moses.test/'
      ? route.fulfill({contentType:'text/html',body:html})
      : (external.push(route.request().url()),route.abort()));
    await page.goto('http://moses.test/');
    await page.evaluate(fixture=>{
      window.fixture=fixture;window.saves=[];window.applies=[];
      window.part3InvalidateAIRecipe=()=>{};
      window.fetch=async(url,options={})=>{
        const reply=value=>({ok:true,status:200,json:async()=>structuredClone(value)});
        if(url==='/api/ping') return reply({});
        if(url==='/api/ai/settings') return reply({settings:{provider:'gemini'}});
        if(url==='/api/ai/chat') return reply(fixture.initial);
        if(url==='/api/ai/editor') return reply(fixture.contract);
        if(url==='/api/ai/edit') return new Promise(resolve=>{
          window.saves.push({request:JSON.parse(options.body),resolve:value=>resolve(reply(value))});
        });
        if(url==='/api/ai/apply') {
          window.applies.push({request:JSON.parse(options.body),summary:document.querySelector('.ai-core-plan-options').textContent});
          return reply({kind:'BACKTEST',result:null});
        }
        throw Error('Unexpected API: '+url);
      };
    },fixture);
    for(const name of ['style.css','ai_editor.css']) await page.addStyleTag({content:fs.readFileSync(path.join(root,'Part3/web',name),'utf8')});
    for(const name of ['ai_display.js','ai_editor.js','ai_chat.js']) await page.addScriptTag({content:fs.readFileSync(path.join(root,'Part3/web',name),'utf8')});
    await page.evaluate(()=>document.dispatchEvent(new Event('DOMContentLoaded')));
    await page.locator('#ai-text').fill('초안으로 백테스트');await page.locator('#ai-send').click();
    const summary=page.locator('.ai-core-plan-options');
    await page.waitForFunction(()=>document.querySelector('.ai-core-plan-options')?.textContent.includes('손절: 1분봉 ATR14'));
    const tf=page.locator('#ai-edit-strategy-interpretation-steps-0-tfs-0');
    await page.locator('details.ai-core-edit').filter({has:tf}).evaluate(element=>{element.open=true;});
    await tf.selectOption({label:'2분봉'});
    assert.match(await summary.innerText(),/시간봉: 수정한 전략 검증 중/);
    assert.doesNotMatch(await summary.innerText(),/손절: 1분봉/);
    assert.equal(await page.locator('#ai-confirm').isDisabled(),true);
    await page.waitForFunction(()=>saves.length===1);
    await tf.selectOption({label:'3분봉'});
    await page.evaluate(()=>saves[0].resolve(fixture.replies['2m']));
    await page.waitForFunction(()=>saves.length===2);
    assert.match(await summary.innerText(),/검증 중/);
    assert.doesNotMatch(await summary.innerText(),/손절: [12]분봉/);
    await page.evaluate(()=>saves[1].resolve(fixture.replies['3m']));
    await page.waitForFunction(()=>document.querySelector('.ai-core-plan-options').textContent.includes('손절: 3분봉 ATR14'));
    assert.doesNotMatch(await summary.innerText(),/검증 중/);
    assert.equal(await page.locator('#ai-confirm').isEnabled(),true);
    checks.push('1분→2분→3분 연속 편집: 검증 전 이전 값 숨김, 늦은 2분 응답 무시, 최신 3분 표시');
    await tf.selectOption({label:'2분봉'});await page.waitForFunction(()=>saves.length===3);
    await page.evaluate(()=>saves[2].resolve({...fixture.replies['2m'],ok:false,can_apply:false,can_confirm:false,
      virtual_draft_frames:null,errors:[{path:['strategy','interpretation','steps'],message:'시험 검증 실패'}]}));
    await page.waitForFunction(()=>!document.querySelector('.ai-core-plan-options').textContent.includes('검증 중'));
    assert.match(await summary.innerText(),/손절: 전략 시간봉 ATR14/);
    assert.doesNotMatch(await summary.innerText(),/손절: [123]분봉/);
    assert.equal(await page.locator('#ai-confirm').isDisabled(),true);
    checks.push('검증 실패: 이전 숫자로 복구하지 않고 미확정 시간봉으로 표시, 실행 차단');
    await tf.selectOption({label:'3분봉'});await page.waitForFunction(()=>saves.length===4);
    await page.evaluate(()=>saves[3].resolve(fixture.replies['3m']));
    await page.waitForFunction(()=>!document.querySelector('#ai-confirm').disabled);
    await page.locator('#ai-confirm').click();await page.waitForFunction(()=>applies.length===1);
    assert.match(await page.evaluate(()=>applies[0].summary),/손절: 3분봉 ATR14/);
    assert.equal(await page.evaluate(()=>saves[3].request.strategy.interpretation.steps[0].tfs[0]),'3m');
    checks.push('최종 실행 전: 저장한 3분 전략과 핵심 요약의 SIGNAL 손절 시간봉 일치');
    await page.evaluate(()=>{
      const plan=structuredClone(fixture.contract.plan);plan.steps[0].command.request.virtual_entry=fixture.multi_policy;
      const editor=part3IntentEditor.create({contract:{...fixture.contract,strategy:null,plan,
        options:{...fixture.contract.options,virtual_draft_frames:fixture.multi_frames}}});
      document.querySelector('#extra').replaceChildren(editor.element);window.extraEditor=editor;
    });
    const extra=page.locator('#extra .ai-core-plan-options');
    assert.match(await extra.innerText(),/1분봉·2분봉·3분봉/);
    assert.match(await extra.innerText(),/환경 프레임 \(1시간봉·2시간봉·3시간봉·4시간봉\)/);
    await page.evaluate(()=>extraEditor.setVirtualDraftFrames(fixture.multi_frames));
    assert.match(await extra.innerText(),/환경 프레임 \(1시간봉·2시간봉·3시간봉·4시간봉\)/);
    checks.push('다중 SIGNAL·ENV 프레임 목록은 서버 metadata 갱신 후에도 유지');
    await page.evaluate(()=>{
      extraEditor.destroy();const plan=structuredClone(fixture.contract.plan);
      plan.steps[0].draft=false;plan.steps[0].command.request.filename='Test_UNKNOWN148.py';
      window.extraEditor=part3IntentEditor.create({contract:{...fixture.contract,strategy:null,plan}});
      document.querySelector('#extra').replaceChildren(extraEditor.element);
      extraEditor.setVirtualDraftFrames(fixture.replies['3m'].virtual_draft_frames);
    });
    assert.match(await extra.innerText(),/손절: 전략 시간봉 ATR14/);
    assert.doesNotMatch(await extra.innerText(),/손절: 3분봉/);
    checks.push('알 수 없는 생성 파일: 현재 초안 3분 정보와 혼동하지 않고 예비 문구 표시');
    assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
    const report={passed:checks.length,checks,errors,external_calls:external};
    fs.writeFileSync(path.join(proof,'ai_summary_refresh148.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  } finally {await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
