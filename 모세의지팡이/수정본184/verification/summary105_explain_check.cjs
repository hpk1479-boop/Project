/* Read-only semantic review of Part3's presentation helper. No project writes. */
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const window = {};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), {window});
const explain = window.part3IntentDisplay.explain;
assert.equal(typeof explain, 'function');
const final = {kind:'OZ',tfs:['1m'],validation_mode:'NORMAL',trigger_mode:'BREAKER',direction:'LONG'};
const base = {direction:'LONG',symbols:['XAUUSD+'],steps:[],order_mode:'SEQUENTIAL',within_sec:null,
  final_window_sec:900,final,persistent:true};
const failures = [], results = [];
function check(name, intent, expected, forbidden=[]) {
  const original = JSON.stringify(intent);
  try {
    const paragraphs = Array.from(explain(intent));
    assert.equal(JSON.stringify(intent),original,'explanation changed its source');
    const target = paragraphs.filter(value=>value.startsWith('독립 분기')).join('\n') || paragraphs.join('\n');
    for (const phrase of expected) assert.ok(target.includes(phrase),`missing ${phrase}: ${target}`);
    for (const phrase of forbidden) assert.ok(!target.includes(phrase),`unexpected ${phrase}: ${target}`);
    results.push({name,passed:true});
  } catch (error) {failures.push({name,message:error.message});}
}
check('partial branch final inherits OZ parameters',
  {...base,branches:[{steps:[{kind:'TREND',tfs:['15m']}],final:{direction:'SHORT'}}]},
  ['1분봉 일반 브레이커','매도']);
check('partial branch lifecycle preserves parent expiry and invalidation',
  {...base,lifecycle:{expires:{seconds:60},invalidate_refs:true},
    branches:[{steps:[{kind:'TREND',tfs:['15m']}],lifecycle:{first_success:true}}]},
  ['감시 수명은 1분','선행 영역이 무효화','첫 알림']);
check('same-kind branch final inherits untouched fields',
  {...base,branches:[{steps:[{kind:'TREND',tfs:['15m']}],final:{kind:'OZ',tfs:['3m']}}]},
  ['3분봉 일반 브레이커']);
check('branch kind change replaces final object',
  {...base,branches:[{direction:'SHORT',steps:[{kind:'TREND',tfs:['15m']}],final:{kind:'NOTIFY'}}]},
  ['매도 알림'],['일반 브레이커','최종 감시 유효시간']);
check('branch explicit null removes inherited interval',
  {...base,within_sec:600,branches:[{steps:[{kind:'TREND',tfs:['15m']}],within_sec:null}]},
  [],['조건 사건 사이의 시간 제한은 10분']);
check('branch time-filter replacement preserves each group',
  {...base,time_filters:['MAIN_ASIA'],final_time_filters:{MAIN_LONDON:{start:'10:00',end:'12:00'}},
    branches:[{steps:[{kind:'TREND',tfs:['15m']}],time_filters:['MAIN_NEWYORK']}]},
  ['뉴욕장','런던장','10:00','12:00'],['조건 거래시간: 아시아장']);
check('nested branch time-filter patch preserves the parent start',
  {...base,time_filters:{MAIN_LONDON:{enabled:true,start:'09:00',end:'12:00'}},
    branches:[{steps:[{kind:'TREND',tfs:['15m']}],time_filters:{MAIN_LONDON:{end:'15:00'}}}]},
  ['런던장','09:00','15:00'],['12:00']);
check('nested snapshot patch preserves fields and other snapshot objects',
  {...base,lifecycle:{snapshots:{entry:{tf:'15m',field:'close'},atr:{tf:'15m',field:'ATR14'}}},
    branches:[{steps:[{kind:'TREND',tfs:['15m']}],lifecycle:{snapshots:{atr:{tf:'5m'}}}}]},
  ['entry (15분봉 확정봉 종가)','atr (5분봉 확정봉 ATR14)']);
check('nested excursion patch preserves its multiplier and references',
  {...base,lifecycle:{snapshots:{entry:{tf:'15m',field:'close'},atr:{tf:'15m',field:'ATR14'}},
      excursion:{anchor:'entry',snapshot:'atr',multiplier:2,direction:'FAVORABLE'}},
    branches:[{steps:[{kind:'TREND',tfs:['15m']}],lifecycle:{excursion:{direction:'ADVERSE'}}}]},
  ['시작점 entry','반대 방향','atr의 2배']);
check('multiple objects and explicit candle state remain visible',
  {...base,steps:[{kind:'FVG_NEW',tfs:['15m'],capture:'higher_gap',side:'BULL',bar_state:'CLOSED'},
    {kind:'FVG_TOUCH',tfs:['1m','3m'],ref:'higher_gap',negated:true,tf_combine:'ALL',bar_state:'FORMING'}]},
  ['확정봉 기준','진행봉 기준','모든 시간봉','만족하지 않음','동일한 영역']);
{
  const intent={...base,branches:[{direction:'SHORT',steps:[{kind:'TREND',tfs:['15m']}],final:{kind:'NOTIFY'}}]};
  const original=JSON.stringify(intent);
  try {
    const paragraphs=Array.from(explain(intent));
    assert.equal(JSON.stringify(intent),original);
    assert.ok(!paragraphs[0].includes('알림을 보냅니다.'),'parent default final was described as a separately running watch');
    results.push({name:'branch template is not described as an additional active strategy',passed:true});
  }catch(error){failures.push({name:'branch template is not described as an additional active strategy',message:error.message});}
}
process.stdout.write(JSON.stringify({checks:results.length+failures.length,passed:results.length,results,failures},null,2)+'\n');
if (failures.length) process.exitCode = 1;
