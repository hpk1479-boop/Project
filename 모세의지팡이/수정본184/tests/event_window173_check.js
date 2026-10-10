// 수정본173: the window cells of the strategy editor and their wording, on the real server schema (stdin).
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
class Element {
  constructor(tag) {
    this.tagName=tag.toUpperCase();this.children=[];this.attrs={};this.listeners={};this.dataset={};
    this._text='';this.className='';this.value='';this.disabled=false;this.type='';this.selected=false;
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
const context={window,document:{createElement:tag=>new Element(tag)}};
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'),context);
vm.runInNewContext(fs.readFileSync(process.argv[3],'utf8'),context);
const clone=value=>JSON.parse(JSON.stringify(value));
const all=root=>[root,...root.children.flatMap(all)];
const field=(editor,path)=>all(editor.element).find(item=>item.dataset.fieldPath===path);
const selects=(editor,path)=>all(field(editor,path)).filter(item=>item.tagName==='SELECT');
const inputs=(editor,path)=>all(field(editor,path)).filter(item=>item.tagName==='INPUT');
const shown=control=>(control.children.find(item=>item.selected) || {}).textContent;
function choose(control,text) {
  const option=control.children.find(item=>item.textContent===text);
  assert.ok(option,'missing option: '+text);control.value=option.value;control.fire('change');
}
function enter(control,value) {control.value=String(value);control.fire('input');}

const cancel={kind:'OZ_ALERT',tfs:['1h'],validation_mode:'BLIND',trigger_mode:'OZ',direction:'OPPOSITE',recent:{bars:6,tf:'1h'}};
const meaning={direction:'BOTH',symbols:[fixture.symbols[0]],order_mode:'SEQUENTIAL',global_combine:'ALL',
  within:{bars:10,tf:'1m'},
  steps:[{kind:'OZ_ALERT',tfs:['1m'],validation_mode:'BLIND',trigger_mode:'OZ'},
    {kind:'PERCENTILE_OUT_IN',tfs:['3m'],families:['RSI']}],
  cancel_conditions:[cancel],final:{kind:'NOTIFY'}};
const envelope={supported:true,intent:'CREATE_STRATEGY',interpretation:meaning,needs_clarification:false,
  clarification_question:null,message_ko:'기간 시험'};
const editor=window.part3IntentEditor.create({response:{result:envelope},contract:fixture.contract});
all(editor.element).find(item=>item.tagName==='BUTTON' && item.textContent==='상세보기').fire('click');
assert.deepEqual(clone(editor.getDraft().strategy),envelope,'rendering keeps the windows as written');
assert.ok(!editor.element.textContent.includes('상세 보기에서 확인'),'windows and the opposite direction are editable cells');

// The chain window: one cell, either a time or a count of one frame's bars.
const within='strategy.interpretation.within';
assert.ok(field(editor,within),'the chain window has its cell');
assert.equal(field(editor,'strategy.interpretation.within_sec'),undefined,'a new chain shows one window cell');
assert.equal(shown(selects(editor,within)[0]),'봉 수');
assert.equal(inputs(editor,within)[0].value,'10');
assert.equal(shown(selects(editor,within)[1]),'1분봉');
choose(selects(editor,within)[0],'시간');
enter(inputs(editor,within)[0],30);
assert.deepEqual(clone(editor.getDraft().strategy.interpretation.within),{seconds:1800},'minutes is the starting unit');
choose(selects(editor,within)[1],'시간');
assert.deepEqual(clone(editor.getDraft().strategy.interpretation.within),{seconds:108000});
choose(selects(editor,within)[1],'초');
assert.deepEqual(clone(editor.getDraft().strategy.interpretation.within),{seconds:30});
choose(selects(editor,within)[0],'봉 수');
enter(inputs(editor,within)[0],10);
choose(selects(editor,within)[1],'3분봉');
assert.deepEqual(clone(editor.getDraft().strategy.interpretation.within),{bars:10,tf:'3m'});
choose(selects(editor,within)[0],'없음');
assert.equal('within' in editor.getDraft().strategy.interpretation,false,'none removes the window');

// The recent window of the cancel condition and its opposite direction.
const recent='strategy.interpretation.cancel_conditions.0.recent';
assert.equal(shown(selects(editor,recent)[0]),'봉 수');
assert.equal(inputs(editor,recent)[0].value,'6');
assert.equal(shown(selects(editor,recent)[1]),'1시간봉');
const direction=selects(editor,'strategy.interpretation.cancel_conditions.0.direction')[0];
assert.equal(shown(direction),'반대 방향');
choose(selects(editor,recent)[0],'시간');enter(inputs(editor,recent)[0],6);choose(selects(editor,recent)[1],'시간');
assert.deepEqual(clone(editor.getDraft().strategy.interpretation.cancel_conditions[0].recent),{seconds:21600});

// A state condition has no recent window: no such cell.
const states=clone(envelope);states.interpretation.steps[1]={kind:'TREND',tfs:['1h']};
const second=window.part3IntentEditor.create({response:{result:states},contract:fixture.contract});
all(second.element).find(item=>item.tagName==='BUTTON' && item.textContent==='상세보기').fire('click');
assert.equal(field(second,'strategy.interpretation.steps.1.recent'),undefined);
assert.ok(field(second,'strategy.interpretation.steps.0.recent'),'an OZ condition offers one');

// A recipe written with within_sec keeps its own cell.
const legacy=clone(envelope);delete legacy.interpretation.within;legacy.interpretation.within_sec=1800;
const third=window.part3IntentEditor.create({response:{result:legacy},contract:fixture.contract});
all(third.element).find(item=>item.tagName==='BUTTON' && item.textContent==='상세보기').fire('click');
assert.ok(field(third,'strategy.interpretation.within_sec') && !field(third,'strategy.interpretation.within'));

// Wording.
const display=window.part3IntentDisplay;
const veto=display.condition({...cancel,negated:true},'LONG');
assert.ok(veto.startsWith('최근 1시간봉 6개 안에 1시간봉 무지성 올존 알림 발생 없음'),veto);
assert.ok(veto.includes('판정 방향: 반대 방향'),veto);
assert.ok(display.condition({...cancel,recent:{seconds:21600}},'LONG').startsWith('최근 6시간 안에 '));
const sentences=display.explain(meaning).join(' ');
assert.ok(sentences.includes('첫 조건부터 전체 조건이 충족될 때까지의 기간은 1분봉 10개입니다.'),sentences);
assert.ok(display.explain({...meaning,within:{seconds:1800}}).join(' ').includes('기간은 30분입니다.'));
console.log('WINDOW PASS');
