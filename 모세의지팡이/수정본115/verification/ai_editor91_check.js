// Isolated DOM regression: real server schemas are passed on stdin by pytest.
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
class Element {
  constructor(tag) {
    this.tagName=tag.toUpperCase();this.children=[];this.attrs={};this.listeners={};this.dataset={};
    this._text='';this.className='';this.value='';this.disabled=false;this.type='';
    this.classList={
      contains:value=>this.className.split(' ').includes(value),
      add:value=>{if(!this.classList.contains(value))this.className=(this.className+' '+value).trim();},
      remove:value=>{this.className=this.className.split(' ').filter(item=>item!==value).join(' ');},
      toggle:(value,state)=>{const chosen=state===undefined?!this.classList.contains(value):state;
        if(chosen)this.classList.add(value);else this.classList.remove(value);return chosen;}
    };
  }
  set textContent(value) {this._text=String(value);this.children=[];}
  get textContent() {return this._text+this.children.map(item=>item.textContent).join('');}
  append(...items) {for(const item of items){item.parentNode=this;this.children.push(item);}}
  replaceChildren(...items) {this.children=[];this._text='';this.append(...items);}
  setAttribute(key,value) {this.attrs[key]=String(value);}
  getAttribute(key) {return this.attrs[key];}
  addEventListener(key,callback) {(this.listeners[key] ||= []).push(callback);}
  fire(key) {for(const callback of this.listeners[key] || [])callback({target:this});}
  remove() {if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(item=>item!==this);}
}
const window={};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'),{window,document:{createElement:tag=>new Element(tag)}});
const createEditor=window.part3IntentEditor.create;
window.part3IntentEditor={create(options){
  const editor=createEditor(options), before=JSON.stringify(editor.getDraft());
  const visit=root=>[root,...root.children.flatMap(visit)];
  const toggle=visit(editor.element).find(item=>item.tagName==='BUTTON' && item.textContent==='상세보기');
  assert.ok(toggle,'summary is the default editor view');
  toggle.fire('click');
  assert.equal(JSON.stringify(editor.getDraft()),before,'opening full details preserves the complete draft');
  return editor;
}};
const clone=value=>JSON.parse(JSON.stringify(value));
const all=root=>[root,...root.children.flatMap(all)];
const field=(editor,path)=>all(editor.element).find(item=>item.dataset.fieldPath===path);
const input=(editor,path,type)=>all(field(editor,path)).find(item=>item.tagName==='INPUT' && (!type || item.type===type));
const select=(editor,path,index=0)=>all(field(editor,path)).filter(item=>item.tagName==='SELECT')[index];
function choose(control,text) {
  const option=control.children.find(item=>item.textContent===text);
  assert.ok(option,'slot option missing: '+text);control.value=option.value;control.fire('change');
}
function enter(control,value) {control.value=String(value);control.fire('input');}
const meaning={direction:'LONG',symbols:[fixture.symbols[0]],order_mode:'SEQUENTIAL',global_combine:'ALL',within_sec:null,
  steps:[{kind:'MA_CROSS',tfs:['15m','SOURCE'],ma_left:'EMA50',ma_right:'WMA200',direction:'BOTH',capture:'start_event'},
    {kind:'FVG_NEW',tfs:['5m'],side:'BULL',capture:'same_gap'},
    {kind:'FVG_TOUCH',tfs:['1m'],ref:'same_gap',scope_ref:'start_event',direction:'SAME_AS_PREVIOUS_DIRECTION'}],
  final:{kind:'OZ',tfs:['1m'],validation_mode:'NORMAL',trigger_mode:'BREAKER',direction:'LONG',scope_ref:'same_gap'},
  final_window_sec:900,persistent:false,
  branches:[{steps:[{kind:'BAR_CLOSE',tfs:['30m'],bar_state:'CLOSED'}],within_sec:60}],
  lifecycle:{expires:{bars:5,tf:'30m'},snapshots:{entry:{tf:'15m',field:'close',bar_state:'CLOSED'},atr:{tf:'15m',field:'ATR14'}},
    excursion:{anchor:'entry',snapshot:'atr',multiplier:2,direction:'ADVERSE'},first_success:true,invalidate_refs:true},
  time_filters:['MAIN_ASIA']};
