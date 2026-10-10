/* Real Edge DOM: the AI research editor's core summary names a virtual entry's real frames (수정본147).
   argv: playwright module, evidence folder. stdin: {contract, plans} from the real contract and recipes. */
'use strict';
const assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path');
const {chromium}=require(process.argv[2]);
const proof=process.argv[3];
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
const root=path.resolve(__dirname,'..');
const checks=[], errors=[], calls=[];
const expected={
  special9_on_2m:[/진입: 조건 진입 · 기준 프레임 2분봉/,/양봉·음봉 \+ 2분봉 HMA50 터치 \+ 2분봉 HMA50 종가 위·아래/,
    /진입 제한: 2분봉 HMA17·50 정배열 유지/,/진입 제한: 20분봉 추세 유지/,/손절: 2분봉 ATR14 × 1배/],
  special1:[/1분봉·2분봉·3분봉·4분봉·5분봉·6분봉·10분봉·12분봉·15분봉·20분봉·30분봉·1시간봉 HMA6 종가 위·아래/,
    /진입 제한: 환경 프레임 \(1시간봉·2시간봉·3시간봉·4시간봉\) 추세 유지/,/손절: 올존 B0/],
  draft:[/손절: 1분봉 ATR14 × 1배/],
  unknown:[/손절: 전략 시간봉 ATR14 × 1배/]};
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
    for(const [name,patterns] of Object.entries(expected)){
      const plan=fixture.plans[name];
      await page.evaluate(({plan,contract})=>{
        window.editor?.destroy();
        window.editor=part3IntentEditor.create({response:{kind:'BACKTEST',editor_strategy:null,plan},
          contract:{...contract,operation:'BACKTEST',strategy:null,plan},onChange:()=>{}});
        document.querySelector('#mount').replaceChildren(editor.element);
      },{plan,contract:fixture.contract});
      const text=await page.locator('.ai-core-plan-options').innerText();
      for(const pattern of patterns) assert.match(text,pattern,name);
      assert.doesNotMatch(text,/알림 시간봉|자동|SIGNAL|ENV\b/,name);
      assert.deepEqual(await page.evaluate(()=>editor.getDraft().plan),plan,name);
      if(name==='special9_on_2m') await page.locator('.ai-intent-editor').screenshot({path:path.join(proof,'ai_summary_special9_2m.png')});
      checks.push(name+': 핵심 요약에 실제 시간봉');
    }
    assert.deepEqual(errors,[]);assert.deepEqual(calls,[]);
    checks.push('실제 Edge에서 JavaScript 오류와 외부 네트워크 호출 없음');
    const report={passed:checks.length,checks,errors,network_calls:calls};
    fs.writeFileSync(path.join(proof,'ai_summary_report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  } finally {await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
