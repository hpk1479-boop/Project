/* Real Edge DOM; server schemas on stdin, no server, model, warehouse or engine. */
'use strict';
const assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path'), crypto=require('node:crypto');
const {chromium}=require(process.argv[2]);
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
const root=path.resolve(__dirname,'..'), proof=path.join((process.env.MOSES_EVIDENCE_ROOT || (process.env.MOSES_EVIDENCE_ROOT || path.join(root, '검증결과'))), 'summary109');
const read=name=>fs.readFileSync(path.join(root,'Part3/web',name),'utf8');
const source={editor:read('ai_editor.js'),display:read('ai_display.js'),css:read('ai_editor.css'),style:read('style.css')};
const checks=[], errors=[], networkCalls=[], measurements={};
const pass=name=>checks.push(name), clone=value=>JSON.parse(JSON.stringify(value));
const field=(page,name)=>page.locator(`[data-field-path="${name}"]`).first();
async function reveal(page,name) {
  const slot=field(page,name);assert.equal(await slot.count(),1,'Detailed field absent: '+name);
  await slot.evaluate(node=>{for(let owner=node.parentElement;owner;owner=owner.parentElement)if(owner.tagName==='DETAILS')owner.open=true;});
  return slot;
}
async function choose(page,name,label,index=0) {
  const slot=await reveal(page,name);await slot.locator('select').nth(index).selectOption({label});
}
async function number(page,name,value,index=0) {
  const slot=await reveal(page,name);await slot.locator('input[type=number]').nth(index).fill(String(value));
}
const draft=page=>page.evaluate(()=>window.summaryEditor.getDraft());
async function details(page) {
  await page.getByRole('button',{name:'상세보기',exact:true}).first().click();
}
async function core(page) {
  await page.getByRole('button',{name:'핵심 요약',exact:true}).first().click();
}
async function measure(page) {
  return {...await page.evaluate(()=>({height:Math.round(summaryEditor.element.getBoundingClientRect().height),
    width:innerWidth,scroll_width:document.documentElement.scrollWidth})),
    visible_inputs:await page.locator('.ai-intent-editor input:visible,.ai-intent-editor select:visible').count()};
}
async function mount(page,strategy,plan=null,preset=false) {
  await page.evaluate(({strategy,plan,contract,preset})=>{
    window.summaryEditor?.destroy();window.edits=[];
    const options={response:{kind:plan?'BACKTEST':'STRATEGY',editor_strategy:strategy,strategy:null,plan,
      ...(preset ? {preset:{id:'synthetic',name:'상세 항목 시험'}} : {})},
      contract:{...contract,operation:plan?'BACKTEST':'STRATEGY',strategy,plan},onChange:value=>edits.push(value)};
    window.mountedResponse=options.response;window.summaryEditor=part3IntentEditor.create(options);
    document.querySelector('#mount').append(summaryEditor.element);
  },{strategy,plan,contract:fixture.contract,preset});
}
async function openPage(browser,width=1280,baseline=false) {
  const page=await browser.newPage({viewport:{width,height:1000}});
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>{networkCalls.push(route.request().url());return route.abort();});
  await page.setContent('<!doctype html><html lang="ko"><head><meta charset="utf-8"></head><body><main id="mount"></main></body></html>');
  await page.addStyleTag({content:source.style});
  await page.addStyleTag({content:baseline?fixture.baseline['ai_editor.css']:source.css});
  await page.addStyleTag({content:'html,body{margin:0;min-width:0;}body{font-family:Arial,sans-serif;background:#091b29;color:#d4e6f1;}#mount{box-sizing:border-box;width:100%;max-width:1000px;margin:0 auto;padding:16px;}'});
  await page.evaluate(()=>{window.summaryApiCalls=[];window.fetch=async(...args)=>{
    summaryApiCalls.push(String(args[0]));throw Error('Summary rendering must not call APIs.');
  };});
  await page.addScriptTag({content:baseline?fixture.baseline['ai_display.js']:source.display});
  await page.addScriptTag({content:baseline?fixture.baseline['ai_editor.js']:source.editor});
  return page;
}
(async()=>{
  fs.mkdirSync(proof,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try {
    const page=await openPage(browser);
    await mount(page,fixture.ordinary);
    assert.deepEqual(await draft(page),{strategy:fixture.ordinary,operation:'STRATEGY',plan:null});
    assert.equal(await page.getByRole('heading',{name:'핵심 요약',exact:true}).count(),1);
    assert.equal(await page.locator('.ai-core-understanding:visible').count(),0,'The default summary must not duplicate its condition descriptions');
    measurements.current=await measure(page);
    assert.ok(measurements.current.visible_inputs<=6,'The default summary exposes too many edit controls');
    assert.ok(measurements.current.height<1100,'An ordinary two-condition summary should fit a concise card');
    pass('기본 핵심요약은 중복 설명 없이 간결한 조건과 최소 편집 슬롯을 표시');
    for(const name of ['strategy.interpretation.symbols','strategy.interpretation.direction','operation'])
      assert.equal(await field(page,name).isVisible(),true,'Main compact slot hidden: '+name);
    const conditionText=await page.locator('[data-condition-path="strategy.interpretation.steps.0"]').innerText();
    assert.match(conditionText,/15분|EMA50|WMA200/);
    pass('핵심 종목·방향·작업 슬롯과 현재 조건의 주요 값 표시');
    await page.locator('.ai-intent-editor').screenshot({path:path.join(proof,'ordinary_core_1280.png')});
    if(fixture.baseline) {
      const previous=await openPage(browser,1280,true);await mount(previous,fixture.ordinary);
      measurements.previous=await measure(previous);
      await previous.locator('.ai-intent-editor').screenshot({path:path.join(proof,'ordinary_previous_1280.png')});
      assert.ok(measurements.current.height<measurements.previous.height*0.8,'Summary height was not meaningfully reduced');
      assert.ok(measurements.current.visible_inputs<measurements.previous.visible_inputs);
      pass('같은 입력의 이전 읽기용 화면보다 높이와 기본 편집 슬롯 수 감소');await previous.close();
    }
    await choose(page,'strategy.interpretation.direction','매도');
    await choose(page,'strategy.interpretation.steps.0.ma_left','HMA');
    await number(page,'strategy.interpretation.steps.0.ma_left',70);
    const expected=clone(fixture.ordinary);expected.interpretation.direction='SHORT';expected.interpretation.steps[0].ma_left='HMA70';
    assert.deepEqual((await draft(page)).strategy,expected);
    assert.match(await page.locator('[data-condition-path="strategy.interpretation.steps.0"]').innerText(),/HMA70/);
    assert.deepEqual(await page.evaluate(()=>mountedResponse.editor_strategy),fixture.ordinary);
    pass('핵심요약에서 실제 슬롯을 펼쳐 주요 값 수정, 의미 문장 갱신과 원본 보존');
    const before=await draft(page);await details(page);assert.deepEqual(await draft(page),before);
    for(const name of ['symbols','direction','order_mode','global_combine','within_sec','final_window_sec'])
      assert.equal(await field(page,'strategy.interpretation.'+name).isVisible(),true);
    await choose(page,'strategy.interpretation.order_mode','순서 무관');
    await choose(page,'strategy.interpretation.global_combine','하나 이상 만족');
    await number(page,'strategy.interpretation.within_sec',180);
    await number(page,'strategy.interpretation.final_window_sec',1200);
    const overview=(await draft(page)).strategy.interpretation;
    assert.equal(overview.order_mode,'UNORDERED');assert.equal(overview.global_combine,'ANY');
    assert.equal(overview.within_sec,180);assert.equal(overview.final_window_sec,1200);
    pass('상세보기의 기존 전체 개요 여섯 항목 표시·편집 유지');
    const edited=await draft(page);await core(page);assert.deepEqual(await draft(page),edited);
    await details(page);assert.deepEqual(await draft(page),edited);
    pass('두 보기 간 왕복은 전체 초안과 상세 옵션을 바꾸지 않음');

    await mount(page,fixture.advanced);
    const advancedBefore=await draft(page), compactText=await page.locator('.ai-intent-editor').innerText();
    assert.match(compactText,/분기|추가|상세/);
    assert.equal(await page.getByRole('button',{name:'상세보기',exact:true}).count()>0,true);
    assert.ok((await measure(page)).visible_inputs<=6,'Extra rules should not expand every detail slot by default');
    await details(page);assert.deepEqual(await draft(page),advancedBefore);
    pass('추가 조건·독립 분기가 있으면 표시와 상세 접근을 제공하며 초안 보존');
    for(const name of [
      'strategy.interpretation.steps.2.ref','strategy.interpretation.steps.2.scope_ref',
      'strategy.interpretation.lifecycle.expires.bars','strategy.interpretation.lifecycle.expires.tf',
      'strategy.interpretation.lifecycle.snapshots.entry.field','strategy.interpretation.lifecycle.excursion.multiplier',
      'strategy.interpretation.lifecycle.first_success','strategy.interpretation.lifecycle.invalidate_refs',
      'strategy.interpretation.lifecycle.replace.scope','strategy.interpretation.lifecycle.restart_on.0.kind',
      'strategy.interpretation.branches.0.steps.0.kind','strategy.interpretation.branches.1.steps.0.side',
      'strategy.interpretation.after_conditions.0.level','strategy.interpretation.final_conditions.0.side',
      'strategy.interpretation.cancel_conditions.0.direction']) assert.equal(await (await reveal(page,name)).isVisible(),true);
    await number(page,'strategy.interpretation.lifecycle.expires.bars',8);
    await number(page,'strategy.interpretation.lifecycle.excursion.multiplier',3);
    await choose(page,'strategy.interpretation.steps.2.ref','cross');
    await choose(page,'strategy.interpretation.branches.1.steps.0.side','양봉');
    const advancedDraft=(await draft(page)).strategy.interpretation;
    assert.equal(advancedDraft.lifecycle.expires.bars,8);assert.equal(advancedDraft.lifecycle.excursion.multiplier,3);
    assert.equal(advancedDraft.steps[2].ref,'cross');assert.equal(advancedDraft.branches[1].steps[0].side,'BULL');
    assert.deepEqual(advancedDraft.time_filters,fixture.advanced.interpretation.time_filters);
    assert.deepEqual(advancedDraft.final_time_filters,fixture.advanced.interpretation.final_time_filters);
    pass('조건 참조·수명·고정값·재시작·분기·유지/최종/취소 조건의 상세 슬롯과 기존 거래시간 보존');
    await page.locator('.ai-intent-editor').screenshot({path:path.join(proof,'advanced_details_1280.png')});
    for(const direction of ['SHORT','BOTH']) {
      const branchMeaning=clone(fixture.advanced);branchMeaning.interpretation.direction=direction;
      branchMeaning.interpretation.branches[0].direction='LONG';
      delete branchMeaning.interpretation.branches[0].final.direction;
      await mount(page,branchMeaning);
      assert.equal(await page.locator('.ai-edit-form > .ai-edit-conditions').count(),0,
        'With independent branches, root conditions must not appear as another active strategy');
      const common=page.locator('.ai-core-extra').first();await common.locator(':scope > summary').click();
      const rootCondition=page.locator('[data-condition-path="strategy.interpretation.steps.0"]');
      assert.match(await rootCondition.evaluate(node=>node.closest('.ai-edit-conditions').querySelector('h4').textContent),/공통/);
      assert.match(await common.locator('.ai-edit-final').first().locator('h4').innerText(),/공통/);
      const branchFinal=page.locator('.ai-edit-branch').first().locator('.ai-core-final-text');
      assert.match(await branchFinal.innerText(),/매수/);
      assert.doesNotMatch(await branchFinal.innerText(),/매도|양방향/);
      assert.deepEqual((await draft(page)).strategy,branchMeaning);
    }
    pass('독립 분기는 공통 템플릿을 별도로 표시하고 최종 행동 방향은 분기 방향을 상속');
    const inheritanceCases=[
      {common:{kind:'OZ',tfs:['1m'],validation_mode:'NORMAL',trigger_mode:'BREAKER',direction:'LONG'},
        branchDirection:'SHORT',override:{kind:'OZ',tfs:['5m']},expected:[/5분봉/,/일반/,/브레이커/,/매수/],absent:[/매도/]},
      {common:{kind:'OZ',tfs:['1m'],validation_mode:'NORMAL',trigger_mode:'BREAKER',direction:'LONG'},
        branchDirection:'SHORT',override:{kind:'NOTIFY'},expected:[/매도/],absent:[/매수/,/브레이커/,/일반/,/1분봉/]},
      {common:{kind:'NOTIFY',direction:'SHORT'},branchDirection:'LONG',override:{kind:'NOTIFY'},
        expected:[/매도/],absent:[/매수/]},
      {common:{kind:'DEFINE'},branchDirection:'LONG',override:{kind:'NOTIFY'},expected:[/매수/],absent:[/매도/,/정의/]}
    ];
    for(const value of inheritanceCases) {
      const partial=clone(fixture.advanced);partial.interpretation.final=value.common;
      partial.interpretation.branches[0].direction=value.branchDirection;partial.interpretation.branches[0].final=value.override;
      await mount(page,partial);await page.locator('.ai-core-extra > summary').first().click();
      const text=await page.locator('.ai-edit-branch').first().locator('.ai-core-final-text').innerText();
      for(const pattern of value.expected) assert.match(text,pattern);
      for(const pattern of value.absent) assert.doesNotMatch(text,pattern);
      assert.deepEqual((await draft(page)).strategy,partial,'Effective preview must preserve the original partial override');
    }
    pass('같은 최종 행동의 부분 override는 공통 설정을 표시에서 상속하고 다른 행동은 기존 전용 필드를 상속하지 않음');
    await mount(page,fixture.ordinary);
    await page.evaluate(()=>summaryEditor.setErrors([{path:['strategy','interpretation','steps',0,'capture'],message:'기록 참조 이름을 확인해 주세요.'}]));
    assert.match(await page.locator('.ai-intent-editor').innerText(),/기록 참조 이름을 확인/);
    assert.equal(await page.locator('.ai-edit-errors').isVisible(),true);
    pass('핵심요약에서 숨겨진 참조 필드의 서버 오류도 사용자에게 표시');
    await page.evaluate(()=>summaryEditor.setErrors([{path:['strategy','interpretation','within_sec'],message:'조건 제한시간을 수정해 주세요.'}]));
    assert.match(await page.locator('.ai-intent-editor').innerText(),/조건 제한시간을 수정/);
    assert.equal(await field(page,'strategy.interpretation.within_sec').isVisible(),true,'An error in a folded edit panel must make that field accessible');
    pass('접힌 편집 필드의 검증 오류는 표시하고 수정 위치에 접근 가능');

    await mount(page,fixture.ambiguous);
    assert.match(await page.locator('.ai-intent-editor').innerText(),/매수 조건으로 확정/);
    assert.equal((await draft(page)).strategy.needs_clarification,true);
    await details(page);await choose(page,'strategy.needs_clarification','이 표의 조건으로 확정');
    assert.equal((await draft(page)).strategy.needs_clarification,false);
    assert.equal((await draft(page)).strategy.clarification_question,null);
    pass('확인 질문과 사용자의 명시적 확정 슬롯 동작 유지');

    await mount(page,null,fixture.plan);
    const planBefore=await draft(page), planText=await page.locator('.ai-intent-editor').innerText();
    assert.equal(planBefore.strategy,null);assert.equal(planBefore.operation,'BACKTEST');
    for(const value of ['SPECIAL1',fixture.symbols[0],'2026-01-01','2026-02-01','WATCH','UTC']) assert.ok(planText.includes(value),'Missing execution meaning: '+value);
    assert.match(planText,/미포함|포함하지/);assert.match(planText,/보유|있는 데이터/);assert.match(planText,/스프레드|포인트/);
    assert.equal(await field(page,'operation').locator('select').count(),0);
    assert.ok((await measure(page)).visible_inputs<=4,'Plan-only summary should keep execution fields folded');
    await details(page);assert.deepEqual(await draft(page),planBefore);
    pass('전략 없는 순차 계획도 실제 실행 대상·UTC 기간·보유 데이터·스프레드를 간결히 표시');
    for(const index of [0,1]) for(const name of ['symbol','start','end','mode','result_mode','spread_points','available_only','build_only','rebuild'])
      assert.equal(await (await reveal(page,`plan.steps.${index}.command.request.${name}`)).isVisible(),true);
    await number(page,'plan.steps.0.command.request.spread_points',7);
    await choose(page,'plan.steps.0.command.request.available_only','사용');
    await choose(page,'plan.steps.1.command.request.available_only','사용 안 함');
    await choose(page,'plan.steps.1.command.request.build_only','사용');
    const start=field(page,'plan.steps.0.command.request.start').locator('input');
    await start.fill('2026-01-10');await start.dispatchEvent('change');
    const planAfter=await draft(page);
    assert.equal(planAfter.strategy,null);
    assert.equal(planAfter.plan.steps[0].command.request.spread_points,7);
    assert.equal(planAfter.plan.steps[0].command.request.available_only,true);
    assert.equal(planAfter.plan.steps[0].command.request.start,'2026-01-10');
    assert.equal(planAfter.plan.steps[1].command.request.build_only,true);
    assert.equal(planAfter.plan.steps[1].command.request.available_only,false);
    assert.equal(planAfter.plan.steps[0].command.request.specials[0],'SPECIAL1');
    assert.equal(planAfter.plan.steps[1].command.request.watch_text,fixture.plan.steps[1].command.request.watch_text);
    pass('여러 작업의 상세 스프레드·보유 데이터·구축·기간 슬롯은 해당 작업만 수정');
    await core(page);assert.deepEqual(await draft(page),planAfter);
    const updatedPlanText=await page.locator('.ai-intent-editor').innerText();
    assert.match(updatedPlanText,/2026-01-10/);assert.match(updatedPlanText,/7/);assert.match(updatedPlanText,/구축/);
    await details(page);assert.deepEqual(await draft(page),planAfter);
    pass('실행 설정을 수정한 뒤 핵심요약 재표시와 양방향 전환은 plan-only JSON 그대로 유지');
    const storedPlan=clone(fixture.plan);storedPlan.steps=storedPlan.steps.slice(0,1);
    Object.assign(storedPlan.steps[0].command.request,{target_mode:'GENERATED',specials:[],filename:'Test_SPECIAL013.py',mode:'TICK'});
    await mount(page,null,storedPlan);assert.match(await page.locator('.ai-intent-editor').innerText(),/Test_SPECIAL013\.py/);
    await details(page);await core(page);
    assert.deepEqual(await draft(page),{strategy:null,operation:'BACKTEST',plan:storedPlan});
    pass('기존 생성 파일의 실행 대상도 plan-only 요약과 보기 전환에서 정확히 보존');
    const buildPlan=clone(storedPlan);buildPlan.steps[0].command.request.build_only=true;
    await mount(page,null,buildPlan);const buildText=await page.locator('.ai-intent-editor').innerText();
    assert.match(buildText,/구축/);assert.doesNotMatch(buildText,/Test_SPECIAL013\.py/);
    await details(page);assert.deepEqual(await draft(page),{strategy:null,operation:'BACKTEST',plan:buildPlan});
    pass('데이터 구축만 요청하면 전략 실행 대상으로 꾸미지 않으며 원본 실행 옵션 보존');
    await mount(page,fixture.ordinary,fixture.generated_plan);
    assert.deepEqual(await draft(page),{strategy:fixture.ordinary,operation:'BACKTEST',plan:fixture.generated_plan});
    await details(page);await core(page);
    assert.deepEqual(await draft(page),{strategy:fixture.ordinary,operation:'BACKTEST',plan:fixture.generated_plan});
    pass('전략+현재 전략 생성 백테스트의 통합 편집 초안도 기존 draft 연결 보존');

    await page.setViewportSize({width:360,height:900});
    for(const [name,strategy,plan] of [['ordinary',fixture.ordinary,null],['advanced',fixture.advanced,null],['plan_only',null,fixture.plan]]) {
      await mount(page,strategy,plan);const current=await measure(page);measurements[name+'_360']=current;
      assert.ok(current.scroll_width<=current.width,'Mobile overflow in '+name);
      await page.locator('.ai-intent-editor').screenshot({path:path.join(proof,name+'_core_360.png')});
      await details(page);const full=await measure(page);
      assert.ok(full.scroll_width<=full.width,'Mobile detail overflow in '+name);
    }
    pass('360px 화면에서 일반 조건·독립 분기·계획 전용 핵심/상세보기 가로 넘침 없음');
    assert.deepEqual(errors,[]);assert.deepEqual(networkCalls,[]);
    assert.deepEqual(await page.evaluate(()=>summaryApiCalls),[]);
    pass('실제 Edge에서 JavaScript 오류와 모델·서버·엔진 네트워크 호출 없음');
    const report={passed:checks.length,checks,errors,network_calls:networkCalls,measurements,sources:{}};
    for(const name of ['ai_editor.js','ai_editor.css','ai_display.js']) report.sources['Part3/web/'+name]=crypto.createHash('sha256').update(read(name)).digest('hex');
    fs.writeFileSync(path.join(proof,'ui_report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  } finally {await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
