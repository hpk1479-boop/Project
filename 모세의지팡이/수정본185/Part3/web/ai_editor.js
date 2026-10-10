/* Editable presentation of the server's canonical contract. No strategy execution. */
(() => {
  'use strict';
  const copy = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
  const labels = {
    LONG:'매수', SHORT:'매도', BOTH:'양방향', SAME_AS_PREVIOUS_DIRECTION:'앞 조건의 방향 계승', OPPOSITE:'반대 방향',
    NORMAL:'일반', BLIND:'무지성', BREAKER:'브레이커', OZ:'올존 감시', NOTIFY:'조건 충족 알림', DEFINE:'조건 체인 정의',
    ALL:'모두 만족', ANY:'하나 이상 만족', INDEPENDENT:'각각 독립', MATCHING_FAMILY:'앞 조건과 같은 지표 계열',
    SIMULTANEOUS:'동시 조건', SEQUENTIAL:'순서대로 발생', UNORDERED:'순서 무관',
    CLOSED:'확정봉', FORMING:'진행봉', UNSPECIFIED:'별도 지정 없음', SOURCE:'선행 조건 시간봉', FINAL:'최종 시간봉',
    LOWER:'하단', UPPER:'상단', BELOW:'아래', ABOVE:'위', LOW:'저점', HIGH:'고점',
    UP:'상승', DOWN:'하락', BULL:'상승 영역', BEAR:'하락 영역', EXISTS:'존재', AREA:'영역 접촉',
    BREAK_UP:'상향 교차', BREAK_DOWN:'하향 교차', TOUCH:'터치', SLOPE_UP:'기울기 상승', SLOPE_DOWN:'기울기 하락',
    IN:'밴드 내부', OUT_LOWER:'하단 이탈', OUT_UPPER:'상단 이탈',
    TREND:'추세', TREND_METRIC:'지표 수치 비교', CANDLE_STATE:'캔들 양봉·음봉', CANDLE_SHAPE:'캔들 모양',
    HAMMER:'망치형', INVERTED_HAMMER:'역망치형', WONBI_TOUCH:'원비 터치', FVG_NEW:'새 FVG 발생',
    FVG_TOUCH:'FVG 터치', FVG_STATE:'FVG 상태', MA_STATE:'이동평균 위치', MA_PRICE_STATE:'가격·이동평균 위치',
    MA_PRICE_CROSS:'가격·이동평균 교차', MA_PRICE_TOUCH:'가격·이동평균 터치', MA_SLOPE_STATE:'이동평균 기울기',
    MA_CROSS:'이동평균 교차', PERCENTILE_OUT:'퍼센타일 OUT', PERCENTILE_OUT_IN:'퍼센타일 OUT → IN',
    REGIME_BAND:'레짐 밴드', OZ_ALERT:'올존 알림 발생', BAR_CLOSE:'봉 마감', SESSION_START:'세션 시작',
    PRICE_LEVEL:'가격 기준', LIQUIDITY_LEVEL:'유동성 기준', EXTERNAL_LIQUIDITY_TOUCH:'외부 유동성 터치',
    GT:'초과', GTE:'이상', LT:'미만', LTE:'이하', EQ:'같음', NE:'다름',
    PRICE:'가격', RSI:'RSI', STO:'스토캐스틱', DI:'이격도',
    BAR:'봉', EVENT:'이벤트', TICK:'틱', ALERT_ONLY:'알림만', VIRTUAL_ENTRY:'가상 진입',
    MAIN_ASIA:'아시아장', MAIN_LONDON:'런던장', MAIN_NEWYORK:'뉴욕장',
    FAVORABLE:'진행 방향', ADVERSE:'반대 방향', SYMBOL:'같은 종목', SYMBOL_DIRECTION:'같은 종목·방향',
    open:'시가', high:'고가', low:'저가', close:'종가', STRATEGY:'전략 생성', BACKTEST:'백테스트'
  };
  const names = {
    direction:'방향', symbols:'종목', kind:'조건 종류', tfs:'시간봉', bar_state:'봉 기준',
    tf_combine:'시간봉 관계', side:'위치', state:'상태', relation:'변화·관계',
    families:'지표 계열', regime_families:'레짐 지표 계열', family_combine:'지표 관계',
    ma_family:'이동평균 종류', fast_period:'단기 기간', slow_period:'기간', price_tf:'가격 시간봉',
    ma_left:'첫 이동평균', ma_right:'둘째 이동평균', lookback:'비교할 이전 봉 수',
    metric:'지표', metric_operator:'비교', metric_value:'비교 수치', level:'기준값', session:'세션',
    validation_mode:'감시 모드', trigger_mode:'최종 트리거', scope_ref:'발생 범위 참조', ref:'동일 객체 참조',
    capture:'후속 조건에 기록', negated:'조건 부정', order_mode:'조건 순서', global_combine:'조건 관계',
    within_sec:'조건 간 제한시간 (초)', within:'조건 간 기간', recent:'최근 기간', final_window_sec:'최종 감시 제한시간 (초)',
    final_after:'최종 감시 시작 사건', persistent:'반복 감시', preset:'기반 프리셋', regime_family:'지표 계열',
    expires:'감시 수명', seconds:'수명 (초)', bars:'수명 (봉 수)', tf:'시간봉',
    first_success:'첫 알림 이후 종료', invalidate_refs:'참조 무효화 시 종료',
    anchor:'시작점 참조', snapshot:'고정값 참조', multiplier:'고정 변동폭 배수', scope:'교체 범위',
    field:'고정 지표', symbol:'백테스트 종목', start:'시작일', end:'종료일 (미포함)',
    mode:'재생 모드', result_mode:'결과 방식', spread_points:'스프레드', commission:'수수료 (1랏 왕복 달러)',
      build_only:'데이터 구축만', rebuild:'기존 데이터 재구축', available_only:'보유 데이터만 사용',
    virtual_entry:'가상진입 정책'
  };
  const frame = value => {
    const found = /^(\d+)(m|h|d|w)$/.exec(String(value));
    return found ? found[1] + {m:'분봉', h:'시간봉', d:'일봉', w:'주봉'}[found[2]] : labels[value] || String(value);
  };
  const label = (value, key) => key === 'tf' || key === 'tfs' || key === 'price_tf' ? frame(value) :
    key==='needs_clarification' ? (value ? '추가 확인 필요' : '이 표의 조건으로 확정') :
    key==='level' && ({ALL:'전체 유동성',DAY_OPEN:'당일 시가',PDH:'전일 고가',PDL:'전일 저가',
      PWH:'전주 고가',PWL:'전주 저가',DAILY:'일간 유동성',WEEKLY:'주간 유동성'})[value] ||
    (typeof value === 'boolean' ? (value ? '사용' : '사용 안 함') : labels[value] || String(value));
  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function values(schema) {
    if (!schema) return [];
    if (Array.isArray(schema.enum)) return schema.enum.filter(value => value !== null);
    if (own(schema, 'const')) return schema.const === null ? [] : [schema.const];
    return (schema.anyOf || schema.oneOf || []).flatMap(values);
  }
  function concrete(schema, type) {
    if (!schema) return {};
    return (schema.anyOf || schema.oneOf || []).find(item => item.type === type ||
      Array.isArray(item.type) && item.type.includes(type)) || schema;
  }
  // A window after an event (수정본173): '30분', '6시간' or '1시간봉 6개'.
  const windowLabel = value => !value || typeof value !== 'object' ? '' :
    own(value, 'bars') ? `${frame(value.tf)} ${value.bars}개` : !Number.isInteger(value.seconds) ? '' :
    value.seconds % 3600 === 0 ? value.seconds / 3600 + '시간' : value.seconds % 60 === 0 ? value.seconds / 60 + '분' : value.seconds + '초';
  // The chain wait already written in seconds keeps its own field; every other chain shows the window field.
  const waitKey = value => own(value, 'within_sec') && !own(value, 'within') ? 'within_sec' : 'within';
  const pathKey = path => path.map(String).join('.');
  const pathOf = value => Array.isArray(value) ? value.slice() : String(value || '').replace(/^\//, '').split(/[./]/).filter(Boolean);

  function create({response = {}, contract = {}, onChange = () => {}}) {
    const output = contract.schema || contract.contract || {};
    const meaningSchema = concrete(output.properties?.interpretation, 'object');
    const variants = output.$defs?.step?.anyOf || output.$defs?.step?.oneOf || [];
    const stepSchema = kind => variants.find(item => item.properties?.kind?.const === kind ||
      values(item.properties?.kind).includes(kind));
    const kinds = variants.flatMap(item => values(item.properties?.kind));
    const families = [...new Set(variants.flatMap(item => values(item.properties?.ma_family)))];
    const options = contract.options || {};
    let virtualDraftFrames = copy(options.virtual_draft_frames) || null, virtualDraftPending = false;
    // An explicit null is canonical; a backtest job result is never a strategy.
    const candidate = own(contract, 'strategy') ? contract.strategy :
      own(response, 'editor_strategy') ? response.editor_strategy :
      own(response, 'strategy') ? response.strategy : response.kind === 'BACKTEST' ? null : response.result;
    let strategy = copy(candidate?.result || candidate) || null;
    const hasQuestion=strategy?.needs_clarification===true;
    const originalQuestion=typeof strategy?.clarification_question==='string' ? strategy.clarification_question : '';
    let operation = contract.operation || response.operation || (response.kind === 'BACKTEST' ? 'BACKTEST' : 'STRATEGY');
    let plan = copy(contract.plan || response.plan) || null;
    let disabled = false, destroyed = false, canonicalPre = null, compactMode = true;
    let externalErrors = [], localErrors = [];
    const fields = new Map(), controls = [], fieldModes = new Map(), targetDescriptions = [];
    const previews = [], openedEditors = new Set();
    const element = node('section', undefined, 'ai-intent-editor');
    const form = node('div', undefined, 'ai-edit-form');
    const summary = node('p', undefined, 'ai-edit-errors');
    summary.setAttribute('role', 'status');
    element.append(form, summary);
    const meaning = () => strategy?.interpretation;
    const startPlan = () => operation === 'BACKTEST' && Array.isArray(plan?.steps) && plan.steps.length > 0 &&
      plan.steps.every(row => row?.command?.action === 'START' && row.command.request &&
        typeof row.command.request === 'object' && !Array.isArray(row.command.request));
    const planOnly = () => strategy === null && startPlan();
    const canonicalValue = () => operation === 'BACKTEST' ? getDraft() : strategy;
    const locate = path => {
      const root = path[0] === 'strategy' ? strategy : path[0] === 'plan' ? plan : null;
      let object = root;
      for (const key of path.slice(1, -1)) object = object?.[key];
      return {object, key:path[path.length - 1]};
    };
    function set(path, value) {
      const target = locate(path);
      if (!target.object) return;
      if (value === undefined) delete target.object[target.key];
      else target.object[target.key] = value;
    }
    const get = path => { const target = locate(path); return target.object?.[target.key]; };
    function changed(rebuild=false) {
      if (destroyed) return;
      // The server owns frame inference. Until this edit is checked, old draft labels are not facts.
      virtualDraftFrames = null; virtualDraftPending = true;
      externalErrors = [];
      if (rebuild) render(); else paintErrors();
      if (canonicalPre) canonicalPre.textContent=JSON.stringify(canonicalValue(),null,2);
      refreshDescriptions();
      onChange(getDraft());
    }
    function button(text, action, className='ai-edit-small') {
      const control = node('button', text, className); control.type = 'button';
      control.addEventListener('click', action); controls.push(control); return control;
    }
    function field(parent, path, title) {
      const cell = node('div', undefined, 'ai-edit-field');
      const name = node('label', title || names[path[path.length - 1]] || String(path[path.length - 1]));
      const error = node('span', undefined, 'ai-edit-field-error');
      const id = 'ai-edit-' + path.map(String).join('-');
      name.setAttribute('for', id); error.id = id + '-error';
      cell.dataset.fieldPath = pathKey(path);
      cell.append(name); parent.append(cell);
      fields.set(pathKey(path), {cell, error, path, id, inputs:[]});
      return {cell, error, path, id, inputs:[]};
    }
    function attach(slot, input) {
      if (!slot.inputs.length) input.id = slot.id;
      input.setAttribute('aria-describedby', slot.error.id);
      slot.inputs.push(input); fields.get(pathKey(slot.path)).inputs = slot.inputs;
      slot.cell.append(input); controls.push(input);
    }
    function finish(slot) { slot.cell.append(slot.error); }
    function select(slot, permitted, current, callback, optional=true, emptyText) {
      const control = node('select');
      if (optional || current === undefined || current === null || !permitted.includes(current)) {
        const blank = node('option', emptyText || (optional ? '기본값 사용' : '선택해 주세요')); blank.value = ''; control.append(blank);
      }
      const last=slot.path[slot.path.length - 1];
      const labelKey=typeof last==='number' ? slot.path[slot.path.length - 2] : last;
      permitted.forEach((value, index) => {
        const candleSide=labelKey==='side' && locate(slot.path).object?.kind==='CANDLE_STATE';
        const item = node('option', candleSide ? ({BULL:'양봉',BEAR:'음봉'})[value] || label(value,labelKey) : label(value, labelKey));
        item.value = String(index); item.selected = current === value; control.append(item);
      });
      const found = permitted.indexOf(current);
      control.value = found < 0 ? '' : String(found);
      control.addEventListener('change', () => callback(control.value === '' ? undefined : permitted[Number(control.value)]));
      attach(slot, control); return control;
    }
    function referenceChoices(key, current) {
      const refs = new Set();
      function walk(value) {
        if (!value || typeof value !== 'object') return;
        if (typeof value.capture === 'string') refs.add(value.capture);
        Object.entries(value.snapshots || {}).forEach(([name]) => refs.add(name));
        Object.values(value).forEach(walk);
      }
      walk(meaning());
      if (key === 'capture') {
        let index = 1;
        while (refs.has('event_' + index)) index++;
        return [...new Set([current, 'event_' + index].filter(Boolean))];
      }
      return [...refs];
    }
    function unsupported(parent, path, schema, current, title) {
      const slot = field(parent, path, title);
      slot.cell.append(node('span', '상세 보기에서 확인', 'ai-edit-readonly'));
      localErrors.push({path, message:'이 항목은 슬롯으로 수정할 수 없습니다. 자연어로 수정해 주세요.'});
      finish(slot);
    }
    function windowState(current) {
      if (current && typeof current==='object' && own(current,'bars')) return {mode:'BARS',bars:current.bars,tf:current.tf};
      if (current && typeof current==='object' && own(current,'seconds')) {
        // Minutes until a length is written; then the largest unit that divides it.
        const seconds=current.seconds, unit=!(Number.isInteger(seconds) && seconds>0) ? 60 :
          [3600,60].find(size=>seconds%size===0) || 1;
        return {mode:'TIME',amount:Number.isFinite(seconds) ? seconds/unit : undefined,unit};
      }
      return {mode:''};
    }
    // A window after an event (수정본173): none, a time in seconds·minutes·hours, or a count of one frame's bars.
    function windowControl(parent, path, schema, title) {
      const object=concrete(schema,'object'), state=windowState(get(path)), frames=values(object.properties?.tf);
      const slot=field(parent,path,title);
      const store=(rebuild=false)=>{
        set(path,state.mode==='TIME' ? {seconds:state.amount===undefined ? undefined : Math.round(state.amount*state.unit)} :
          state.mode==='BARS' ? {bars:state.bars,tf:state.tf} : undefined);
        changed(rebuild);
      };
      const choose=(entries,current,callback)=>{
        const choice=node('select');
        for (const [value,name] of entries) {
          const option=node('option',name);option.value=String(value);option.selected=String(current)===String(value);choice.append(option);
        }
        choice.addEventListener('change',()=>callback(choice.value));attach(slot,choice);
      };
      const amount=(value,callback,placeholder)=>{
        const number=node('input');number.type='number';number.min='1';number.step='1';number.placeholder=placeholder;
        number.value=value===undefined ? '' : String(value);
        number.addEventListener('input',()=>callback(number.value==='' ? undefined : Number(number.value)));attach(slot,number);
      };
      choose([['','없음'],['TIME','시간'],['BARS','봉 수']],state.mode,value=>{
        state.mode=value;
        if (value==='TIME') {state.amount=undefined;state.unit=60;}
        if (value==='BARS') {state.bars=undefined;state.tf=frames[0];}
        store(true);
      });
      if (state.mode==='TIME') {
        amount(state.amount,value=>{state.amount=value;store();},'길이');
        choose([[1,'초'],[60,'분'],[3600,'시간']],state.unit,value=>{state.unit=Number(value);store();});
      } else if (state.mode==='BARS') {
        amount(state.bars,value=>{state.bars=value;store();},'봉 수');
        choose(frames.map(value=>[value,frame(value)]),state.tf,value=>{state.tf=value;store();});
      }
      finish(slot);
    }
    function control(parent, path, schema, required=false, title) {
      const key = String(path[path.length - 1]), current = get(path);
      if (key==='side' && locate(path).object?.kind==='CANDLE_STATE') title=title || '캔들 상태';
      schema = schema || {};
      if (key==='within' || key==='recent') {windowControl(parent,path,schema,title);return;}
      if (key==='level_gate' && schema.type==='object' && current && typeof current==='object' && !Array.isArray(current)) {
        // The final OZ is tied to one captured external-liquidity touch and an optional ATR distance rule.
        const touches=[];
        (function walk(value) {
          if (!value || typeof value!=='object') return;
          if (value.kind==='EXTERNAL_LIQUIDITY_TOUCH' && typeof value.capture==='string') touches.push(value.capture);
          Object.values(value).forEach(walk);
        })(meaning());
        const refPath=[...path,'ref'], slot=field(parent,refPath,'연결할 외부유동성 터치');
        select(slot,[...new Set(touches)],current.ref,value=>{set(refPath,value); changed();},false);
        finish(slot);
        for (const sub of ['atr_period','atr_mult']) if (schema.properties?.[sub]) {
          control(parent,[...path,sub],schema.properties[sub],false,sub==='atr_period' ? 'ATR 기간' : 'ATR 배수');
          const input=fields.get(pathKey([...path,sub]))?.inputs[0];
          if (input) input.placeholder='기본값 사용';
        }
        for (const extra of Object.keys(current)) if (!own(schema.properties,extra))
          unsupported(parent,[...path,extra],{},current[extra]);
        return;
      }
      if (key==='level') {
        const owner=locate(path).object, permitted=options.level_choices?.[owner?.kind];
        if (Array.isArray(permitted)) {
          const slot=field(parent,path,title), numeric=Boolean((schema.anyOf || []).some(item=>item.type==='number'));
          const mode=typeof current==='number' || fieldModes.get(pathKey(path))==='number';
          const choices=numeric ? [...permitted,'숫자로 지정'] : permitted;
          select(slot,choices,mode ? '숫자로 지정' : current,value=>{
            if (value==='숫자로 지정') {fieldModes.set(pathKey(path),'number');set(path,undefined);}
            else {fieldModes.delete(pathKey(path));set(path,value);}
            changed(true);
          },!required);
          if (mode) {
            const number=node('input');number.type='number';number.step='any';number.placeholder='가격';
            number.value=typeof current==='number' ? String(current) : '';
            number.addEventListener('input',()=>{set(path,number.value==='' ? undefined : Number(number.value));changed();});attach(slot,number);
          }
          finish(slot);return;
        }
        if (typeof current==='string') {unsupported(parent,path,schema,current,title);return;}
      }
      if ((key === 'ma_left' || key === 'ma_right') && schema.pattern) {
        const slot = field(parent, path, title);
        const valid=typeof current==='string' && (new RegExp(schema.pattern)).test(current);
        let family = valid ? families.find(value=>current.startsWith(value) && /^[1-9][0-9]*$/.test(current.slice(value.length))) : undefined;
        let period = family ? Number(current.slice(family.length)) : undefined;
        const update = () => { set(path, family !== undefined && period !== undefined ? family + period : undefined); changed(); };
        select(slot, families, family, value => { family=value; update(); }, true, '이동평균 종류');
        const number = node('input'); number.type='number'; number.min='1'; number.step='1';
        number.placeholder='기간'; number.value=period === undefined ? '' : String(period);
        number.addEventListener('input', () => { period=number.value === '' ? undefined : Number(number.value); update(); });
        attach(slot, number); finish(slot); return;
      }
      const permitted = values(schema);
      if (permitted.length || schema.type === 'boolean' || Array.isArray(schema.type) && schema.type.includes('boolean')) {
        const slot = field(parent, path, title);
        const allowed = permitted.length ? permitted : [true,false];
        select(slot, allowed, current, value => {set(path,value); changed();}, !required);
        finish(slot); return;
      }
      if (schema.type === 'array') {
        if (values(schema.items).length) {
          const slot = field(parent,path,title), rows = Array.isArray(current) ? current : [];
          const group = node('div',undefined,'ai-edit-multi');
          const allowed = values(schema.items);
          rows.forEach((value,index) => {
            const item = node('div',undefined,'ai-edit-array-item');
            const child = {cell:item,error:slot.error,path:[...path,index],id:slot.id+'-'+index,inputs:[]};
            fields.set(pathKey(child.path),child);
            select(child,allowed,value,next => {rows[index]=next === undefined ? '' : next; set(path,rows); changed();},true);
            item.append(button('×',() => {rows.splice(index,1); set(path,rows); changed(true);},'ai-edit-remove'));
            group.append(item);
          });
          group.append(button('+ 추가',() => {set(path,[...rows,'']); changed(true);}));
          slot.cell.append(group); finish(slot); return;
        }
        if (schema.items?.$ref === '#/$defs/step') {steps(parent,path,current,title); return;}
      }
      const numeric = concrete(schema,'integer').type === 'integer' ? concrete(schema,'integer') : concrete(schema,'number');
      if (numeric.type === 'integer' || numeric.type === 'number' || Array.isArray(numeric.type) && numeric.type.includes('number')) {
        const slot = field(parent,path,title), number=node('input'); number.type='number';
        number.step=numeric.type === 'integer' ? '1' : 'any';
        if (numeric.minimum !== undefined) number.min=String(numeric.minimum);
        if (numeric.maximum !== undefined) number.max=String(numeric.maximum);
        number.placeholder=required ? '숫자를 입력하세요' : '제한 없음 / 별도 지정 없음';
        number.value=typeof current === 'number' ? String(current) : '';
        number.addEventListener('input',() => {set(path,number.value === '' ? undefined : Number(number.value)); changed();});
        attach(slot,number); finish(slot); return;
      }
      if (schema.pattern === '^[A-Za-z][A-Za-z0-9_]*$') {
        const slot=field(parent,path,title);
        select(slot,referenceChoices(key,current),current,value=>{set(path,value); changed(key === 'capture');},!required,
          key === 'capture' ? '기록하지 않음' : '참조하지 않음');
        finish(slot); return;
      }
      if (current !== undefined || required) unsupported(parent,path,schema,current,title);
    }
    function details(parent, title, value) {
      if (value === undefined) return;
      const box=node('details',undefined,'ai-edit-details');
      const pre=node('pre',JSON.stringify(value,null,2));
      box.append(node('summary',title),pre); parent.append(box);return pre;
    }
    function editFold(parent, key, title='수정') {
      const box=node('details',undefined,'ai-core-edit');box.open=openedEditors.has(key);
      box.append(node('summary',title));
      box.addEventListener('toggle',()=>{if(box.open)openedEditors.add(key);else openedEditors.delete(key);});
      parent.append(box);return box;
    }
    function preview(parent, describe, className='ai-core-preview') {
      const text=node('p',undefined,className);previews.push({element:text,describe});parent.append(text);return text;
    }
    function steps(parent,path,current,title='조건',compact=false,emptyText) {
      const section=node('section',undefined,'ai-edit-conditions');
      section.append(node('h4',title)); parent.append(section);
      const rows=Array.isArray(current) ? current : [];
      const errorsField=field(section,path,title+' 검사');
      errorsField.cell.classList.add('ai-edit-group-error'); finish(errorsField);
      rows.forEach((row,index) => {
        const card=node('div',undefined,'ai-edit-condition');
        const heading=node('div',undefined,'ai-edit-condition-heading');
        heading.append(node('strong',`${title} ${index+1}`));
        const remove=button('조건 삭제',()=>{rows.splice(index,1);set(path,rows);changed(true);});
        if (!compact) heading.append(remove);
        card.append(heading); section.append(card);
        const rowPath=[...path,index], variant=stepSchema(row.kind);
        let editor=card;
        if (compact) {
          const description=node('p',undefined,'ai-core-condition-text');
          description.dataset.conditionPath=pathKey(rowPath);heading.append(description);
          editor=editFold(card,pathKey(rowPath));editor.append(remove);
        }
        const grid=node('div',undefined,'ai-edit-grid');editor.append(grid);
        const kindSlot=field(grid,[...rowPath,'kind'],'조건 종류');
        select(kindSlot,kinds,row.kind,next => {
          const selected=stepSchema(next);
          if (!selected) return;
          const replacement={kind:next};
          // Changing kind is an explicit replacement; shared properties keep their values.
          for (const key of ['tfs','direction','bar_state','tf_combine','negated','recent','capture','ref','scope_ref'])
            if (own(row,key) && own(selected.properties,key)) replacement[key]=copy(row[key]);
          rows[index]=replacement;set(path,rows);changed(true);
        },false); finish(kindSlot);
        if (!compact) card.append(node('p','조건 종류를 바꾸면 이전 종류의 세부 항목이 초기화됩니다.','muted'));
        if (!variant) {localErrors.push({path:[...rowPath,'kind'],message:'허용된 조건 종류를 선택해 주세요.'}); details(editor,'현재 조건 상세',row);return;}
        const visible=new Set(['tfs','direction','side','bar_state','relation','families','regime_families',...variant.required.filter(key=>key!=='kind')]);
        for (const key of visible) if (variant.properties[key])
          control(grid,[...rowPath,key],variant.properties[key],variant.required.includes(key));
        const advanced=compact ? editor : node('details',undefined,'ai-edit-details');
        if (!compact) advanced.append(node('summary','참조·조건 세부 설정'));
        const extra=node('div',undefined,'ai-edit-grid'); advanced.append(extra);
        for (const [key,schema] of Object.entries(variant.properties))
          if (key!=='kind' && !visible.has(key)) control(extra,[...rowPath,key],schema,variant.required.includes(key));
        for (const key of Object.keys(row)) if (!own(variant.properties,key))
          unsupported(extra,[...rowPath,key],{},row[key],`지원되지 않는 항목 · ${key}`);
        if (!compact) card.append(advanced);
      });
      section.append(button('+ 조건 추가',()=>{set(path,[...rows,{kind:kinds[0],tfs:[]}]);changed(true);}));
      if (!rows.length) section.append(node('p',compact ? emptyText || title+' 없음' :
        '선행 조건이 없습니다. 필요한 조건을 추가해 주세요.','muted'));
    }
    function ownerDirection(path) {
      for (let length=path.length-1;length>=2;length--) {
        const owner=get(path.slice(0,length));
        if (owner?.direction) return owner.direction;
      }
      return meaning()?.direction;
    }
    function effectiveFinal(path) {
      let value;
      for (let length=2;length<path.length;length++) {
        const owner=get(path.slice(0,length));
        if (!owner || !own(owner,'final')) continue;
        const patch=owner.final;
        if (!patch || typeof patch!=='object') {value=patch;continue;}
        // Final fields are flat; a different kind replaces the inherited action contract.
        value=!value || own(patch,'kind') && value.kind!==patch.kind ? copy(patch) : {...copy(value),...copy(patch)};
      }
      return value;
    }
    function finalText(value,inheritedDirection) {
      if (!value) return '최종 행동을 확인해 주세요.';
      if (value.kind==='DEFINE') return '조건 체인 정의만 저장 · 알림 및 감시 실행 없음';
      const direction=label(value.direction || inheritedDirection || 'BOTH','direction');
      if (value.kind==='NOTIFY') return direction+' 조건 충족 알림';
      if (value.kind!=='OZ') return '최종 행동은 상세보기에서 확인해 주세요.';
      return [(value.tfs || []).map(frame).join(' · '),
        label(value.validation_mode || 'UNSPECIFIED','validation_mode'),
        value.trigger_mode && value.trigger_mode!=='OZ' ? label(value.trigger_mode,'trigger_mode') : '',
        '올존 감시',direction,
        (value.tfs || []).length>1 ? '시간봉 관계: '+label(value.tf_combine || 'ANY','tf_combine') : '',
        value.regime_family ? label(value.regime_family,'regime_family')+' 지표 계열' : '',
        value.scope_ref ? '선행 사건의 영역 안에서만 허용' : ''].filter(Boolean).join(' · ');
    }
    function final(parent,path,value,schema,compact=false,title='최종 행동') {
      const section=node('section',undefined,'ai-edit-final'); section.append(node('h4',title));parent.append(section);
      if (!value || typeof value !== 'object') {
        localErrors.push({path,message:'최종 행동을 자연어로 다시 해석해 주세요.'});
        details(section,'현재 최종 행동',value);return;
      }
      let editor=section;
      if (compact) {preview(section,()=>finalText(effectiveFinal(path),ownerDirection(path)),'ai-core-final-text');editor=editFold(section,pathKey(path));}
      const grid=node('div',undefined,'ai-edit-grid');editor.append(grid);
      const kindSlot=field(grid,[...path,'kind'],'최종 행동');
      select(kindSlot,values(schema.properties.kind),value.kind,next=>{
        set([...path,'kind'],next);
        // These fields are OZ-only under the common validator. Remove them only on an explicit action change.
        if (next!=='OZ') for (const key of ['tfs','validation_mode','trigger_mode','tf_combine','regime_family']) delete value[key];
        changed(true);
      },false);finish(kindSlot);
      const ozOnly=new Set(['tfs','validation_mode','trigger_mode','tf_combine','regime_family']);
      const extra=node('details',undefined,'ai-edit-details');extra.append(node('summary','최종 행동 세부 설정'));
      const extraGrid=node('div',undefined,'ai-edit-grid');extra.append(extraGrid);
      for (const [key,property] of Object.entries(schema.properties || {}))
        if (key!=='kind' && (value.kind==='OZ' || !ozOnly.has(key) || own(value,key)))
          control(['scope_ref','tf_combine','regime_family'].includes(key) ? extraGrid : grid,[...path,key],property,(schema.required || []).includes(key));
      if (!compact) section.append(extra);
      else grid.append(...extraGrid.children);
      for (const key of Object.keys(value)) if (!own(schema.properties,key)) unsupported(grid,[...path,key],{},value[key]);
    }
    function retainedRules(parent,path,value,schema,compact=false) {
      if (value === undefined) return;
      const branchCount=Array.isArray(value.branches) ? value.branches.length : 0;
      const wrap=node('details',undefined,compact ? 'ai-edit-details ai-core-extra' : 'ai-edit-details');
      wrap.open=!compact && !!response.preset && branchCount>0;
      const additions=[branchCount ? `독립 분기 ${branchCount}개` : '',
        own(value,'after_conditions') ? '유지 조건'+(value.after_conditions?.length ? '' : ' 없음') : '',
        own(value,'final_conditions') ? '최종 확인 조건'+(value.final_conditions?.length ? '' : ' 없음') : '',
        own(value,'cancel_conditions') ? '취소 조건'+(value.cancel_conditions?.length ? '' : ' 없음') : '',
        own(value,'persistent') ? (value.persistent===false ? '1회 감시' : '반복 감시') : '',value.lifecycle ? '감시 수명' : '',
        own(value,'time_filters') || own(value,'final_time_filters') ? '거래시간' : ''].filter(Boolean);
      wrap.append(node('summary',compact ? '추가 조건 있음'+(additions.length ? ' · '+additions.join(' · ') : '') :
        '분기·수명·추가 조건'+(branchCount ? ` · 분기 ${branchCount}개` : '')));parent.append(wrap);
      if (compact && branchCount) {
        wrap.append(node('p','각 분기는 아래 공통 설정을 바탕으로 분기별 조건과 최종 행동을 적용합니다.','muted'));
        steps(wrap,[...path,'steps'],value.steps,'공통 조건',true);
        if (value.final) final(wrap,[...path,'final'],value.final,schema.properties.final,true,'공통 최종 행동');
      }
      const grid=node('div',undefined,'ai-edit-grid');wrap.append(grid);
      for (const key of ['final_after','persistent','preset'])
        if (schema.properties?.[key]) control(grid,[...path,key],schema.properties[key]);
      for (const key of ['after_conditions','final_conditions','cancel_conditions'])
        if (own(value,key)) steps(wrap,[...path,key],value[key],{after_conditions:'선행 사건 이후 유지 조건',final_conditions:'최종 확인 조건',cancel_conditions:'취소 조건'}[key],compact);
      if (Array.isArray(value.branches)) value.branches.forEach((branch,index) => {
        const box=node('section',undefined,'ai-edit-branch');box.append(node('h4',`독립 분기 ${index+1}`));wrap.append(box);
        const branchPath=[...path,'branches',index], branchSchema=schema.properties.branches.items;
        const branchGrid=node('div',undefined,'ai-edit-grid');box.append(branchGrid);
        for (const key of ['direction','order_mode','global_combine',waitKey(branch),'final_window_sec'])
          if (branchSchema.properties[key]) control(branchGrid,[...branchPath,key],branchSchema.properties[key]);
        steps(box,[...branchPath,'steps'],branch.steps,'분기 조건',compact,
          !own(branch,'steps') ? '분기 조건은 공통 조건을 적용합니다.' : undefined);
        if (branch.final) final(box,[...branchPath,'final'],branch.final,branchSchema.properties.final,compact);
        else if (compact) box.append(node('p','최종 행동: 공통 설정 적용','ai-core-preview'));
        if (!compact || hasRetainedRules(branch,branchSchema,true)) retainedRules(box,branchPath,branch,branchSchema,compact);
      });
      const lifecycle=value.lifecycle;
      if (lifecycle) {
        const lifeSchema=schema.properties.lifecycle;
        if (!lifeSchema) {unsupported(grid,[...path,'lifecycle'],{},lifecycle);return;}
        const lifePath=[...path,'lifecycle'], lifeGrid=node('div',undefined,'ai-edit-grid');wrap.append(node('h4','감시 수명'),lifeGrid);
        for (const key of ['first_success','invalidate_refs']) if (own(lifecycle,key)) control(lifeGrid,[...lifePath,key],lifeSchema.properties[key]);
        for (const key of ['expires','replace','excursion']) if (lifecycle[key])
          for (const [sub,property] of Object.entries(lifeSchema.properties[key].properties))
            if (own(lifecycle[key],sub)) control(lifeGrid,[...lifePath,key,sub],property,true);
        if (lifecycle.snapshots) for (const [id,snapshot] of Object.entries(lifecycle.snapshots)) {
          wrap.append(node('h5',`고정값 · ${id}`));const snap=node('div',undefined,'ai-edit-grid');wrap.append(snap);
          const snapshotSchema=lifeSchema.properties.snapshots.additionalProperties;
          for (const [key,property] of Object.entries(snapshotSchema.properties)) if (own(snapshot,key))
            control(snap,[...lifePath,'snapshots',id,key],property,(snapshotSchema.required||[]).includes(key));
        }
        if (lifecycle.restart_on) steps(wrap,[...lifePath,'restart_on'],lifecycle.restart_on,'새 감시 주기');
      }
      // Arbitrary time-filter strings remain verbatim and visible; they are never reinterpreted here.
      for (const key of ['time_filters','final_time_filters']) if (own(value,key))
        details(wrap,key==='time_filters' ? '기존 거래시간 설정 (자연어로 수정)' : '최종 거래시간 설정 (자연어로 수정)',value[key]);
      const known=new Set(Object.keys(schema.properties || {}));
      for (const key of Object.keys(value)) if (!known.has(key)) {
        const candidate=pathKey([...path,key].slice(1));
        if ((contract.readonly_metadata || []).some(item=>pathKey(pathOf(item))===candidate))
          details(wrap,'해석 부가 정보 · '+key,value[key]);
        else unsupported(grid,[...path,key],{},value[key]);
      }
    }
    function hasRetainedRules(value,schema,branch=false) {
      return ['after_conditions','final_conditions','cancel_conditions','branches'].some(key=>value[key]?.length) ||
        branch && ['after_conditions','final_conditions','cancel_conditions','persistent','final_after','lifecycle'].some(key=>own(value,key)) ||
        Boolean(value.lifecycle || value.final_after || value.preset || value.persistent===false) ||
        ['time_filters','final_time_filters'].some(key=>own(value,key)) ||
        Object.keys(value).some(key=>!own(schema.properties,key));
    }
    function makePlan() {
      if (plan || !meaning()) return;
      const requestSchema=contract.plan_schema?.properties?.steps?.items?.properties?.command?.properties?.request;
      const request={};
      for (const key of Object.keys(requestSchema?.properties || {})) request[key]=null;
      Object.assign(request,{target_mode:'GENERATED',symbol:meaning()?.symbols?.[0] || null});
      plan={strategy_text:null,steps:[{draft:true,command:{supported:true,action:'START',request,job_id:null,
        needs_clarification:false,clarification_question:null,message_ko:''}}],needs_clarification:false,clarification_question:null};
    }
    function executionTarget(row) {
      const request=row.command?.request || {};
      if (request.build_only === true) return '데이터 구축만 수행하며 전략은 실행하지 않습니다.';
      if (row.draft) return meaning() ? '현재 연구 전략을 확인 후 생성하여 실행합니다.'
        : '현재 연구 전략을 참조하지만 편집할 전략 해석이 없습니다.';
      if (request.target_mode === 'SPECIAL') return Array.isArray(request.specials) && request.specials.length
        ? '기존 SPECIAL 실행 대상: '+request.specials.join(', ')
        : '기존 SPECIAL 실행 대상을 지정하지 않았습니다.';
      // filenames: several generated files replayed together (수정본184).
      if (request.target_mode === 'GENERATED') return request.filename || request.filenames?.length
        ? '기존 생성 전략 실행 대상: '+(request.filename || request.filenames.join(', '))
        : '기존 생성 전략 파일을 지정하지 않았습니다.';
      if (request.target_mode === 'WATCH') return request.watch_text
        ? 'WATCH 실행 대상: '+request.watch_text
        : 'WATCH 명령을 지정하지 않았습니다.';
      return '실행 대상을 지정하지 않았습니다.';
    }
    // The virtual-entry target's frames on its base frame: a recipe's from the server, or the draft being written.
    function virtualFrames(request) {
      const tf=request.virtual_entry?.tf || 'SIGNAL', specials=request.specials || [];
      const name=request.target_mode === 'SPECIAL' && specials.length === 1 ? specials[0]
        : request.target_mode === 'GENERATED' && request.filename ? String(request.filename).replace(/\.py$/, '') : null;
      if (name) return options.virtual_frames?.[name]?.[tf] || null;
      return request.target_mode === 'GENERATED' && tf === 'SIGNAL' ? virtualDraftFrames : null;
    }
    function planSettingsText(request) {
      const value=key=>request[key]===undefined || request[key]===null ? '기본값' : label(request[key],key);
      const summary = [(request.build_only===true ? '데이터 모드: ' : '재생 모드: ')+value('mode'),
        request.build_only===true ? '전략 실행 없음' : '결과 방식: '+value('result_mode'),
        '스프레드: '+(request.spread_points===undefined || request.spread_points===null ? '기본값' : request.spread_points+' 포인트'),
        '수수료: '+(request.commission===undefined || request.commission===null ? '기본값' : '1랏 왕복 '+request.commission+'달러'),
        request.available_only===true ? '보유 데이터만 · 없는 구간 구축 안 함' : '데이터 부족 시 구축 확인',
        '재구축: '+value('rebuild')].join(' · ');
      return request.result_mode === 'VIRTUAL_ENTRY' && request.build_only !== true
        ? summary + '\n' + window.part3IntentDisplay.virtualEntry(request.virtual_entry, virtualFrames(request)) +
          (virtualDraftPending && request.target_mode === 'GENERATED' && !request.filename
            ? '\n시간봉: 수정한 전략 검증 중' : '')
        : summary;
    }
    function backtest(parent) {
      makePlan();
      const section=node('section',undefined,'ai-edit-backtest');section.append(node('h4','백테스트 실행 설정'));parent.append(section);
      const rows=plan?.steps;
      const commandSchema=contract.plan_schema?.properties?.steps?.items?.properties?.command;
      const properties=commandSchema?.properties?.request?.properties || {};
      if (!Array.isArray(rows) || !rows.length || !commandSchema) {
        localErrors.push({path:['plan'],message:'백테스트 설정 계약을 불러오지 못했습니다.'}); return;
      }
      rows.forEach((row,index)=>{
        section.append(node('h5',rows.length>1 ? `순차 작업 ${index+1}` : '실행할 구간'));
        const request=row.command?.request, prefix=['plan','steps',index,'command','request'];
        if (row.command?.action !== 'START' || !request) {
          localErrors.push({path:['plan','steps',index],message:'조회·중지 계획은 이 전략 편집표에서 실행할 수 없습니다.'});return;
        }
        let editor=section;
        if (compactMode) {
          preview(section,()=>executionTarget(plan.steps[index]),'ai-core-plan-target');
          preview(section,()=>{
            const current=plan.steps[index].command.request;
            return `${current.symbol || '종목 미지정'} · ${current.start || '시작일 미지정'} ~ ${current.end || '종료일 미지정'} (종료일 미포함)`;
          },'ai-core-plan-period');
          preview(section,()=>planSettingsText(plan.steps[index].command.request),'ai-core-plan-options');
          editor=editFold(section,pathKey(prefix));
        }
        const grid=node('div',undefined,'ai-edit-grid');editor.append(grid);
        const target=field(grid,[...prefix,'target_mode'],'실행 대상');
        const targetText=node('span',executionTarget(row),'ai-edit-readonly');
        targetText.id=target.id;
        target.cell.append(targetText);targetDescriptions.push({element:targetText,index});finish(target);
        const symbolSlot=field(grid,[...prefix,'symbol'],'백테스트 종목');
        const symbols=options.backtest_symbols?.length ? options.backtest_symbols :
          contract.symbols || options.symbols || values(meaningSchema.properties?.symbols?.items);
        select(symbolSlot,symbols,request.symbol,next=>{set([...prefix,'symbol'],next);changed();},false);finish(symbolSlot);
        for (const key of ['start','end']) {
          const slot=field(grid,[...prefix,key],names[key]), date=node('input');date.type='date';
          date.value=/^\d{4}-\d{2}-\d{2}$/.test(request[key] || '') ? request[key] : '';
          date.addEventListener('change',()=>{set([...prefix,key],date.value || null);changed();});attach(slot,date);finish(slot);
        }
        for (const key of ['mode','result_mode','spread_points','commission','available_only']) if (properties[key])
          control(grid,[...prefix,key],properties[key],false);
        const rest=node('details',undefined,'ai-edit-details');rest.append(node('summary','기존 백테스트 옵션'));
        const restGrid=node('div',undefined,'ai-edit-grid');rest.append(restGrid);
        for (const key of ['build_only','rebuild']) if (properties[key]) control(restGrid,[...prefix,key],properties[key]);
        details(rest,'기존 실행 대상·옵션 (그대로 유지)',Object.fromEntries(Object.entries(request).filter(([key])=>
          !['symbol','start','end','mode','result_mode','spread_points','commission','available_only','build_only','rebuild'].includes(key))));
        editor.append(rest);
      });
      if (!compactMode) {
        section.append(node('p','종료일은 포함하지 않습니다. 데이터 구축이 필요하면 기존 진행 화면에서 추가 확인합니다.','muted'));
        if (planOnly()) section.append(node('p','실행 대상의 원본 전략 조건은 그대로 사용합니다. 실행 대상 변경은 대화로 요청해 주세요.','muted'));
      }
    }
    function requiredErrors() {
      const result=[];
      function check(value,schema,path) {
        schema=schema || {};
        if (schema.$ref === '#/$defs/step') schema=stepSchema(value?.kind) || {};
        if (schema.anyOf) schema=concrete(schema,typeof value === 'number' ? 'integer' : typeof value);
        const allowed=values(schema);
        if (value === undefined || value === null) return;
        if (allowed.length && !allowed.includes(value)) result.push({path,message:'허용된 값을 선택해 주세요.'});
        if (schema.type === 'object') {
          for (const key of schema.required || []) if (value[key] === undefined || value[key] === null || value[key] === '')
            result.push({path:[...path,key],message:(names[key] || '필수 항목')+'을 입력하거나 선택해 주세요.'});
          for (const [key,child] of Object.entries(value)) if (schema.properties?.[key]) check(child,schema.properties[key],[...path,key]);
        }
        if (schema.type === 'array') {
          if (!Array.isArray(value) || value.length < (schema.minItems || 0)) result.push({path,message:'하나 이상 선택해 주세요.'});
          if (Array.isArray(value)) value.forEach((item,index)=>check(item,schema.items,[...path,index]));
        }
        const numericType=Array.isArray(schema.type) ? schema.type.find(type=>type==='integer' || type==='number') : schema.type;
        if (numericType === 'integer' || numericType === 'number') {
          if (typeof value !== 'number' || !Number.isFinite(value) || numericType === 'integer' && !Number.isInteger(value))
            result.push({path,message:numericType==='integer'?'정수를 입력해 주세요.':'숫자를 입력해 주세요.'});
          else if (schema.minimum !== undefined && value < schema.minimum)
            result.push({path,message:`${schema.minimum} 이상의 숫자를 입력해 주세요.`});
          else if (schema.exclusiveMinimum !== undefined && value <= schema.exclusiveMinimum)
            result.push({path,message:`${schema.exclusiveMinimum}보다 큰 숫자를 입력해 주세요.`});
        }
        if (schema.pattern && typeof value === 'string' && !(new RegExp(schema.pattern)).test(value))
          result.push({path,message:'허용된 값으로 수정해 주세요.'});
      }
      if (meaning()) check(meaning(),meaningSchema,['strategy','interpretation']);
      if (strategy?.needs_clarification===true) result.push({path:['strategy','needs_clarification'],
        message:'조건을 수정한 뒤 이 표의 조건으로 확정하거나, 대화로 추가 설명해 주세요.'});
      if (operation==='BACKTEST') {
        (plan?.steps || []).forEach((row,index)=>{
          if (row.command?.action !== 'START') return;
          const request=row.command?.request || {}, prefix=['plan','steps',index,'command','request'];
          if (row.draft && !meaning()) result.push({path:['plan','steps',index,'draft'],message:'현재 전략을 실행하려면 유효한 전략 해석이 필요합니다.'});
          for (const key of ['symbol','start','end']) if (!request[key]) result.push({path:[...prefix,key],message:(names[key] || key)+'을 선택해 주세요.'});
          if (request.start && request.end && request.start>=request.end) result.push({path:[...prefix,'end'],message:'종료일은 시작일 이후로 선택해 주세요.'});
        });
        if (plan?.needs_clarification) result.push({path:['plan'],message:plan.clarification_question || '백테스트 설정을 대화로 추가 설명해 주세요.'});
      }
      return result;
    }
    function normalizedErrors() {
      const errors=[...localErrors,...requiredErrors(),...externalErrors];
      return errors.map(error=>{
        const path=pathOf(error.path || error.field);
        if (path[0]==='interpretation') path.unshift('strategy');
        return {path,message:error.message || error.message_ko || '이 값을 수정해 주세요.'};
      });
    }
    function paintErrors() {
      for (const slot of fields.values()) {slot.cell.classList.remove('ai-edit-invalid');slot.error.textContent='';
        slot.inputs.forEach(input=>input.setAttribute('aria-invalid','false'));}
      const errors=normalizedErrors();
      for (const error of errors) {
        let key=pathKey(error.path), slot=fields.get(key);
        while (!slot && key.includes('.')) {key=key.slice(0,key.lastIndexOf('.'));slot=fields.get(key);}
        if (slot) {slot.cell.classList.add('ai-edit-invalid');slot.error.textContent=error.message;
          slot.inputs.forEach(input=>input.setAttribute('aria-invalid','true'));
          for (let parent=slot.cell.parentNode;parent && parent!==form;parent=parent.parentNode)
            if (parent.tagName==='DETAILS') parent.open=true;
        }
      }
      summary.textContent=errors.length ? '붉게 표시된 항목을 수정해 주세요. '+[...new Set(errors.filter(error=>{
        let key=pathKey(error.path);while (key) {if(fields.has(key))return false;key=key.includes('.')?key.slice(0,key.lastIndexOf('.')):'';}return true;
      }).map(error=>(compactMode ? '상세보기에서 수정: ' : '')+error.message))].join(' ') :
        '해석표를 확인하고 마지막 확인 버튼을 눌러 주세요. 아직 적용하거나 실행하지 않습니다.';
      summary.classList.toggle('has-errors',errors.length>0);
    }
    function refreshDescriptions() {
      const display=window.part3IntentDisplay;
      previews.forEach(({element,describe})=>{element.textContent=describe();});
      targetDescriptions.forEach(description=>{
        const row=plan?.steps?.[description.index];
        if (row) description.element.textContent=executionTarget(row);
      });
      form.querySelectorAll?.('[data-condition-path]').forEach(description=>{
        const value=get(description.dataset.conditionPath.split('.'));
        if (value) description.textContent=display?.condition(value,ownerDirection(description.dataset.conditionPath.split('.'))) ||
          labels[value.kind] || '상세보기에서 조건을 확인하세요.';
      });
    }
    function relationText() {
      const value=meaning();
      return [label(value.order_mode || 'SIMULTANEOUS','order_mode'),label(value.global_combine || 'ALL','global_combine'),
        value.within_sec ? '조건 간 '+value.within_sec+'초 이내' : value.within ? '조건 간 '+windowLabel(value.within)+' 이내' : '',
        value.final_window_sec ? '최종 감시 '+value.final_window_sec+'초 이내' : '',
        value.persistent===false ? '1회 감시' : '반복 감시'].filter(Boolean).join(' · ');
    }
    function operationControl(parent) {
      const slot=field(parent,['operation'],'진행할 작업');
      if (meaning()) select(slot,['STRATEGY','BACKTEST'],operation,next=>{operation=next;changed(true);},false);
      else {const selected=node('span','백테스트','ai-edit-readonly');selected.id=slot.id;slot.cell.append(selected);}
      finish(slot);
    }
    function render() {
      form.replaceChildren(); fields.clear(); controls.length=0; targetDescriptions.length=0;previews.length=0;
      localErrors=[];canonicalPre=null;
      element.classList.toggle('ai-core-mode',compactMode);
      const heading=node('div',undefined,'ai-edit-heading');
      heading.append(node('h3',compactMode ? '핵심 요약' : planOnly() ? '백테스트 계획표' : '전략 해석표'),
        button(compactMode ? '상세보기' : '핵심 요약',()=>{compactMode=!compactMode;render();},'ai-edit-view-toggle'));
      form.append(heading);
      if (!compactMode) form.append(node('p','작은 변경은 슬롯과 숫자로 수정하세요. 해석이 크게 다르면 대화로 다시 설명해 주세요.','muted'));
      if (operation==='BACKTEST' && plan && !startPlan()) {
        form.append(node('p','조회·중지 계획은 실행 설정 편집 대상이 아닙니다. 대화에 표시된 작업 내용을 확인해 주세요.','muted'));
        localErrors.push({path:['plan'],message:'백테스트 시작 계획만 실행 설정을 편집할 수 있습니다.'});paintErrors();return;
      }
      if ((!meaning() && !planOnly()) || (meaning() && (!meaningSchema.properties || !kinds.length))) {
        form.append(node('p','편집할 전략 해석이 없습니다. 자연어로 전략을 먼저 설명해 주세요.','muted'));
        localErrors.push({path:['strategy'],message:'유효한 전략 해석과 편집 계약이 필요합니다.'});paintErrors();return;
      }
      if (compactMode) {
        const grid=node('div',undefined,'ai-edit-grid ai-edit-overview');form.append(grid);
        if (meaning()) for (const key of ['symbols','direction']) control(grid,['strategy','interpretation',key],
          meaningSchema.properties[key],(meaningSchema.required || []).includes(key),
          key==='direction' && meaning().branches?.length ? '공통 방향' : undefined);
        operationControl(grid);
      }
      if (meaning()) {
        const prefix=['strategy','interpretation'];
        if (compactMode) {
          preview(form,relationText,'ai-core-relation');
          const edit=editFold(form,'overview','조건 관계 수정'), grid=node('div',undefined,'ai-edit-grid');edit.append(grid);
          for (const key of ['order_mode','global_combine',waitKey(meaning()),'final_window_sec'])
            control(grid,[...prefix,key],meaningSchema.properties[key],(meaningSchema.required || []).includes(key));
        } else {
          const grid=node('div',undefined,'ai-edit-grid ai-edit-overview');form.append(grid);
          for (const key of ['symbols','direction','order_mode','global_combine',waitKey(meaning()),'final_window_sec'])
            control(grid,[...prefix,key],meaningSchema.properties[key],(meaningSchema.required || []).includes(key));
        }
        if (hasQuestion) {
          const question=node('section',undefined,'ai-edit-clarification');
          question.append(node('h4','AI 확인 질문'),node('p',originalQuestion || '해석된 조건을 확인해 주세요.','muted'));
          const allowed=output.properties?.needs_clarification?.type==='boolean' ? [true,false] : values(output.properties?.needs_clarification);
          const slot=field(question,['strategy','needs_clarification'],'확인 질문');
          select(slot,allowed,strategy.needs_clarification,next=>{
            strategy.needs_clarification=next;
            strategy.clarification_question=next===false ? null : originalQuestion;
            changed();
          },false);finish(slot);form.append(question);
        }
        if (!compactMode || !meaning().branches?.length) {
          steps(form,[...prefix,'steps'],meaning().steps,'조건',compactMode);
          final(form,[...prefix,'final'],meaning().final,meaningSchema.properties.final,compactMode);
        } else form.append(node('p',`독립 분기 ${meaning().branches.length}개 · 분기별 조건과 최종 행동은 추가 조건 또는 상세보기에서 확인하세요.`,
          'ai-core-branches'));
        if (!compactMode || hasRetainedRules(meaning(),meaningSchema)) retainedRules(form,prefix,meaning(),meaningSchema,compactMode);
      }
      const action=compactMode ? form : node('section',undefined,'ai-edit-operation');
      if (!compactMode) {form.append(action);operationControl(action);}
      if (operation==='BACKTEST') backtest(action);
      if (!compactMode) canonicalPre=details(form,'상세 보기 · 전체 해석 / JSON (기존 옵션 포함)',canonicalValue());
      refreshDescriptions();
      controls.forEach(control=>{control.disabled=disabled;});paintErrors();
    }
    function getDraft() {return {strategy:copy(strategy),operation,plan:operation==='BACKTEST' ? copy(plan) : null};}
    function setErrors(errors) {
      externalErrors=Array.isArray(errors) ? copy(errors) : Object.entries(errors || {}).map(([path,message])=>({path,message}));
      paintErrors();
    }
    function setVirtualDraftFrames(frames) {
      virtualDraftFrames = copy(frames) || null; virtualDraftPending = false;
      refreshDescriptions();
    }
    function setDisabled(value) {disabled=Boolean(value);controls.forEach(control=>{control.disabled=disabled;});}
    function destroy() {destroyed=true;element.remove();fields.clear();controls.length=0;}
    render();setErrors(contract.errors || []);
    return {element,getDraft,setErrors,setVirtualDraftFrames,setDisabled,destroy};
  }
  window.part3IntentEditor=Object.freeze({create});
})();
