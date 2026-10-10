// Plan-only editor regression using the real server contract supplied on stdin.
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
const clone = value => JSON.parse(JSON.stringify(value));
const all = root => [root, ...root.children.flatMap(all)];
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
  appendChild(item) {this.append(item);return item;}
  replaceChildren(...items) {this.children=[];this._text='';this.append(...items);}
  setAttribute(key,value) {this.attrs[key]=String(value);}
  getAttribute(key) {return this.attrs[key];}
  addEventListener(key,callback) {(this.listeners[key] ||= []).push(callback);}
  fire(key) {return Promise.all((this.listeners[key] || []).map(callback=>callback({target:this})));}
  querySelectorAll(selector) {
    return all(this).filter(item=>selector==='[data-condition-path]' && item.dataset.conditionPath);
  }
  scrollIntoView() {}
  focus() {}
  remove() {if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(item=>item!==this);}
}
const editorSource=fs.readFileSync(process.argv[2],'utf8');
const chatSource=fs.readFileSync(process.argv[3],'utf8');
// The page loads ai_display.js first; the plan summary shows the entry policy through virtualEntry.
const window={part3IntentDisplay:{explain(){throw new Error('Plan-only editing must not fabricate strategy conditions.');},
  virtualEntry:()=>'가상진입: 전략 기본값'}};
vm.runInNewContext(editorSource,{window,document:{createElement:tag=>new Element(tag)}});
const field=(editor,path)=>all(editor.element).find(item=>item.dataset.fieldPath===path);
const control=(editor,path,tag='INPUT')=>all(field(editor,path)).find(item=>item.tagName===tag);
const toggle=editor=>all(editor.element).find(item=>item.tagName==='BUTTON' && item.classList.contains('ai-edit-view-toggle'));
function choose(input,text) {
  const option=input.children.find(item=>item.textContent===text);
  assert.ok(option,'Missing option: '+text);input.value=option.value;input.fire('change');
}
function enter(input,value,event='input') {input.value=String(value);input.fire(event);}
function row(changes={}) {
  return {draft:false,command:{supported:true,action:'START',request:{target_mode:'SPECIAL',specials:['SPECIAL1','SPECIAL2'],
    filename:null,watch_text:null,symbol:fixture.symbols[0],start:'2026-01-01',end:'2026-02-01',mode:'EVENT',
    result_mode:'VIRTUAL_ENTRY',spread_points:5,build_only:false,rebuild:true,...changes},
    job_id:null,needs_clarification:false,clarification_question:null,message_ko:'기존 실행 대상'}};
}
const plan=(...rows)=>({strategy_text:null,steps:rows,needs_clarification:false,clarification_question:null});
function create(value,contract=fixture.contract) {
  return window.part3IntentEditor.create({response:{kind:'BACKTEST',editor_strategy:null,strategy:null,plan:value,
    result:{items:[]}},contract});
}
const original=plan(row());
const editor=create(original);
assert.deepEqual(clone(editor.getDraft()),{strategy:null,operation:'BACKTEST',plan:original});
assert.ok(editor.element.classList.contains('ai-core-mode'));
assert.ok(editor.element.textContent.includes('핵심 요약'));
assert.ok(editor.element.textContent.includes('기존 SPECIAL 실행 대상: SPECIAL1, SPECIAL2'));
const planPeriod=all(editor.element).find(item=>item.classList.contains('ai-core-plan-period')).textContent;
assert.ok(['2026-01-01','2026-02-01','종료일 미포함'].every(value=>planPeriod.includes(value)));
// 수정본177: the dates are the start and end days only, never labelled UTC.
assert.ok(!planPeriod.includes('UTC'));
const planOptions=()=>all(editor.element).filter(item=>item.classList.contains('ai-core-plan-options')).map(item=>item.textContent).join(' ');
assert.ok(planOptions().includes('재생 모드: 이벤트'));
assert.ok(planOptions().includes('결과 방식: 가상 진입'));
assert.ok(!all(editor.element).some(item=>(item.dataset.fieldPath || '').startsWith('strategy.')));
assert.ok(!all(field(editor,'operation')).some(item=>item.tagName==='SELECT'),'A plan without a strategy cannot become STRATEGY');
assert.ok(!editor.element.textContent.includes('전략의 상세 해석을 확인'));
assert.ok(!editor.element.textContent.includes('유효한 전략 해석과 편집 계약이 필요'));
toggle(editor).fire('click');
assert.deepEqual(clone(editor.getDraft()),{strategy:null,operation:'BACKTEST',plan:original},'Opening details preserves every option');
assert.ok(editor.element.textContent.includes('백테스트 계획표'));
const canonical=()=>all(editor.element).filter(item=>item.tagName==='PRE').at(-1);
assert.deepEqual(JSON.parse(canonical().textContent),{strategy:null,operation:'BACKTEST',plan:original});
const prefix='plan.steps.0.command.request.';
choose(control(editor,prefix+'symbol','SELECT'),fixture.symbols[1]);
enter(control(editor,prefix+'start'),'2026-02-01','change');
enter(control(editor,prefix+'end'),'2026-03-01','change');
choose(control(editor,prefix+'mode','SELECT'),'틱');
choose(control(editor,prefix+'result_mode','SELECT'),'알림만');
enter(control(editor,prefix+'spread_points'),8);
choose(control(editor,prefix+'rebuild','SELECT'),'사용 안 함');
let expected=clone(original);
Object.assign(expected.steps[0].command.request,{symbol:fixture.symbols[1],start:'2026-02-01',end:'2026-03-01',mode:'TICK',
  result_mode:'ALERT_ONLY',spread_points:8,rebuild:false});
