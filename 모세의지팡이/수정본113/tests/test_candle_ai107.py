"""Candle meaning travels through the common contract, AI and editable display."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3'), str(ROOT / 'Part1/program')]
from common_ai import external_prompt, reference_pack, security
from common_ai.schema_contract import contract_schema
from lab.ai import research_editor, research_presets, schema
from lab.ai.tools import ReadOnlyWorkspace
from lab.compiler import compile_recipe
from strategy_recipe.contract import execution_plan
import moses_language


def intent(side='BULL', bar_state='FORMING', direction='LONG'):
    return {'supported': True, 'intent': 'CREATE_STRATEGY',
        'interpretation': {'symbols': ['XAUUSD+'], 'direction': direction,
            'steps': [], 'order_mode': 'SIMULTANEOUS',
            'final_conditions': [{'kind': 'CANDLE_STATE', 'tfs': ['1h'],
                'side': side, 'bar_state': bar_state}],
            'final': {'kind': 'OZ', 'tfs': ['1m'], 'direction': direction,
                'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'}},
        'needs_clarification': False, 'clarification_question': None,
        'message_ko': '캔들 조건을 최종 알림 시점에 확인합니다.'}


@pytest.mark.parametrize('side', ['BULL', 'BEAR'])
@pytest.mark.parametrize('bar_state', ['FORMING', 'CLOSED'])
@pytest.mark.parametrize('direction', ['LONG', 'SHORT'])
def test_candle_condition_survives_schema_lowering_and_generated_code(side, bar_state, direction):
    value = intent(side, bar_state, direction)
    original = copy.deepcopy(value)
    Draft202012Validator(schema.output_schema()).validate(value)
    Draft202012Validator(contract_schema(schema.output_schema())).validate(value)
    checked = schema.validate_intent(value)
    step = checked['interpretation']['final_conditions'][0]
    assert step['side'] == side and step['bar_state'] == bar_state
    assert checked['interpretation']['direction'] == direction
    assert not checked['interpretation']['steps']
    plan = execution_plan(checked['interpretation'], schema.contract_context())
    assert plan['meaning']['final_conditions'][0]['side'] == side
    recipe = schema.recipe_from_intent(checked)
    source = compile_recipe(recipe, 'Test_SPECIAL107.py')
    compiled = {}
    exec(compile(source, '<candle-contract-test>', 'exec'), compiled)
    assert compiled['_INTENT_PLAN']['final_conditions'][0]['side'] == side
    assert compiled['_INTENT_PLAN']['final_conditions'][0]['bar_state'] == bar_state
    assert callable(compiled['register'])
    assert value == original


@pytest.mark.parametrize('changes', [
    {'side': None}, {'side': 'ABOVE'}, {'side': 'LONG'},
    {'bar_state': None}, {'bar_state': 'UNSPECIFIED'}, {'bar_state': 'UNKNOWN'},
    {'slow_period': 1}, {'ma_family': 'SMA'}])
def test_candle_fields_are_required_and_do_not_accept_other_condition_parameters(changes):
    value = intent()
    row = value['interpretation']['final_conditions'][0]
    for key, item in changes.items():
        if item is None:
            row.pop(key)
        else:
            row[key] = item
    assert not Draft202012Validator(schema.output_schema()).is_valid(value)
    assert not Draft202012Validator(contract_schema(schema.output_schema())).is_valid(value)
    with pytest.raises(ValueError):
        schema.validate_intent(value)
    errors = research_editor.schema_errors(value, schema.output_schema(), ['strategy'])
    assert any(error['path'][:4] == ['strategy', 'interpretation', 'final_conditions', 0] for error in errors)


def test_local_and_remote_receive_same_static_candle_meaning_and_language_terms():
    vocabulary = ReadOnlyWorkspace().vocabulary()
    clean = security.safe_tool_result('vocabulary', vocabulary)
    assert clean['intent_parameters']['CANDLE_STATE'] == ['side']
    assert clean['candle_state'] == vocabulary['candle_state']
    assert clean['candle_state']['sides'] == ['BULL', 'BEAR']
    assert clean['candle_state']['bar_states'] == ['FORMING', 'CLOSED']
    assert clean['candle_state']['default_bar_state'] == 'FORMING'
    query = '골드 2시간 진행봉이 음봉일 때 3분 매수 올존을 알려 주세요'
    messages = [{'role': 'system', 'content': json.dumps({'purpose': 'strategy',
        'moses_contract': {'vocabulary': vocabulary}}, ensure_ascii=False)},
        {'role': 'user', 'content': json.dumps({'message': query}, ensure_ascii=False)}]
    local, _, _ = security.external_payload(messages, [], schema.output_schema(), settings={'provider': 'ollama'})
    local = external_prompt.attach_language_reference(local, {'provider': 'ollama'})
    remote, _, transmitted = external_prompt.prepare(messages, [], schema.output_schema(), {'provider': 'gemini'})
    assert json.loads(local[0]['content'])['moses_contract']['vocabulary']['candle_state'] == clean['candle_state']
    conventions = remote[0]['content'].split('Language conventions: ', 1)[1]
    remote_meaning, _ = json.JSONDecoder().raw_decode(conventions)
    assert remote_meaning['candle_state'] == clean['candle_state']
    assert transmitted == schema.output_schema()
    references = lambda rows: next(row for row in rows if row['content'].startswith(external_prompt._LANGUAGE_REFERENCE_PREFIX))
    assert references(local) == references(remote)
    assert 'CANDLE_STATE' in references(local)['content']
    assert 'final_conditions' in clean['candle_state']['final_conditions']
    assert 'neither' in clean['candle_state']['flat']
    assert 'independent' in clean['candle_state']['trade_direction']


@pytest.mark.parametrize('query', [
    '1시간이 양봉인 동안 1분 매수 올존',
    '3시간 확정봉이 음봉이면 2분 매도 올존',
    '7분 캔들 시가보다 현재가가 위에 있어야 합니다',
    '4시간 bearish candle이면 매수 올존'])
def test_dictionary_selects_candle_topic_without_interpreting_the_sentence(query):
    assert 'CANDLE_STATE' in reference_pack.relevant_kinds(query)
    assert 'CANDLE_STATE' in external_prompt.language_reference(query)['conditions']
    assert 'CANDLE_STATE' in moses_language.public_vocabulary()['condition_terms']


def test_candle_recipe_example_keeps_candle_side_separate_from_trade_direction():
    text = research_presets.example(intent('BEAR', 'CLOSED', 'LONG')['interpretation'], '캔들 시험')
    assert '캔들 상태: 음봉' in text and '방향: 매수' in text
    assert '확정봉 기준' in text and '1시간봉' in text
    assert '최종 행동 시 다시 확인할 조건' in text


def test_summary_and_details_show_and_edit_candle_side_and_bar_state():
    node = shutil.which('node')
    assert node, 'Part3 표시 검증에 필요한 Node.js가 없습니다.'
    value = intent('BULL', 'FORMING', 'SHORT')
    contract = research_editor.contract({}, value)
    result = subprocess.run([node, '-e', _DISPLAY_CHECK, str(ROOT)],
        input=json.dumps({'strategy': value, 'contract': contract}, ensure_ascii=False),
        text=True, encoding='utf-8', capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['passed'] == 9


def test_distribution_selects_current_candle_code_dictionary_and_display(tmp_path):
    sys.path.insert(0, str(ROOT / '통합설치'))
    from releasekit.builder import source_paths, prepare_data
    selected = dict((rel.as_posix(), path) for rel, path in source_paths(ROOT))
    required = {'Part1/program/strategy_recipe/contract.py', 'Part1/program/strategy_recipe/port.py',
        'Part3/lab/ai/schema.py', 'Part3/lab/ai/tools.py', 'Part3/lab/ai/research_presets.py',
        'common_ai/security.py', 'common_ai/external_prompt.py', 'moses_language/language.json',
        'Part3/web/ai_editor.js', 'Part3/web/ai_display.js'}
    assert required <= set(selected)
    data = ['moses_language/language.json', 'Part3/web/ai_editor.js', 'Part3/web/ai_display.js']
    prepare_data(ROOT, tmp_path, [(Path(name), selected[name]) for name in data])
    for name in data:
        assert (tmp_path / name).read_bytes() == (ROOT / name).read_bytes()
    dictionary = json.loads((tmp_path / 'moses_language/language.json').read_text(encoding='utf-8'))
    assert 'CANDLE_STATE' in dictionary['condition_terms']


_DISPLAY_CHECK = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const path=require('node:path'),root=process.argv[1],fixture=JSON.parse(fs.readFileSync(0,'utf8'));
class Element {
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.attrs={};this.listeners={};this.dataset={};this.className='';this._text='';this.value='';
    this.classList={contains:x=>this.className.split(' ').includes(x),add:x=>{if(!this.classList.contains(x))this.className+=' '+x;},remove:x=>{this.className=this.className.split(' ').filter(v=>v!==x).join(' ');},toggle:(x,s)=>{if(s)this.classList.add(x);else this.classList.remove(x);}};}
  set textContent(x){this._text=String(x);this.children=[];}get textContent(){return this._text+this.children.map(x=>x.textContent).join('');}
  append(...xs){for(const x of xs){x.parentNode=this;this.children.push(x);}}replaceChildren(...xs){this.children=[];this._text='';this.append(...xs);}
  setAttribute(k,v){this.attrs[k]=String(v);}getAttribute(k){return this.attrs[k];}addEventListener(k,f){(this.listeners[k]||=[]).push(f);}fire(k){for(const f of this.listeners[k]||[])f({target:this});}
  remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(x=>x!==this);}
  querySelectorAll(selector){return walk(this).filter(x=>selector==='[data-condition-path]'&&x.dataset.conditionPath);}
}
const walk=e=>[e,...e.children.flatMap(walk)],window={},document={createElement:tag=>new Element(tag)};
for(const name of ['ai_display.js','ai_editor.js'])vm.runInNewContext(fs.readFileSync(path.join(root,'Part3/web',name),'utf8'),{window,document});
const before=JSON.stringify(fixture.strategy),editor=window.part3IntentEditor.create({contract:fixture.contract,response:{result:fixture.strategy}});
assert.equal(JSON.stringify(editor.getDraft().strategy),before);
let nodes=walk(editor.element);assert.ok(editor.element.textContent.includes('1시간봉 양봉'));assert.ok(editor.element.textContent.includes('진행봉 기준'));
let fields=nodes.filter(x=>x.dataset.fieldPath==='strategy.interpretation.final_conditions.0.side');
let choice=walk(fields[0]).find(x=>x.tagName==='SELECT');assert.deepEqual(choice.children.map(x=>x.textContent),['양봉','음봉']);
choice.value='1';choice.fire('change');assert.equal(editor.getDraft().strategy.interpretation.final_conditions[0].side,'BEAR');
assert.equal(editor.getDraft().strategy.interpretation.direction,'SHORT');assert.ok(editor.element.textContent.includes('1시간봉 음봉'));
nodes=walk(editor.element);nodes.find(x=>x.tagName==='BUTTON'&&x.textContent==='상세보기').fire('click');
nodes=walk(editor.element);choice=walk(nodes.find(x=>x.dataset.fieldPath==='strategy.interpretation.final_conditions.0.bar_state')).find(x=>x.tagName==='SELECT');
assert.deepEqual(choice.children.map(x=>x.textContent),['진행봉','확정봉']);choice.value='1';choice.fire('change');
assert.equal(editor.getDraft().strategy.interpretation.final_conditions[0].bar_state,'CLOSED');
assert.ok(window.part3IntentDisplay.condition(editor.getDraft().strategy.interpretation.final_conditions[0],'SHORT').includes('확정봉 기준'));
const expected=JSON.parse(before);expected.interpretation.final_conditions[0].side='BEAR';expected.interpretation.final_conditions[0].bar_state='CLOSED';
assert.equal(JSON.stringify(editor.getDraft().strategy),JSON.stringify(expected));
assert.ok(window.part3IntentDisplay.condition({kind:'FVG_STATE',tfs:['1m'],side:'BULL'},'LONG').includes('상승 FVG'));
const existing=JSON.parse(before);existing.interpretation.final_conditions=[{kind:'FVG_STATE',tfs:['1m'],side:'BULL',state:'EXISTS',bar_state:'FORMING'}];
const fvg=window.part3IntentEditor.create({contract:fixture.contract,response:{result:existing}});
assert.equal(JSON.stringify(fvg.getDraft().strategy),JSON.stringify(existing));
choice=walk(walk(fvg.element).find(x=>x.dataset.fieldPath==='strategy.interpretation.final_conditions.0.side')).find(x=>x.tagName==='SELECT');
assert.deepEqual(choice.children.map(x=>x.textContent),['기본값 사용','상승 영역','하락 영역']);
console.log(JSON.stringify({passed:9}));
"""
