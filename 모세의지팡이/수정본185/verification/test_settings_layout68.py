"""Exercise the actual settings UI with synthetic API data and a native DOM model.

No application configuration, engines, browser, or network is used. The model
keeps descendant selectors and details.open behavior so folding a group cannot
silently change which existing fields are saved.
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
    dispatchEvent(event) {for(const callback of this.listeners[event?.type]??[])callback(event);return true;}
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
// 수정본138: the server sends only the settings the engine reads (unified_settings.LIVE_KEYS + POINT_*).
const liveKeys=['SYMBOLS','TELEGRAM_TOKEN','TELEGRAM_CHAT_ID','TELEGRAM_COMMAND_CHAT_IDS',
    'PRIVATE_LIVE_ALERTS_ENABLED','PRIVATE_ECONOMY_ALERTS_ENABLED','ECONOMY_ENABLED','LIVE_RECORD_ENABLED',
    'STAFF_PIPE_NAME','STAFF_STALE_SEC','WONBI_SIGMA','ASIA','LONDON','NEWYORK',
    'MAIN_ASIA','MAIN_LONDON','MAIN_NEWYORK','OPENING_ASIA','OPENING_LONDON','OPENING_NEWYORK',
    'ECONOMY_FETCH_SEC','ECONOMY_POLL_SEC','TRACE_ENABLED','TRACE_RING_LINES','TRACE_WARNING_BYTES',
    'TRACE_WARNING_BACKUPS','POINT_XAUUSD+'];
// A row the screen does not know is never drawn, even if a response carries it.
const unknownKey='FUTURE_OPTION';
const secrets=new Set(['TELEGRAM_TOKEN','TELEGRAM_CHAT_ID','TELEGRAM_COMMAND_CHAT_IDS']);
const sample=key=>key.endsWith('_ENABLED')?'true':key.endsWith('_SEC')||key.endsWith('_LINES')?'10':
    key==='STAFF_PIPE_NAME'?'\\\\.\\pipe\\Synthetic':key===unknownKey?'<img src=x onerror=bad()>':'synthetic-value';
let config={live:[...liveKeys,unknownKey].map(key=>({key,secret:secrets.has(key),configured:secrets.has(key),value:secrets.has(key)?'':sample(key)})),
    part2:{cores:4,broker_symbols:{'XAUUSD+':'GOLD'}},
    connections:{warehouse:'synthetic warehouse',python_executable:''},
    ai:{provider:'ollama',model:'qwen3.5:4b',base_url:'http://127.0.0.1:11434',timeout:90}};
const calls=[]; let loadFailure=false,saveFailure=false,holdLoad=null;
const context=vm.createContext({console,document,window:{dispatchEvent(){},addEventListener(){},confirm:()=>true},
    Event:class {constructor(type){this.type=type;}},Option:Element,
    setInterval:()=>1,setTimeout:()=>1,clearTimeout(){},
    api:async(route,body)=>{
        calls.push({route,body:body&&JSON.parse(JSON.stringify(body))});
        if(route.startsWith('mo/live/status'))return {modules:{STAFF:{state:'대기'},ENGINE:{state:'대기'}},lines:[]};
        if(route==='ai/models')return {available:true,models:['qwen3.5:4b','qwen3:8b']};
        assert.equal(route,'mo/settings');
        if(body) {
            if(saveFailure)throw Error('STAFF_PIPE_NAME: Windows Named Pipe 형식이어야 합니다.');
            assert.equal(body.group,'live');
            for(const [key,value] of Object.entries(body.changes)){
                const row=config.live.find(row=>row.key===key);assert(row,'unknown saved setting');
                if(row.secret){row.configured=value!==null;row.value='';}else row.value=value;
            }
            return {ok:true,message:'저장 완료'};
        }
        if(loadFailure)throw Error('설정 읽기 실패');
        if(holdLoad)return holdLoad;
        return JSON.parse(JSON.stringify(config));
    }});
const run=code=>vm.runInContext(code,context);
const get=id=>document.getElementById(id), live=()=>get('settings-live-fields');
const field=key=>live().querySelector('[data-setting="'+key+'"]');
const advanced=()=>live().querySelector('details');
const writes=()=>calls.filter(call=>call.route==='mo/settings'&&call.body);
const clean=value=>JSON.parse(JSON.stringify(value));
const sectionTitle=el=>el.closest('fieldset')?.querySelector('legend')?.textContent||'';
async function load(){await run('moView("settings")');}
function allClosed(){assert(get('view-settings').querySelectorAll('details').every(el=>el.open===false));}
(async()=>{
    run(fs.readFileSync(root+'/unified.js','utf8'));
    await new Promise(resolve=>setImmediate(resolve));
    if(scenario==='grouping') {
        await load();const fields=live().querySelectorAll('[data-setting]');
        assert.equal(fields.length,liveKeys.length);
        assert.equal(new Set(fields.map(el=>el.dataset.setting)).size,liveKeys.length);
        assert.deepEqual(fields.map(el=>el.dataset.setting).sort(),liveKeys.slice().sort());
        assert.equal(field(unknownKey),null);
        assert.equal(advanced().firstElementChild.tagName,'SUMMARY');
        assert.match(advanced().firstElementChild.textContent,/^상세 설정/);
        allClosed();
        for(const key of ['SYMBOLS','ECONOMY_ENABLED','LIVE_RECORD_ENABLED','TELEGRAM_TOKEN','TELEGRAM_CHAT_ID',
                          'TELEGRAM_COMMAND_CHAT_IDS','PRIVATE_LIVE_ALERTS_ENABLED','PRIVATE_ECONOMY_ALERTS_ENABLED'])
            assert.equal(field(key).closest('details'),null,key+' belongs in everyday settings');
        for(const key of ['STAFF_PIPE_NAME','STAFF_STALE_SEC','WONBI_SIGMA','POINT_XAUUSD+','MAIN_LONDON',
                          'ECONOMY_POLL_SEC','TRACE_ENABLED','TRACE_WARNING_BACKUPS'])
            assert.equal(field(key).closest('details'),advanced(),key+' belongs in advanced settings');
        // 수정본138 merged the one-field groups into "기본".
        assert.equal(sectionTitle(field('SYMBOLS')),'기본');
        assert.equal(sectionTitle(field('ECONOMY_ENABLED')),'기본');
        assert.match(sectionTitle(field('TELEGRAM_TOKEN')),/텔레그램/);
        assert.match(sectionTitle(field('STAFF_PIPE_NAME')),/MT5 연결/);
        assert.equal(writes().length,0);
    } else if(scenario==='reenter') {
        await load();get('view-settings').querySelectorAll('details').forEach(el=>el.open=true);
        await run('moView("strategy")');await run('moView("settings")');allClosed();
        advanced().open=true;await run('moSettingsLoad()');allClosed();
        assert.equal(writes().length,0);
    } else if(scenario==='loading') {
        await load();advanced().open=true;await run('moView("strategy")');
        let release;holdLoad=new Promise(resolve=>release=resolve);
        const waiting=run('moView("settings")');allClosed();
        release(clean(config));await waiting;holdLoad=null;allClosed();
        advanced().open=true;await run('moView("strategy")');loadFailure=true;
        await run('moView("settings")');allClosed();
        assert.match(get('settings-message').textContent,/설정 읽기 실패/);
    } else if(scenario==='save_unchanged') {
        await load();await run('moSettingsSave("live")');
        // 수정본138: the result is shown next to the group's save button.
        assert.equal(writes().length,0);
        assert.equal(document.querySelector('[data-save-status="live"]').textContent,'변경한 값이 없습니다');
        allClosed();
    } else if(scenario==='save_folded') {
        await load();assert.equal(advanced().open,false);
        field('STAFF_STALE_SEC').value='25';field('SYMBOLS').value='XAUUSD+,NAS100';
        await run('moSettingsSave("live")');
        assert.deepEqual(writes().map(call=>call.body),[{group:'live',changes:{
            STAFF_STALE_SEC:'25',SYMBOLS:'XAUUSD+,NAS100'}}]);
        assert.equal(field('STAFF_STALE_SEC').value,'25');assert.equal(field('STAFF_STALE_SEC').dataset.original,'25');
        assert.equal(field('SYMBOLS').value,'XAUUSD+,NAS100');allClosed();
        await run('moSettingsSave("live")');assert.equal(writes().length,1);
    } else if(scenario==='save_failure') {
        await load();field('STAFF_PIPE_NAME').value='bad';advanced().open=true;saveFailure=true;
        await assert.rejects(()=>run('moSettingsSave("live")'),/MT5 연결 통로: Windows 연결 통로 형식/);
        assert.equal(field('STAFF_PIPE_NAME').value,'bad');assert.equal(advanced().open,true);
        assert.equal(writes().length,1);
        assert.equal(config.live.find(row=>row.key==='STAFF_PIPE_NAME').value,sample('STAFF_PIPE_NAME'));
    } else if(scenario==='safe_unknown') {
        config.live.find(row=>row.key==='TELEGRAM_CHAT_ID').value='stored synthetic secret must never render';
        await load();assert.equal(field(unknownKey),null);
        assert.equal(live().querySelectorAll('img').length,0);
        assert(!live().textContent.includes('onerror'));
        assert(!live().textContent.includes('stored synthetic secret must never render'));
        assert.equal(field('TELEGRAM_CHAT_ID').value,'');
        await run('moSettingsSave("live")');assert.equal(writes().length,0);
    } else if(scenario==='basic_only') {
        config.live=config.live.filter(row=>['SYMBOLS','TELEGRAM_TOKEN','ECONOMY_ENABLED'].includes(row.key));
        await load();assert.equal(advanced(),null);
        assert.equal(live().querySelectorAll('[data-setting]').length,3);
        field('SYMBOLS').value='NAS100';await run('moSettingsSave("live")');
        assert.deepEqual(writes().at(-1).body.changes,{SYMBOLS:'NAS100'});
        assert.equal(advanced(),null);
    } else if(scenario==='research_label') {
        const menu=document.querySelector('.moses-nav [data-moses-view="strategy"]');
        assert.equal(menu.textContent,'AI 전략연구');
        assert.match(get('view-strategy').querySelector('.ai-heading h2').textContent,/AI 전략연구/);
        await run('moView("strategy")');
        assert.equal(document.body.dataset.mosesView,'strategy');
        assert.equal(get('view-strategy').classList.contains('hidden'),false);
    } else throw Error('Unknown scenario: '+scenario);
    console.log('settings layout '+scenario+' PASS');
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', [
    # 수정본138 removed the per-field help text and the Telegram save through the group save
    # (each Telegram field saves by its own [확인]); those two scenarios were deleted.
    'grouping', 'reenter', 'loading', 'save_unchanged', 'save_folded',
    'save_failure', 'safe_unknown', 'basic_only', 'research_label',
])
def test_current_settings_layout_and_existing_save_contract(tmp_path, scenario):
    node = shutil.which('node')
    assert node, 'Node.js is required by current web verification'
    result = subprocess.run([node, '-e', SCRIPT, str(ROOT / 'Part3/web'), scenario],
                            cwd=tmp_path, capture_output=True, text=True,
                            encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'settings layout {scenario} PASS' in result.stdout
