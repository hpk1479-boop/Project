/* Real Edge DOM: the AI research editor names every generated file of a lanes request and keeps its
   trigger and file lists unchanged (수정본184). argv: playwright module, evidence folder. stdin: {contract, plans}. */
'use strict';
const assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path');
const {chromium}=require(process.argv[2]);
const proof=process.argv[3];
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
const root=path.resolve(__dirname,'..');
const errors=[], calls=[];
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
    const shown={};
    for(const [name,plan] of Object.entries(fixture.plans)){
      await page.evaluate(({plan,contract})=>{
        window.editor?.destroy();
        window.editor=part3IntentEditor.create({response:{kind:'BACKTEST',editor_strategy:null,plan},
          contract:{...contract,operation:'BACKTEST',strategy:null,plan},onChange:()=>{}});
        document.querySelector('#mount').replaceChildren(editor.element);
      },{plan,contract:fixture.contract});
      shown[name]=await page.locator('.ai-core-plan-target').innerText();
      assert.deepEqual(await page.evaluate(()=>editor.getDraft().plan),plan,name);
    }
    assert.match(shown.files,/기존 생성 전략 실행 대상: Test_SPECIAL001\.py, Test_SPECIAL002\.py/);
    assert.match(shown.file,/기존 생성 전략 실행 대상: Test_SPECIAL001\.py$/);
    assert.match(shown.none,/기존 생성 전략 파일을 지정하지 않았습니다/);
    assert.deepEqual(errors,[]);assert.deepEqual(calls,[]);
    const report={passed:3,shown,errors,network_calls:calls};
    fs.writeFileSync(path.join(proof,'lane_editor_report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  } finally {await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
