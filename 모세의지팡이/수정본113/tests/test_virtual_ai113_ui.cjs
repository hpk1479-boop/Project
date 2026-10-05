/* Real Edge DOM for post-alert policy summaries. No server/model/market calls. */
'use strict';
const assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path');
const {chromium}=require(process.argv[2]);
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
const root=path.resolve(__dirname,'..'), proof=path.join(root,'검증결과/revision113/ai');
const checks=[], errors=[], calls=[];
(async()=>{
  fs.mkdirSync(proof,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1280,height:1100}});
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',route=>{calls.push(route.request().url());return route.abort();});
    await page.setContent('<!doctype html><html lang="ko"><meta charset="utf-8"><main id="mount"></main></html>');
    await page.evaluate(()=>{window.fetch=()=>{throw Error('No API call is permitted');};});
    for(const name of ['style.css','ai_editor.css']) await page.addStyleTag({content:fs.readFileSync(path.join(root,'Part3/web',name),'utf8')});
    for(const name of ['ai_display.js','ai_editor.js']) await page.addScriptTag({content:fs.readFileSync(path.join(root,'Part3/web',name),'utf8')});
    async function mount(plan){
      await page.evaluate(({plan,contract})=>{
        window.editor?.destroy();
        window.editor=part3IntentEditor.create({response:{kind:'BACKTEST',editor_strategy:null,plan},
          contract:{...contract,operation:'BACKTEST',strategy:null,plan},onChange:()=>{}});
        document.querySelector('#mount').append(editor.element);
      },{plan,contract:fixture.contract});
    }
    await mount(fixture.plan);
    const text=await page.locator('.ai-intent-editor').innerText();
    for(const pattern of [/확인 진입/,/직전 봉 매수 양봉/,/현재봉 시가/,/EMA50/,/SMA20/,/WMA30/,/인걸핑/,/넥라인/,
      /ATR21/,/몸통/,/전체 길이/,/HMA17/,/7개 확정봉/,/1:1\.5/]) assert.match(text,pattern);
    assert.deepEqual(await page.evaluate(()=>editor.getDraft().plan),fixture.plan);
    checks.push('AI 계획 핵심요약에 전체 확인 조건·ATR 필터·손절 선택 표시와 원본 보존');
    await page.locator('.ai-intent-editor').screenshot({path:path.join(proof,'ai_policy_core.png')});
    await page.getByRole('button',{name:'상세보기',exact:true}).click();
    const result=page.locator('[data-field-path="plan.steps.0.command.request.result_mode"] select').first();
    await result.selectOption({label:'알림만'});
    assert.deepEqual(await page.evaluate(()=>editor.getDraft().plan.steps[0].command.request.virtual_entry),fixture.policy);
    await page.getByRole('button',{name:'핵심 요약',exact:true}).click();
    assert.doesNotMatch(await page.locator('.ai-core-plan-options').innerText(),/가상진입:/);
    checks.push('알림만으로 변경하면 가상진입 요약을 숨기며 정책 객체는 유지');
    await page.getByRole('button',{name:'상세보기',exact:true}).click();
    await result.selectOption({label:'가상 진입'});
    await page.getByRole('button',{name:'핵심 요약',exact:true}).click();
    assert.match(await page.locator('.ai-core-plan-options').innerText(),/EMA50/);
    assert.deepEqual(await page.evaluate(()=>editor.getDraft().plan),fixture.plan);
    checks.push('가상진입으로 돌아오면 모든 정책과 상세보기 왕복 유지');
    const immediate=structuredClone(fixture.plan);
    immediate.steps[0].command.request.virtual_entry.mode='IMMEDIATE';
    immediate.steps[0].command.request.virtual_entry.conditions=[];
    immediate.steps[0].command.request.virtual_entry.stop.kind='ATR';
    immediate.steps[0].command.request.virtual_entry.stop.multiplier=1.5;
    await mount(immediate);
    const immediateText=await page.locator('.ai-core-plan-options').innerText();
    assert.match(immediateText,/즉시 · 알림 시점 가격/);assert.match(immediateText,/ATR21 × 1\.5/);
    assert.doesNotMatch(immediateText,/추가 확인:|직전 봉 매수 양봉/);
    assert.deepEqual(await page.evaluate(()=>editor.getDraft().plan),immediate);
    checks.push('즉시 진입은 확인 조건을 표시하지 않으며 ATR 필터·손절은 표시');
    assert.deepEqual(errors,[]);assert.deepEqual(calls,[]);
    checks.push('실제 Edge에서 JavaScript 오류와 외부 네트워크 호출 없음');
    const report={passed:checks.length,checks,errors,network_calls:calls};
    fs.writeFileSync(path.join(proof,'ui_report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  } finally {await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
