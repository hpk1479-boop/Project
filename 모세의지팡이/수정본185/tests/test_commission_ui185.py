"""수정본185 screens, in the real unified.js: the commission field next to the spread and the 1-lot size field.

The backtest screen shows "수수료 (1랏 왕복 달러)" beside the spread only for a virtual entry, starts from the
saved value of the symbol (0 when none) and sends it with the request. The settings screen has a
"<symbol> 1랏 크기" number field per symbol in the indicator group, beside the 1-point price.
"""
import ast
from pathlib import Path
import importlib.util
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def backtest_dom():
    """The shipped index.html DOM model the official backtest-screen checks use."""
    source = ROOT / 'verification/test_backtest_recovery70.py'
    spec = importlib.util.spec_from_file_location('commission_dom185', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._existing_dom_fixture()


def settings_dom():
    tree = ast.parse((ROOT / 'verification/test_settings_layout68.py').read_text('utf-8'))
    script = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == 'SCRIPT' for target in node.targets))
    return script.split('const liveKeys=')[0]


BACKTEST = r'''
const options={symbol:'XAUUSD+',symbols:['XAUUSD+'],start:'2026-09-01',end:'2026-10-01',mode:'BAR',
 specials:[],special_settings:{},watch_text:'',watch_chat_id:'BACKTEST',spread_points:{'XAUUSD+':20},
 ...(scenario==='saved'?{commission:{'XAUUSD+':7}}:{}),warehouse_set:true};
const context=vm.createContext({console,document,Option,window:{dispatchEvent(){},addEventListener(){}},
 setInterval:()=>1,setTimeout:()=>1,clearTimeout(){},api:async name=>{
  if(name==='mo/backtest/options')return JSON.parse(JSON.stringify(options));
  if(name.startsWith('mo/live/status'))return {modules:{},lines:[]};
  throw Error('Unexpected API '+name);
 }});
const run=value=>vm.runInContext(value,context),get=id=>document.querySelector('#'+id);
(async()=>{
 run(fs.readFileSync(root+'/unified.js','utf8'));
 await run('moView("backtest")');
 const box=get('mo-bt-commission-box'),input=get('mo-bt-commission');
 assert.equal(box.childNodes[0].textContent,'수수료 (1랏 왕복 달러)');
 assert.equal(box.parentElement,get('mo-bt-spread-box').parentElement);      // beside the spread
 assert.equal(input.type,'number');
 assert.equal(String(input.value),scenario==='saved'?'7':'0');                // the model keeps the number as set
 get('mo-bt-result-mode').value='ALERT_ONLY';run('moBacktestMode()');
 assert(box.classList.contains('hidden'));
 get('mo-bt-result-mode').value='VIRTUAL_ENTRY';run('moBacktestMode()');
 assert(!box.classList.contains('hidden') && !get('mo-bt-spread-box').classList.contains('hidden'));
 input.value='3.5';
 assert.equal(run('moBacktestRequest().commission'),3.5);
 assert.equal(run('moBacktestRequest().spread_points'),20);
 get('mo-bt-build-only').checked=true;run('moBacktestMode()');
 assert(box.classList.contains('hidden'));                                   // a data build has no costs
 console.log('PASS '+scenario);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''

SETTINGS = r'''
const config={live:[{key:'SYMBOLS',value:'XAUUSD+,NAS100',secret:false,configured:null},
  {key:'POINT_XAUUSD+',value:'0.01',secret:false,configured:null},
  {key:'LOT_XAUUSD+',value:'100',secret:false,configured:null},
  {key:'LOT_NAS100',value:'',secret:false,configured:null}],
 part2:{cores:null,overlap_trading_days:3},part2_cores:4,part2_tuned:null,
 connections:{warehouse:'synthetic warehouse',python_executable:''},ai:{provider:'disabled',timeout:90}};
const writes=[];
const context=vm.createContext({console,document,
 Option:class {constructor(text,value){const item=new Element('option');item.textContent=text;item.value=value;return item;}},
 window:{dispatchEvent(){},addEventListener(){},confirm:()=>true},Event:class{},
 setInterval:()=>1,setTimeout:fn=>{fn();return 1;},clearTimeout(){},
 api:async(route,body)=>{
  if(route==='mo/settings'&&body){writes.push(JSON.parse(JSON.stringify(body)));
   for(const [key,value] of Object.entries(body.changes))config.live.find(row=>row.key===key).value=value;
   return {ok:true,message:'설정이 저장되었습니다'};}
  if(route==='mo/settings')return JSON.parse(JSON.stringify(config));
  if(route.startsWith('mo/live/status'))return {modules:{},lines:[]};
  if(route.startsWith('ai/'))return {available:false,models:[]};
  throw Error('Unexpected API '+route);
 }});
const run=code=>vm.runInContext(code,context),get=id=>document.getElementById(id);
const field=key=>get('settings-live-fields').querySelector('[data-setting="'+key+'"]');
(async()=>{
 run(fs.readFileSync(root+'/unified.js','utf8'));
 await run('moView("settings")');
 const nasdaq=field('LOT_NAS100'),gold=field('LOT_XAUUSD+');
 assert.equal(nasdaq.closest('fieldset').querySelector('legend').textContent,'지표');
 assert.equal(nasdaq.closest('fieldset'),field('POINT_XAUUSD+').closest('fieldset'));   // with the 1-point prices
 assert.equal(nasdaq.closest('label').childNodes[0].textContent,'NAS100 1랏 크기');
 assert.equal(nasdaq.type,'number');assert.equal(nasdaq.value,'');assert.equal(nasdaq.placeholder,'예: 100');
 assert.equal(gold.value,'100');
 await run('moSettingsSave("live")');
 assert.equal(writes.length,0);                                   // an untouched empty field is not saved
 nasdaq.value='1';await run('moSettingsSave("live")');
 assert.deepEqual(writes,[{group:'live',changes:{'LOT_NAS100':'1'}}]);
 console.log('PASS');
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''


def run(script, scenario, cwd):
    result = subprocess.run([shutil.which('node'), '-e', script, str(ROOT / 'Part3/web'), scenario],
                            cwd=cwd, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_the_commission_field_sits_beside_the_spread_for_a_virtual_entry(tmp_path):
    for scenario in ('saved', 'none'):
        assert 'PASS ' + scenario in run(backtest_dom() + BACKTEST, scenario, tmp_path)


def test_each_symbol_has_a_lot_size_field_with_the_point_prices(tmp_path):
    assert 'PASS' in run(settings_dom() + SETTINGS, 'lots', tmp_path)