assert.deepEqual(clone(editor.getDraft()),{strategy:null,operation:'BACKTEST',plan:expected},'Only explicitly edited execution fields change');
assert.deepEqual(JSON.parse(canonical().textContent),clone(editor.getDraft()),'Full JSON follows current plan edits');
assert.deepEqual(original,plan(row()),'Response remains immutable');
enter(control(editor,prefix+'end'),'2026-01-01','change');
assert.ok(field(editor,prefix+'end').classList.contains('ai-edit-invalid'));
assert.ok(!field(editor,prefix+'symbol').classList.contains('ai-edit-invalid'));
enter(control(editor,prefix+'end'),'2026-03-01','change');
editor.setErrors([{path:['plan','steps',0,'command','request','spread_points'],message:'스프레드를 확인해 주세요.'}]);
assert.ok(field(editor,prefix+'spread_points').classList.contains('ai-edit-invalid'));
editor.setErrors([]);
assert.ok(!field(editor,prefix+'spread_points').classList.contains('ai-edit-invalid'));
choose(control(editor,prefix+'build_only','SELECT'),'사용');
assert.ok(field(editor,prefix+'target_mode').textContent.includes('데이터 구축만 수행하며 전략은 실행하지 않습니다.'));
toggle(editor).fire('click');
assert.ok(planOptions().includes('데이터 모드: 틱'));
assert.ok(!planOptions().includes('결과 방식:'),'A build-only request does not claim strategy results');
editor.setDisabled(true);
assert.ok(all(editor.element).filter(item=>['INPUT','SELECT','BUTTON'].includes(item.tagName)).every(item=>item.disabled));
editor.setDisabled(false);
assert.ok(all(editor.element).filter(item=>['INPUT','SELECT','BUTTON'].includes(item.tagName)).every(item=>!item.disabled));

const sequential=plan(row({target_mode:'GENERATED',specials:[],filename:'Test_SPECIAL017.py',mode:null,result_mode:null,spread_points:null}),
  row({target_mode:'WATCH',specials:null,watch_text:'골드 기존 감시 명령',mode:'BAR'}),row({target_mode:null,specials:[],build_only:true}));
const sequenceEditor=create(sequential);
assert.deepEqual(clone(sequenceEditor.getDraft().plan),sequential);
assert.ok(sequenceEditor.element.textContent.includes('기존 생성 전략 실행 대상: Test_SPECIAL017.py'));
assert.ok(sequenceEditor.element.textContent.includes('WATCH 실행 대상: 골드 기존 감시 명령'));
assert.ok(all(sequenceEditor.element).find(item=>item.classList.contains('ai-core-plan-options')).textContent.includes('재생 모드: 기본값'));
assert.deepEqual(all(sequenceEditor.element).filter(item=>item.tagName==='H5').map(item=>item.textContent),
  ['순차 작업 1','순차 작업 2','순차 작업 3'],'The compact plan retains ordered job labels');
