/* Real Edge DOM; the shipped SPECIAL2 meaning (final level_gate) in the AI editor. No server, model or engine. */
'use strict';
const assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path');
const {chromium}=require(process.argv[2]);
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
const root=path.resolve(__dirname,'..'), proof=process.argv[3]||path.join((process.env.MOSES_EVIDENCE_ROOT || (process.env.MOSES_EVIDENCE_ROOT || path.join(root, '검증결과'))), 'revision116/level_gate_editor');
const read=name=>fs.readFileSync(path.join(root,'Part3/web',name),'utf8');
const source={editor:read('ai_editor.js'),display:read('ai_display.js'),css:read('ai_editor.css'),style:read('style.css')};
const checks=[], errors=[], networkCalls=[];
const pass=name=>checks.push(name), clone=value=>JSON.parse(JSON.stringify(value));
const GATE='strategy.interpretation.final.level_gate';
const field=(page,name)=>page.locator(`[data-field-path="${name}"]`).first();
const draft=page=>page.evaluate(()=>window.gateEditor.getDraft());
const gateOf=async page=>(await draft(page)).strategy.interpretation.final.level_gate;
const SLOT_ERROR='이 항목은 슬롯으로 수정할 수 없습니다';
async function mount(page,strategy) {
  await page.evaluate(({strategy,contract})=>{
    window.gateEditor?.destroy();
    window.gateEditor=part3IntentEditor.create({response:{kind:'STRATEGY',editor_strategy:strategy,strategy:null,plan:null},
      contract:{...contract,operation:'STRATEGY',strategy,plan:null},onChange:()=>{}});
    document.querySelector('#mount').append(gateEditor.element);
  },{strategy,contract:fixture.contract});
}
(async()=>{
  fs.mkdirSync(proof,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1280,height:1000}});
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',route=>{networkCalls.push(route.request().url());return route.abort();});
    await page.setContent('<!doctype html><html lang="ko"><head><meta charset="utf-8"></head><body><main id="mount"></main></body></html>');
    await page.addStyleTag({content:source.style});await page.addStyleTag({content:source.css});
    await page.addStyleTag({content:'html,body{margin:0}body{font-family:Arial,sans-serif;background:#091b29;color:#d4e6f1}#mount{max-width:1000px;margin:0 auto;padding:16px}'});
    await page.addScriptTag({content:source.display});await page.addScriptTag({content:source.editor});

    await mount(page,fixture.strategy);
    await page.getByRole('button',{name:'상세보기',exact:true}).first().click();
    // A correct strategy shows no error and the draft is untouched by merely looking at it.
    const text=await page.locator('.ai-intent-editor').innerText();
    assert.equal(text.includes(SLOT_ERROR),false,'level_gate must not be reported as unsupported');
    assert.equal(await page.locator('.ai-edit-errors.has-errors').count(),0);
    assert.equal(await page.locator('.ai-edit-invalid').count(),0);
    assert.deepEqual((await draft(page)).strategy,fixture.strategy);
    pass('level_gate가 있는 정상 전략은 오류 표시가 없고 초안이 그대로');

    // The three values are editable slots.
    const slot=name=>field(page,`${GATE}.${name}`);
    for (const name of ['ref','atr_period','atr_mult']) assert.equal(await slot(name).count(),1,name);
    assert.equal(await slot('ref').locator('select').inputValue()!=='' ,true);
    assert.equal((await slot('ref').locator('select option:checked').innerText()).trim(),fixture.ref);
    assert.deepEqual(await slot('ref').locator('select option').allInnerTexts(),[fixture.ref]);
    assert.equal(await slot('atr_period').locator('input').inputValue(),'14');
    assert.equal(await slot('atr_mult').locator('input').inputValue(),'1.5');
    await page.locator('.ai-intent-editor').screenshot({path:path.join(proof,'level_gate_slots.png')});
    pass('연결 대상(외부유동성 터치 캡처 하나)·ATR 기간·ATR 배수가 현재 값으로 표시');

    await slot('atr_period').locator('input').fill('20');
    await slot('atr_mult').locator('input').fill('2');
    assert.deepEqual(await gateOf(page),{ref:fixture.ref,atr_period:20,atr_mult:2});
    assert.equal(await page.locator('.ai-edit-invalid').count(),0);
    pass('기간·배수를 고치면 초안의 level_gate만 바뀜');

    await slot('atr_period').locator('input').fill('');
    await slot('atr_mult').locator('input').fill('');
    assert.deepEqual(await gateOf(page),{ref:fixture.ref});
    assert.equal(await slot('atr_period').locator('input').getAttribute('placeholder'),'기본값 사용');
    assert.equal(await page.locator('.ai-edit-invalid').count(),0);
    pass('비우면 해당 항목이 빠지고(기본 ATR 규칙) 오류가 아님');

    await slot('atr_mult').locator('input').fill('0');
    assert.match(await slot('atr_mult').locator('.ai-edit-field-error').innerText(),/0보다 큰 숫자/);
    await slot('atr_mult').locator('input').fill('1.5');
    await slot('atr_period').locator('input').fill('0');
    assert.match(await slot('atr_period').locator('.ai-edit-field-error').innerText(),/1 이상/);
    pass('범위를 벗어난 값은 그 칸에 숫자 안내가 표시됨');

    // A gate name not offered by any touch is flagged on its own slot, not as an unsupported item.
    const broken=clone(fixture.strategy);broken.interpretation.final.level_gate.ref='nothing_here';
    await mount(page,broken);
    await page.getByRole('button',{name:'상세보기',exact:true}).first().click();
    assert.equal((await page.locator('.ai-intent-editor').innerText()).includes(SLOT_ERROR),false);
    assert.equal((await slot('ref').locator('select option').allInnerTexts()).includes(fixture.ref),true);
    pass('없는 캡처를 가리키면 선택지에서 올바른 대상을 고를 수 있음');

    // Without a gate nothing is added.
    const plain=clone(fixture.strategy);delete plain.interpretation.final.level_gate;
    await mount(page,plain);
    await page.getByRole('button',{name:'상세보기',exact:true}).first().click();
    assert.equal(await slot('ref').count(),0);
    assert.deepEqual((await draft(page)).strategy,plain);
    pass('level_gate가 없는 전략에는 칸이 생기지 않음');

    assert.deepEqual(errors,[]);assert.deepEqual(networkCalls,[]);
    fs.writeFileSync(path.join(proof,'level_gate_editor_checks.json'),JSON.stringify({passed:checks.length,checks,errors},null,2)+'\n');
    console.log(JSON.stringify({passed:checks.length,errors,network_calls:networkCalls}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