const envelope={supported:true,intent:'CREATE_STRATEGY',interpretation:meaning,needs_clarification:false,
  clarification_question:null,message_ko:'<img src=x onerror=alert(1)>'};
const contract=fixture.contract;
let changes=0;
const editor=window.part3IntentEditor.create({response:{result:envelope},contract,onChange:()=>changes++});
const originalExtras=all(editor.element).find(item=>item.tagName==='DETAILS' && item.children[0]?.textContent.startsWith('분기·수명·추가 조건'));
assert.equal(originalExtras.open,false,'ordinary AI interpretation keeps its details collapsed');
const presetEditor=window.part3IntentEditor.create({response:{result:envelope,preset:{id:'registry-template',name:'기존 전략'}},contract});
const presetExtras=all(presetEditor.element).find(item=>item.tagName==='DETAILS' && item.children[0]?.textContent.startsWith('분기·수명·추가 조건'));
assert.equal(presetExtras.open,true,'loaded preset branches must be visible without another click');
assert.equal(presetExtras.children[0].textContent,'분기·수명·추가 조건 · 분기 1개');
for(let parent=field(presetEditor,'strategy.interpretation.branches.0.steps.0.kind').parentNode;parent;parent=parent.parentNode)
  if(parent.tagName==='DETAILS')assert.equal(parent.open,true,'loaded branch condition has no closed parent');
assert.deepEqual(clone(presetEditor.getDraft().strategy),envelope,'expanded preset display preserves every original condition');
assert.deepEqual(clone(editor.getDraft().strategy),envelope,'render must not rewrite canonical interpretation');
assert.equal(editor.getDraft().operation,'STRATEGY');
assert.ok(all(editor.element).filter(item=>item.tagName==='INPUT').every(item=>['number','date'].includes(item.type)),
  'no arbitrary free-text canonical input');
assert.ok(all(editor.element).every(item=>item.tagName!=='IMG'),'model content must remain text');
choose(select(editor,'strategy.interpretation.direction'),'매도');
assert.equal(editor.getDraft().strategy.interpretation.direction,'SHORT');
const canonical=all(editor.element).filter(item=>item.tagName==='PRE').at(-1);
assert.equal(JSON.parse(canonical.textContent).interpretation.direction,'SHORT','detail JSON follows current edits');
assert.equal(editor.getDraft().strategy.interpretation.persistent,false);
assert.deepEqual(clone(editor.getDraft().strategy.interpretation.branches),meaning.branches,'no inherited branch overrides');
assert.deepEqual(clone(editor.getDraft().strategy.interpretation.lifecycle),meaning.lifecycle);
assert.deepEqual(clone(editor.getDraft().strategy.interpretation.time_filters),meaning.time_filters);
assert.deepEqual(clone(editor.getDraft().strategy.interpretation.steps[0].tfs),['15m','SOURCE']);
assert.ok(select(editor,'strategy.interpretation.steps.0.tfs').children.some(item=>item.textContent==='15분봉'));
assert.equal(editor.getDraft().strategy.interpretation.steps[2].direction,'SAME_AS_PREVIOUS_DIRECTION');
choose(select(editor,'strategy.interpretation.steps.0.ma_left'),'HMA');
enter(input(editor,'strategy.interpretation.steps.0.ma_left'),70);
assert.equal(editor.getDraft().strategy.interpretation.steps[0].ma_left,'HMA70');
assert.equal(editor.getDraft().strategy.interpretation.steps[0].ma_right,'WMA200');
enter(input(editor,'strategy.interpretation.final_window_sec'),0);
assert.ok(field(editor,'strategy.interpretation.final_window_sec').classList.contains('ai-edit-invalid'));
assert.ok(!field(editor,'strategy.interpretation.direction').classList.contains('ai-edit-invalid'));
editor.setErrors([]);
assert.ok(field(editor,'strategy.interpretation.final_window_sec').classList.contains('ai-edit-invalid'),'server clear retains invalid current number');
enter(input(editor,'strategy.interpretation.final_window_sec'),120);
assert.ok(!field(editor,'strategy.interpretation.final_window_sec').classList.contains('ai-edit-invalid'));
editor.setErrors([{path:['interpretation','steps',2,'ref'],message:'선행 객체를 선택해 주세요.'}]);
assert.ok(field(editor,'strategy.interpretation.steps.2.ref').classList.contains('ai-edit-invalid'));
assert.ok(!field(editor,'strategy.interpretation.steps.2.scope_ref').classList.contains('ai-edit-invalid'));
editor.setErrors([]);
assert.ok(!field(editor,'strategy.interpretation.steps.2.ref').classList.contains('ai-edit-invalid'));
choose(select(editor,'strategy.interpretation.final.kind'),'조건 충족 알림');
const final=editor.getDraft().strategy.interpretation.final;
assert.equal(final.kind,'NOTIFY');
assert.equal(final.direction,'LONG');
assert.equal(final.scope_ref,'same_gap');
assert.ok(!('validation_mode' in final) && !('tfs' in final) && !('trigger_mode' in final));
assert.deepEqual(clone(editor.getDraft().strategy.interpretation.steps),[
  {...meaning.steps[0],ma_left:'HMA70'},meaning.steps[1],meaning.steps[2]]);