const beforeFirst=clone(sequential.steps[0]);
enter(control(sequenceEditor,'plan.steps.1.command.request.end'),'2026-04-01','change');
assert.deepEqual(clone(sequenceEditor.getDraft().plan.steps[0]),beforeFirst);
assert.deepEqual(clone(sequenceEditor.getDraft().plan.steps[2]),sequential.steps[2]);
const ambiguous=clone(original);ambiguous.needs_clarification=true;ambiguous.clarification_question='기간을 더 설명해 주세요.';
const ambiguousEditor=create(ambiguous);
assert.equal(ambiguousEditor.getDraft().plan.needs_clarification,true,'Rendering never resolves plan clarification');
assert.ok(ambiguousEditor.element.textContent.includes(ambiguous.clarification_question));
const noSchema=create(original,{plan_schema:fixture.contract.plan_schema,options:{backtest_symbols:fixture.symbols}});
assert.ok(field(noSchema,prefix+'symbol'),'Plan-only rendering needs no strategy schema');
for (const action of ['STATUS','STOP','RECENT','RECONNECT']) {
  const nonStart=plan({draft:false,command:{supported:true,action,request:null,job_id:null,needs_clarification:false,
    clarification_question:null,message_ko:'작업 조회'}});
  const value=create(nonStart);
  assert.ok(!field(value,prefix+'start'),action+' must not expose START execution fields');
  assert.deepEqual(clone(value.getDraft().plan),nonStart);
}
const dangling=clone(original);dangling.steps[0].draft=true;
const danglingEditor=create(dangling);
assert.equal(danglingEditor.getDraft().strategy,null);
assert.ok(danglingEditor.element.textContent.includes('현재 전략을 실행하려면 유효한 전략 해석이 필요합니다.'));
assert.ok(!danglingEditor.element.textContent.includes('현재 연구 전략을 확인 후 생성하여 실행합니다.'));

async function checkChatMount(action) {
  const messages=new Element('div'), ids=new Map([['#ai-messages',messages]]), calls=[], created=[];
  const get=id=>{if(!ids.has(id))ids.set(id,new Element('div'));return ids.get(id);};
  const value=action==='START' ? original : plan({draft:false,command:{supported:true,action,request:null,job_id:null,
    needs_clarification:false,clarification_question:null,message_ko:'작업 조회'}});
  const response={kind:'BACKTEST',strategy:null,editor_strategy:null,operation:'BACKTEST',plan:value,
    revision:'fixture:1',can_confirm:action==='START',message_ko:'계획 확인',preview:'기존 작업',result:null};
  const callbacks={}, window={addEventListener(){},dispatchEvent(){},part3InvalidateAIRecipe(){},
    part3IntentDisplay:{render(){throw new Error('No fabricated strategy display');},virtualEntry:()=>'가상진입: 전략 기본값'}};
  const document={createElement:tag=>new Element(tag),querySelector:get,querySelectorAll:()=>[],
    addEventListener:(key,callback)=>{callbacks[key]=callback;}};
  const context={window,document,crypto:{randomUUID:()=> 'fixture-session'},CustomEvent:class {},
    sessionStorage:{getItem:()=>null,setItem(){}},setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,
    fetch:async(url,request)=>{
      calls.push({url,body:request?.body && JSON.parse(request.body)});
      const payload=url==='/api/ai/chat' ? response : url==='/api/ai/editor' ? {...fixture.contract,strategy:null,plan:value,operation:'BACKTEST'}
        : url==='/api/ai/settings' ? {settings:{provider:'gemini'}} : {};
      return {ok:true,status:200,json:async()=>clone(payload)};
    }};
  vm.runInNewContext(editorSource,context);
  const realCreate=window.part3IntentEditor.create;
  window.part3IntentEditor={create(options){const value=realCreate(options);created.push(value);return value;}};
  vm.runInNewContext(chatSource,context);
  callbacks.DOMContentLoaded();
  await new Promise(setImmediate);
  get('#ai-text').value='기존 백테스트 작업을 확인해 주세요.';
  await get('#ai-send').fire('click');
  assert.equal(calls.filter(item=>item.url==='/api/ai/chat').length,1,'Exactly one user interpretation request');
  if(action==='START') {
    assert.equal(created.length,1,'A strategy-null START response mounts the editable summary');
    assert.equal(created[0].getDraft().strategy,null);
    assert.ok(messages.textContent.includes('핵심 요약'));
    assert.equal(calls.filter(item=>item.url==='/api/ai/editor').length,1);
    assert.equal(get('#ai-confirm').disabled,false);
  } else {
    assert.equal(created.length,0,action+' cannot mount the START editor');
    assert.equal(calls.filter(item=>item.url==='/api/ai/editor').length,0);
  }
  assert.ok(!calls.some(item=>item.url==='/api/ai/edit' || item.url==='/api/ai/apply'),'Rendering neither edits nor executes a plan');
}
(async()=>{
  for(const action of ['START','STATUS','STOP','RECENT','RECONNECT']) await checkChatMount(action);
  process.stdout.write('ai_editor106 plan-only DOM and chat mounting regression PASS\n');
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
