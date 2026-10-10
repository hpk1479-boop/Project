"""Run current strategy dialogs against synthetic APIs and a DOM model.

No real settings, engines or external network are touched. Browser rendering is
checked separately; these tests assert staged editing and the existing payloads.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''

const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const root = process.argv[1], scenario = process.argv[2];
class TextNode {
    constructor(text) {this.nodeType=3; this.textContent=String(text); this.parentElement=null;}
}
class Element {
    constructor(tag='div') {
        this.nodeType=1; this.tagName=tag.toUpperCase(); this.childNodes=[]; this.parentElement=null;
        this.attributes={}; this.dataset={}; this.value=''; this.checked=false; this.disabled=false;
        this.open=false; this.readOnly=false; this.listeners={}; this.style={}; this.flags=new Set();
        this.classList={add:(...names)=>names.forEach(name=>this.flags.add(name)),
            remove:(...names)=>names.forEach(name=>this.flags.delete(name)),
            contains:name=>this.flags.has(name), toggle:(name,on)=>{
                const active=on===undefined?!this.flags.has(name):on;
                active?this.flags.add(name):this.flags.delete(name); return active;}};
    }
    get className() {return [...this.flags].join(' ');}
    set className(value) {this.flags=new Set(String(value).split(/\s+/).filter(Boolean));}
    get children() {return this.childNodes.filter(child=>child.nodeType===1);}
    get firstChild() {return this.childNodes[0]||null;}
    get firstElementChild() {return this.children[0]||null;}
    get lastElementChild() {return this.children.at(-1)||null;}
    get options() {return this.children.filter(child=>child.tagName==='OPTION');}
    get selectedOptions() {return this.options.filter(child=>child.value===this.value);}
    get isConnected() {let p=this; while(p.parentElement)p=p.parentElement; return p===documentRoot;}
    get textContent() {return this.childNodes.map(child=>child.textContent).join('');}
    set textContent(value) {this.replaceChildren(new TextNode(value??''));}
    set innerHTML(value) {throw Error('Dynamic settings markup must use safe text/element APIs');}
    append(...children) {for(let child of children) {
        if(typeof child==='string')child=new TextNode(child);
        if(child.parentElement)child.remove(); child.parentElement=this; this.childNodes.push(child);
    }}
    prepend(...children) {for(let child of children.reverse()) {
        if(typeof child==='string')child=new TextNode(child);
        if(child.parentElement)child.remove(); child.parentElement=this; this.childNodes.unshift(child);
    }}
    replaceChildren(...children) {for(const child of this.childNodes)child.parentElement=null;
        this.childNodes=[]; this.append(...children);}
    before(child) {const parent=this.parentElement;if(!parent)return;
        if(child.parentElement)child.remove();child.parentElement=parent;
        parent.childNodes.splice(parent.childNodes.indexOf(this),0,child);}
    after(child) {const parent=this.parentElement;if(!parent)return;
        if(child.parentElement)child.remove();child.parentElement=parent;
        parent.childNodes.splice(parent.childNodes.indexOf(this)+1,0,child);}
    remove() {if(this.parentElement){const parent=this.parentElement;
        parent.childNodes=parent.childNodes.filter(child=>child!==this); this.parentElement=null;}}
    setAttribute(name,value) {
        this.attributes[name]=String(value);
        if(name==='class')this.className=value;
        else if(name.startsWith('data-'))this.dataset[name.slice(5).replace(/-([a-z])/g,(_,x)=>x.toUpperCase())]=String(value);
        else if(name==='open')this.open=true;
        else if(name==='checked')this.checked=true;
        else if(name==='disabled')this.disabled=true;
        else if(['id','type','value'].includes(name))this[name]=String(value);
    }
    getAttribute(name) {
        if(name==='class')return this.className;
        if(name==='id')return this.id??null;
        if(name.startsWith('data-'))return this.dataset[name.slice(5).replace(/-([a-z])/g,(_,x)=>x.toUpperCase())]??null;
        return this.attributes[name]??null;
    }
    removeAttribute(name) {delete this.attributes[name]; if(name==='open')this.open=false;}
    addEventListener(name,callback) {(this.listeners[name]??=[]).push(callback);}
    focus() {} scrollIntoView() {}
    matches(selector) {
        let rest=selector.trim();
        const tag=/^[\w-]+/.exec(rest);
        if(tag){if(this.tagName!==tag[0].toUpperCase())return false; rest=rest.slice(tag[0].length);}
        while(rest) {
            let match=/^([.#])([\w-]+)/.exec(rest);
            if(match){if(match[1]==='#'?this.id!==match[2]:!this.flags.has(match[2]))return false;
                rest=rest.slice(match[0].length); continue;}
            match=/^\[([\w-]+)(?:=["']?([^\]"']*)["']?)?\]/.exec(rest);
            if(match){const value=this.getAttribute(match[1]);
                if(value===null || (match[2]!==undefined&&value!==match[2]))return false;
                rest=rest.slice(match[0].length); continue;}
            throw Error('Unsupported selector in settings DOM model: '+selector);
        }
        return true;
    }
    querySelectorAll(selector) {
        const groups=selector.split(',').map(group=>group.trim().split(/\s+/));
        const result=[];
        const visit=node=>{for(const child of node.children){
            if(groups.some(parts=>{
                if(!child.matches(parts.at(-1)))return false;
                let parent=child.parentElement;
                for(let i=parts.length-2;i>=0;i--){
                    while(parent&&!parent.matches(parts[i]))parent=parent.parentElement;
                    if(!parent)return false; parent=parent.parentElement;
                }
                return true;
            }))result.push(child); visit(child);
        }}; visit(this); return result;
    }
    querySelector(selector) {return this.querySelectorAll(selector)[0]||null;}
    closest(selector) {for(let node=this;node;node=node.parentElement)if(node.matches(selector))return node;return null;}
}
const documentRoot=new Element('document');
const html=fs.readFileSync(root+'/index.html','utf8');
const stack=[documentRoot], voidTags=new Set(['area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr']);
for(const token of html.matchAll(/<!--[\s\S]*?-->|<\/?([a-z][\w-]*)\b([^>]*)>|([^<]+)/gi)) {
    if(token[0].startsWith('<!--'))continue;
    if(token[3]) {stack.at(-1).append(new TextNode(token[3]));continue;}
    if(!token[1])continue;
    const tag=token[1].toLowerCase();
    if(token[0].startsWith('</')) {
        for(let i=stack.length-1;i>0;i--)if(stack[i].tagName.toLowerCase()===tag){stack.length=i;break;}
        continue;
    }
    const el=new Element(tag);
    for(const attr of token[2].matchAll(/([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g))
        el.setAttribute(attr[1],attr[2]??attr[3]??attr[4]??'');
    stack.at(-1).append(el); if(!voidTags.has(tag))stack.push(el);
}
const document={body:documentRoot.querySelector('body'),querySelector:s=>documentRoot.querySelector(s),
    querySelectorAll:s=>documentRoot.querySelectorAll(s),createElement:tag=>new Element(tag),
    createTextNode:text=>new TextNode(text),getElementById:id=>documentRoot.querySelector('#'+id)};


const modalSnapshots=[];
Element.prototype.showModal = function() {
    modalSnapshots.push({id:this.id,cards:document.querySelectorAll('#strategy-settings-cards [data-strategy]').length});
    this.open=true;
};
Element.prototype.close = function() {this.open=false;};
Element.prototype.insertBefore = function(child,before) {
    if(child.parentElement)child.remove();
    child.parentElement=this;
    const index=this.childNodes.indexOf(before);
    if(index<0)this.childNodes.push(child);else this.childNodes.splice(index,0,child);
};
const originalMatches=Element.prototype.matches;
Element.prototype.matches=function(selector) {
    if(selector.endsWith(':checked'))return this.checked&&originalMatches.call(this,selector.slice(0,-8));
    return originalMatches.call(this,selector);
};
class Option extends Element {
    constructor(text,value) {super('option');this.textContent=text;this.value=value;}
}
const choices=['일반 올존','무지성 올존','일반 브레이커 올존','무지성 브레이커 올존'];
const labels={MAIN_ASIA:'아시아',MAIN_LONDON:'런던',MAIN_NEWYORK:'뉴욕'};
const times={MAIN_ASIA:'0900-1530',MAIN_LONDON:'1600-2000',MAIN_NEWYORK:'2130-2400'};
const original={SPECIAL1:{enabled:true,trigger:null,time_filters:null,default_trigger:choices[0],default_time_filters:0},
    SPECIAL2:{enabled:false,trigger:choices[2],time_filters:{MAIN_ASIA:{enabled:true,start:'1000'},MAIN_LONDON:{enabled:false}},
        default_trigger:choices[1],default_time_filters:{MAIN_LONDON:'1700-2000'}}};
const clone=value=>JSON.parse(JSON.stringify(value));
let liveData={items:clone(original),trigger_choices:choices,session_labels:labels,session_times:times,
    default_time_filters:{SPECIAL1:0,SPECIAL2:{MAIN_LONDON:'1700-2000'}},settings_error:null,needs_recovery:false};
const backtest={specials:Object.keys(original),special_settings:clone(original),trigger_choices:choices,
    default_triggers:{SPECIAL1:choices[0],SPECIAL2:choices[1]},default_time_filters:liveData.default_time_filters,
    session_labels:labels,session_times:times,symbol:'XAUUSD+',symbols:['XAUUSD+'],start:'2026-09-01',end:'2026-10-01',
    mode:'BAR',watch_chat_id:'BACKTEST',spread_points:{},warehouse_set:true};
const calls=[];let failWrite=false,failLoad=false,holdLive=null,allowRecovery=true,specialState='연결 대기';
const context=vm.createContext({console,document,Option,Event:class {constructor(type){this.type=type;}},
    window:{dispatchEvent(){},addEventListener(){},confirm:()=>allowRecovery},setInterval:()=>1,setTimeout:()=>1,clearTimeout(){},
    api:async(route,body)=>{
        calls.push({route,body:body&&clone(body)});
        if(route.startsWith('mo/live/status'))return {modules:{STAFF:{name:'STAFF',state:'연결 대기'},ENGINE:{name:'ENGINE',state:'연결 대기'},SPECIAL1:{name:'전략 1',preset:true,state:specialState}},lines:[],pipe_connected:false};
        if(route==='mo/backtest/options')return clone(backtest);
        if(route==='mo/live/stop')return {ok:true,message:'라이브 엔진 종료 완료',warnings:[]};
        assert.equal(route,'mo/live/specials');
        if(body){if(failWrite)throw Error('저장 실패');liveData.items={...liveData.items,...clone(body.items)};
            liveData.needs_recovery=false;liveData.settings_error=null;return {ok:true,message:'저장 완료'};}
        if(holdLive)await holdLive;
        if(failLoad)throw Error('전략 목록 불러오기 실패');
        return clone(liveData);
    }
});
const run=code=>vm.runInContext(code,context);
const get=id=>document.querySelector('#'+id);
const writes=()=>calls.filter(call=>call.body);
const json=code=>JSON.parse(JSON.stringify(run(code)));
const click=id=>get(id).onclick();
const card=name=>document.querySelector('[data-strategy="'+name+'"]');
const toggle=name=>{const el=card(name).querySelector('input');el.checked=!el.checked;el.onchange();};
const openProfile=name=>card(name).querySelector('.moses-strategy-profile').onclick();
const selectProfile=value=>{
    // Dynamic input.value is a property, so locate these radios without attribute selectors.
    const radio=[...get('strategy-editor-body').querySelectorAll('input')].find(el=>el.value===value);
    assert(radio);radio.checked=true;radio.onchange();};
const openTime=name=>card(name).querySelector('.moses-strategy-time').onclick();
const timeField=(label,part)=>[...get('strategy-editor-body').querySelectorAll('input')].find(input=>
    input.getAttribute('aria-label')===label+(part==='start'?' 시작 시각':' 종료 시각'));
const enterTime=(label,part,value)=>{timeField(label,part).value=value;};

(async()=>{
    run(fs.readFileSync(root+'/unified.js','utf8'));await new Promise(resolve=>setImmediate(resolve));
    get('mo-bt-target').value='SPECIAL';get('mo-bt-result-mode').value='ALERT_ONLY';
    // Preserve parent/child selection effects in the native DOM model.
    if(scenario==='live_cancel') {
        await click('live-open-settings');assert(get('strategy-settings-dialog').open);
        assert.equal(run('moState.livePage'),'main');assert(!get('live-main-page').classList.contains('hidden'));
        toggle('SPECIAL1');openProfile('SPECIAL1');selectProfile(choices[2]);click('strategy-editor-save');
        assert.equal(json('moStrategyPopup.draft.SPECIAL1').trigger,choices[2]);assert.equal(writes().length,0);
        click('strategy-settings-cancel');assert(!get('strategy-settings-dialog').open);assert.equal(writes().length,0);
        await click('live-open-settings');assert.equal(json('moStrategyPopup.draft.SPECIAL1').enabled,true);
        assert.equal(json('moStrategyPopup.draft.SPECIAL1').trigger,null);
    } else if(scenario==='backtest_cancel') {
        await run('moView("backtest")');const before=json('moBacktestRequest()');
        await click('mo-bt-edit-specials');assert.equal(run('moState.backtestPage'),'setup');
        toggle('SPECIAL1');openTime('SPECIAL2');enterTime('아시아','end','12:00');click('strategy-editor-save');
        assert.deepEqual(json('moBacktestRequest()'),before);assert.equal(writes().length,0);
        click('strategy-settings-close');assert.deepEqual(json('moBacktestRequest()'),before);
        await click('mo-bt-edit-specials');assert.equal(json('moStrategyPopup.draft.SPECIAL1').enabled,true);
        assert.equal(json('moStrategyPopup.draft.SPECIAL2').time_filters.MAIN_ASIA.end,undefined);
    } else if(scenario==='corrupt_profile') {
        liveData.items.SPECIAL1.load_error='현재 프로필을 선택하세요.';liveData.items.SPECIAL1.trigger='옛 값';
        await click('live-open-settings');await click('strategy-settings-apply');assert.equal(writes().length,0);
        assert(get('strategy-settings-message').textContent.includes('현재 프로필'));assert(get('strategy-settings-dialog').open);
        openProfile('SPECIAL1');click('strategy-editor-save');assert(get('strategy-editor-dialog').open);
        selectProfile(choices[0]);click('strategy-editor-save');await click('strategy-settings-apply');
        assert.equal(writes().length,1);assert.equal(writes()[0].body.items.SPECIAL1.trigger,null);
    } else if(scenario==='time_cancel_invalid') {
        await click('live-open-settings');const before=json('moStrategyPopup.draft.SPECIAL2');openTime('SPECIAL2');
        enterTime('아시아','start','25:00');click('strategy-editor-save');assert(get('strategy-editor-dialog').open);
        assert(get('strategy-editor-error').textContent.includes('시각'));assert.deepEqual(json('moStrategyPopup.draft.SPECIAL2'),before);
        enterTime('아시아','start','23:00');click('strategy-editor-cancel');assert.deepEqual(json('moStrategyPopup.draft.SPECIAL2'),before);
        openTime('SPECIAL2');enterTime('아시아','start','23:00');enterTime('아시아','end','24:00');click('strategy-editor-save');
        assert.equal(json('moStrategyPopup.draft.SPECIAL2').time_filters.MAIN_ASIA.end,'2400');assert.equal(writes().length,0);
    } else if(scenario==='live_status_retains_draft') {
        await click('live-open-settings');toggle('SPECIAL1');const before=json('moStrategyPopup.draft');
        specialState='연결중';await run('moLiveStatus()');
        assert(card('SPECIAL1').querySelector('.moses-strategy-state').textContent.includes('연결중'));
        assert.deepEqual(json('moStrategyPopup.draft'),before);assert.equal(card('SPECIAL1').querySelector('input').checked,false);
        specialState='오류';await run('moLiveStatus()');assert(card('SPECIAL1').querySelector('.moses-strategy-state').classList.contains('error'));
        assert.equal(writes().length,0);
    } else if(scenario==='unchanged_time_preserved') {
        await run('moView("backtest")');const before=json('moBacktestRequest()');await click('mo-bt-edit-specials');
        await click('strategy-settings-apply');assert.deepEqual(json('moBacktestRequest()'),before);
        await click('mo-bt-edit-specials');openTime('SPECIAL2');
        assert.equal(timeField('아시아','start').value,'10:00');
        assert.equal(timeField('런던','start').value,'17:00');
        click('strategy-editor-cancel');await click('strategy-settings-apply');assert.deepEqual(json('moBacktestRequest()'),before);
    } else if(scenario==='save_error_retains_draft') {
        await click('live-open-settings');toggle('SPECIAL1');failWrite=true;await click('strategy-settings-apply');
        assert(get('strategy-settings-dialog').open);assert.equal(json('moStrategyPopup.draft.SPECIAL1').enabled,false);
        assert(get('strategy-settings-message').textContent.includes('저장 실패'));assert(!get('strategy-settings-apply').disabled);
        failWrite=false;await click('strategy-settings-apply');assert(!get('strategy-settings-dialog').open);
        assert.equal(liveData.items.SPECIAL1.enabled,false);
    } else if(scenario==='load_cancel_race') {
        let release;holdLive=new Promise(resolve=>{release=resolve;});const pending=click('live-open-settings');
        assert(!get('strategy-settings-dialog').open);run('moCloseStrategyPopup()');release();await pending;
        assert(!get('strategy-settings-dialog').open);assert.equal(run('moStrategyPopup.mode'),null);assert.equal(writes().length,0);
        assert.equal(modalSnapshots.filter(row=>row.id==='strategy-settings-dialog').length,0);
    } else if(scenario==='load_once_ready') {
        let release;holdLive=new Promise(resolve=>{release=resolve;});const pending=click('live-open-settings');
        assert(!get('strategy-settings-dialog').open);assert.equal(modalSnapshots.length,0);
        const before=calls.filter(call=>call.route==='mo/live/specials').length;
        await click('live-open-settings');assert.equal(calls.filter(call=>call.route==='mo/live/specials').length,before);
        release();await pending;
        assert(get('strategy-settings-dialog').open);assert.equal(modalSnapshots.length,1);
        assert.deepEqual(modalSnapshots[0],{id:'strategy-settings-dialog',cards:2});
        assert.equal(run('moStrategyPopup.busy'),false);assert.equal(writes().length,0);
    } else if(scenario==='load_navigation_race') {
        let release;holdLive=new Promise(resolve=>{release=resolve;});const pending=click('live-open-settings');
        assert(!get('strategy-settings-dialog').open);await run('moView("strategy")');release();await pending;
        assert(!get('strategy-settings-dialog').open);assert.equal(run('moStrategyPopup.mode'),null);
        assert.equal(run('moStrategyPopup.busy'),false);assert.equal(modalSnapshots.length,0);
        await run('moView("live")');holdLive=null;await click('live-open-settings');
        assert(get('strategy-settings-dialog').open);assert.equal(modalSnapshots.length,1);
        assert.equal(writes().length,0);
    } else if(scenario==='load_error_retry') {
        failLoad=true;await click('live-open-settings');
        assert(get('strategy-settings-dialog').open);assert.equal(modalSnapshots.length,1);
        assert.equal(run('moStrategyPopup.busy'),false);assert.equal(run('moStrategyPopup.draft'),null);
        assert(get('strategy-settings-message').textContent.includes('불러오기 실패'));
        assert(get('strategy-settings-apply').disabled);
        click('strategy-settings-cancel');failLoad=false;await click('live-open-settings');
        assert(get('strategy-settings-dialog').open);assert.equal(modalSnapshots.length,2);
        assert.equal(modalSnapshots[1].cards,2);assert(!get('strategy-settings-apply').disabled);
        assert.equal(writes().length,0);
    } else if(scenario==='load_other_part_immediate') {
        let release;holdLive=new Promise(resolve=>{release=resolve;});const previous=click('live-open-settings');
        assert(!get('strategy-settings-dialog').open);await run('moView("backtest")');
        assert.equal(run('moStrategyPopup.mode'),null);assert.equal(run('moStrategyPopup.busy'),false);
        const before=calls.filter(call=>call.route==='mo/backtest/options').length;
        await click('mo-bt-edit-specials');
        assert.equal(calls.filter(call=>call.route==='mo/backtest/options').length,before+1);
        assert(get('strategy-settings-dialog').open);assert.equal(run('moStrategyPopup.mode'),'backtest');
        assert.equal(modalSnapshots.length,1);assert.equal(modalSnapshots[0].cards,2);
        const draft=json('moStrategyPopup.draft');release();await previous;
        assert(get('strategy-settings-dialog').open);assert.equal(run('moStrategyPopup.mode'),'backtest');
        assert.equal(run('moStrategyPopup.busy'),false);assert.deepEqual(json('moStrategyPopup.draft'),draft);
        assert.equal(modalSnapshots.length,1);assert.equal(writes().length,0);
    } else if(scenario==='recovery_confirmation') {
        liveData.needs_recovery=true;liveData.settings_error='전략 설정 파일 손상';
        await click('live-open-settings');allowRecovery=false;await click('strategy-settings-apply');
        assert.equal(writes().length,0);assert(get('strategy-settings-dialog').open);
        allowRecovery=true;await click('strategy-settings-apply');assert.equal(writes().length,1);
        assert.equal(writes()[0].body.recover,true);assert(!get('strategy-settings-dialog').open);
    } else if(scenario==='stop_stays_dashboard') {
        await click('live-stop');assert.equal(writes().length,1);assert.equal(writes()[0].route,'mo/live/stop');
        assert.equal(run('moState.livePage'),'main');assert(!get('live-main-page').classList.contains('hidden'));
        assert(get('live-action-message').textContent.includes('종료 완료'));
        const direct=get('live-main-page').children.filter(el=>!el.classList.contains('hidden'));
        assert.equal(direct.length,4);assert(get('live-action-message').closest('.moses-live-status'));
        await run('moLiveStatus()');assert(get('live-action-message').textContent.includes('종료 완료'));
        assert(!get('live-start').disabled);assert(!get('live-stop').disabled);
    } else if(scenario==='desktop_close_progress') {
        run('window.mosesWindowClosing("창을 닫기 위해 기록을 저장하고 있습니다.")');
        assert(get('live-start').disabled&&get('live-stop').disabled);
        assert(get('live-action-message').textContent.includes('기록'));await click('live-start');assert.equal(writes().length,0);
        await run('moLiveStatus()');assert(get('live-action-message').textContent.includes('기록'));
        run('window.mosesWindowClosing("종료 실패, 다시 시도하세요.",true)');
        assert(!get('live-start').disabled&&!get('live-stop').disabled);assert(get('live-action-message').classList.contains('moses-warning'));
    } else throw Error('Unknown scenario: '+scenario);
    console.log(scenario+' PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', [
    # Later revisions changed these on purpose, so their old-screen scenarios were deleted (수정본162):
    # the backtest takes one strategy (149), the time editor stays open after saving, [기본값] only fills
    # the fields, and times equal to the defaults are kept as "follow the defaults" (null), not a copy.
    'live_cancel', 'backtest_cancel', 'corrupt_profile', 'time_cancel_invalid',
    'live_status_retains_draft', 'unchanged_time_preserved', 'save_error_retains_draft', 'load_cancel_race',
    'load_once_ready', 'load_navigation_race', 'load_error_retry', 'load_other_part_immediate',
    'recovery_confirmation', 'stop_stays_dashboard', 'desktop_close_progress',
])
def test_strategy_popup_behavior(scenario, tmp_path):
    node = shutil.which('node')
    assert node, 'Node.js is required by current web verification'
    result = subprocess.run([node, '-e', SCRIPT, str(ROOT / 'Part3/web'), scenario],
                            cwd=tmp_path, capture_output=True, text=True,
                            encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert scenario + ' PASS' in result.stdout