choose(select(editor,'operation'),'백테스트');
let draft=editor.getDraft();
assert.equal(draft.operation,'BACKTEST');assert.equal(draft.plan.steps[0].draft,true);
assert.equal(draft.plan.steps[0].command.action,'START');
assert.equal(draft.plan.steps[0].command.request.target_mode,'GENERATED');
assert.ok(field(editor,'plan.steps.0.command.request.start').classList.contains('ai-edit-invalid'));
const start=input(editor,'plan.steps.0.command.request.start'),end=input(editor,'plan.steps.0.command.request.end');
start.value='2026-01-01';start.fire('change');end.value='2026-02-01';end.fire('change');
assert.equal(editor.getDraft().plan.steps[0].command.request.start,'2026-01-01');
assert.ok(!field(editor,'plan.steps.0.command.request.end').classList.contains('ai-edit-invalid'));
end.value='2025-01-01';end.fire('change');
assert.ok(field(editor,'plan.steps.0.command.request.end').classList.contains('ai-edit-invalid'));
editor.setDisabled(true);
assert.ok(all(editor.element).filter(item=>['INPUT','SELECT','BUTTON'].includes(item.tagName)).every(item=>item.disabled));
editor.setDisabled(false);
assert.ok(all(editor.element).filter(item=>['INPUT','SELECT','BUTTON'].includes(item.tagName)).every(item=>!item.disabled));
choose(select(editor,'operation'),'전략 생성');
assert.equal(editor.getDraft().plan,null);
choose(select(editor,'operation'),'백테스트');
assert.equal(editor.getDraft().plan.steps[0].command.request.start,'2026-01-01','toggle keeps unsubmitted plan');
assert.ok(changes>0);assert.deepEqual(envelope.interpretation,meaning,'editor never mutates original AI response');

const preservedPlan={strategy_text:null,steps:[0,1].map((_,i)=>({draft:true,command:{supported:true,action:'START',
  request:{target_mode:'GENERATED',symbol:fixture.symbols[0],start:'2026-01-01',end:'2026-02-01',mode:'TICK',
    result_mode:'ALERT_ONLY',spread_points:3,build_only:false,rebuild:false,filename:null,specials:null,watch_text:null},
  job_id:null,needs_clarification:false,clarification_question:null,message_ko:'순서 '+i}})),
  needs_clarification:false,clarification_question:null};
const second=window.part3IntentEditor.create({response:{strategy:{result:envelope}},contract:{...contract,operation:'BACKTEST',plan:preservedPlan}});
assert.deepEqual(clone(second.getDraft().plan),preservedPlan,'existing sequential plan stays intact');
choose(select(second,'plan.steps.1.command.request.result_mode'),'가상 진입');
assert.equal(second.getDraft().plan.steps[0].command.request.result_mode,'ALERT_ONLY');
assert.equal(second.getDraft().plan.steps[1].command.request.result_mode,'VIRTUAL_ENTRY');
assert.equal(second.getDraft().plan.steps[1].command.request.spread_points,3);
const unsupported=clone(envelope);unsupported.interpretation.steps[0].mystery='keep this';
const third=window.part3IntentEditor.create({response:{result:unsupported},contract});
assert.equal(third.getDraft().strategy.interpretation.steps[0].mystery,'keep this');
assert.ok(field(third,'strategy.interpretation.steps.0.mystery').classList.contains('ai-edit-invalid'));
const priceIntent=clone(envelope);
priceIntent.interpretation.steps=[{kind:'PRICE_LEVEL',tfs:['15m'],level:'DAY_OPEN',relation:'ABOVE'}];
priceIntent.interpretation.symbol_source='CURRENT_DEFAULT';
const price=window.part3IntentEditor.create({response:{result:priceIntent},contract});
assert.deepEqual(clone(price.getDraft().strategy),priceIntent,'string price selector and metadata remain intact');
assert.ok(!all(price.element).some(item=>item.dataset.fieldPath==='strategy.interpretation.symbol_source' && item.classList.contains('ai-edit-invalid')));
choose(select(price,'strategy.interpretation.steps.0.level'),'숫자로 지정');
enter(input(price,'strategy.interpretation.steps.0.level'),2300.5);
assert.equal(price.getDraft().strategy.interpretation.steps[0].level,2300.5);
assert.ok(!field(price,'strategy.interpretation.steps.0.level').classList.contains('ai-edit-invalid'));
choose(select(price,'strategy.interpretation.steps.0.level'),'당일 시가');
assert.equal(price.getDraft().strategy.interpretation.steps[0].level,'DAY_OPEN');
const ambiguous=clone(envelope);ambiguous.needs_clarification=true;
ambiguous.clarification_question='상단 원비 조건을 이 표대로 확정할까요?';
const question=window.part3IntentEditor.create({response:{result:ambiguous},contract});
assert.equal(question.getDraft().strategy.needs_clarification,true,'render never automatically resolves AI clarification');
assert.ok(field(question,'strategy.needs_clarification').classList.contains('ai-edit-invalid'));
choose(select(question,'strategy.needs_clarification'),'이 표의 조건으로 확정');
assert.equal(question.getDraft().strategy.needs_clarification,false);
assert.equal(question.getDraft().strategy.clarification_question,null);
assert.ok(!field(question,'strategy.needs_clarification').classList.contains('ai-edit-invalid'));
assert.ok(question.element.textContent.includes(ambiguous.clarification_question),'original question remains visible');
choose(select(question,'strategy.needs_clarification'),'추가 확인 필요');
assert.equal(question.getDraft().strategy.needs_clarification,true);
assert.equal(question.getDraft().strategy.clarification_question,ambiguous.clarification_question);
assert.equal(ambiguous.needs_clarification,true,'original model response is immutable');
const remove=all(second.element).find(item=>item.tagName==='BUTTON' && item.textContent==='조건 삭제');remove.fire('click');
assert.equal(second.getDraft().strategy.interpretation.steps.length,2);
assert.equal(second.getDraft().strategy.interpretation.steps[1].scope_ref,'start_event','deleting capture never silently drops references');
editor.destroy();second.destroy();third.destroy();price.destroy();question.destroy();
process.stdout.write('ai_editor91 DOM regression PASS\n');
