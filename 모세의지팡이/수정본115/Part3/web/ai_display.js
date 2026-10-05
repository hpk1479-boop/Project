/* Presentation only. Never normalize or mutate the canonical intent. */
(() => {
  'use strict';
  const labels = Object.freeze({
    LONG:'매수', SHORT:'매도', BOTH:'매수·매도', SAME_AS_PREVIOUS_DIRECTION:'이전 조건과 같은 방향',
    NORMAL:'일반', BLIND:'무지성', BREAKER:'브레이커', OZ:'올존', NOTIFY:'알림', DEFINE:'조건 정의만 저장',
    ALL:'모든 조건을 만족', ANY:'하나 이상의 조건을 만족', INDEPENDENT:'조건별 독립 판단',
    SIMULTANEOUS:'동시에 평가', SEQUENTIAL:'조건 순서대로 충족', UNORDERED:'순서와 관계없이 사건 충족',
    CLOSED:'확정봉 기준', FORMING:'진행봉 기준', UNSPECIFIED:'봉 기준 별도 지정 없음',
    TREND:'추세', CANDLE_STATE:'캔들 양봉·음봉', WONBI_TOUCH:'WONBI 터치', FVG_NEW:'FVG 신규 발생', FVG_TOUCH:'FVG 터치',
    PRICE:'가격', RSI:'RSI', STO:'스토캐스틱', DI:'이격도', MATCHING_FAMILY:'앞 조건과 같은 지표 계열',
    SOURCE:'선행 조건 시간봉', FINAL:'최종 감시 시간봉', DAY_OPEN:'당일 시가',
    MAIN_ASIA:'아시아장', MAIN_LONDON:'런던장', MAIN_NEWYORK:'뉴욕장',
    PDH:'전일 고가', PDL:'전일 저가', PWH:'전주 고가', PWL:'전주 저가',
    DAILY:'일간', WEEKLY:'주간', ASIA:'아시아장', LONDON:'런던장', NEWYORK:'뉴욕장'
  });
  const text = (value, fallback='상세 보기에서 확인') => labels[value] || fallback;
  const readable = value => String(value || '').replace(/\b[A-Z][A-Z0-9_]*\b/g, word => labels[word] || word);
  const frame = value => {
    if (value === 'SOURCE' || value === 'FINAL') return text(value);
    const match = /^(\d+)(m|h|d|w)$/.exec(value || '');
    return match ? match[1] + {m:'분봉',h:'시간봉',d:'일봉',w:'주봉'}[match[2]] : '지정 시간봉';
  };
  const frames = item => (item.tfs || (item.tf ? [item.tf] : [])).map(frame).join(' · ');
  const bullish = direction => direction === 'LONG' ? '상승' : direction === 'SHORT' ? '하락' :
    direction === 'SAME_AS_PREVIOUS_DIRECTION' ? '이전 조건과 같은 방향의' : '상승 또는 하락';
  const profile = item => [text(item.validation_mode, '모드 별도 지정 없음'),
    text(item.trigger_mode || 'OZ', '유형 상세 확인')].join(' ');
  const side = value => ({LOWER:'하단',UPPER:'상단',LOW:'저점',HIGH:'고점',BULL:'상승',BEAR:'하락',
    ABOVE:'위',BELOW:'아래',UP:'상승',DOWN:'하락'})[value];
  const level = value => typeof value === 'number' ? String(value) :
    ({ALL:'전체 유동성',DAILY:'일간 유동성',WEEKLY:'주간 유동성',H4:'이전 4시간봉',
      '4H':'이전 4시간봉','1H':'이전 1시간봉'})[value] || text(value, '지정 가격·유동성 기준');
  const duration = value => value >= 60 ? `${Math.floor(value / 60)}분` +
    (value % 60 ? ` ${value % 60}초` : '') : `${value}초`;
  const relation = value => ({ABOVE:'위에 있음',BELOW:'아래에 있음',TOUCH:'터치',BREAK_UP:'상향 돌파',
    BREAK_DOWN:'하향 돌파',BOTH:'상향 또는 하향 교차',SLOPE_UP:'기울기 상승',SLOPE_DOWN:'기울기 하락',IN:'밴드 안에 있음',
    OUT_LOWER:'밴드 하단 밖에 있음',OUT_UPPER:'밴드 상단 밖에 있음'})[value] || '지정 관계 확인';

  function condition(item, inheritedDirection) {
    const direction = item.direction || inheritedDirection;
    const tf = frames(item), at = tf ? tf + ' ' : '';
    const ma = (item.ma_family || '') + (item.slow_period || '');
    const position = item.side || (direction === 'LONG' ? 'ABOVE' : direction === 'SHORT' ? 'BELOW' : null);
    let description;
    switch (item.kind) {
      case 'TREND': description = `${tf || '지정 시간봉'}이 ${bullish(direction)}추세`; break;
      case 'CANDLE_STATE': description = `${at}${({BULL:'양봉',BEAR:'음봉'})[item.side] || '캔들 상태 확인'}`; break;
      case 'TREND_METRIC': {
        const operator = ({GT:'초과',GTE:'이상',LT:'미만',LTE:'이하',EQ:'같음',NE:'다름'})[item.metric_operator] || '비교';
        description = `${at}${String(item.metric || '').toUpperCase()} ${item.metric_value} ${operator}`;
        break;
      }
      case 'WONBI_TOUCH': description = `${at}${side(item.side) ||
        (direction === 'LONG' ? '하단' : direction === 'SHORT' ? '상단' : '상·하단')} WONBI 터치`; break;
      case 'FVG_STATE': {
        const type = side(item.side) || bullish(direction);
        description = item.state === 'AREA' ? `${at}${type} FVG 영역 접촉` : `${at}${type} FVG가 존재`;
        break;
      }
      case 'FVG_NEW': description = `${at}${side(item.side) || bullish(direction)} FVG 신규 발생`; break;
      case 'FVG_TOUCH': description = `${at}${side(item.side) || bullish(direction)} FVG 터치`; break;
      case 'MA_STATE': description = position ? `${at}${item.ma_family}${item.fast_period}이 ${ma} ${side(position)}` :
        `${at}${item.ma_family}${item.fast_period}와 ${ma}의 위치 비교`; break;
      case 'MA_PRICE_STATE': description = position ? `${at}${item.price_tf ? frame(item.price_tf) + ' ' : ''}가격이 ${ma} ${side(position)}` : `${at}가격과 ${ma} 비교`; break;
      case 'MA_PRICE_CROSS': description = `${at}가격이 ${ma} ${relation(item.relation)}`; break;
      case 'MA_PRICE_TOUCH': description = `${at}가격이 ${ma} 터치`; break;
      case 'MA_SLOPE_STATE': description = `${at}${ma} 기울기` + (item.side ? ` ${side(item.side)}` : ' 비교') +
        (item.lookback ? ` · ${item.lookback}개 봉 전과 비교` : ''); break;
      case 'MA_CROSS': description = `${at}${item.ma_left}와 ${item.ma_right} ` +
        (direction === 'LONG' ? '상향 교차' : direction === 'SHORT' ? '하향 교차' : '교차'); break;
      case 'EXTERNAL_LIQUIDITY_TOUCH': description = `${at}${item.level ? level(item.level) + ' ' : ''}` +
        `${side(item.side) || ''} 외부 유동성 터치`; break;
      case 'SESSION_START': description = `${at}${text(item.session, '지정 세션')} 시작`; break;
      case 'BAR_CLOSE': description = `${at}봉 마감`; break;
      case 'PERCENTILE_OUT': case 'PERCENTILE_OUT_IN': {
        const families = (item.families || []).map(value => text(value, '지정 지표')).join(' · ');
        description = `${at}${families} ${side(item.side) || (direction === 'LONG' ? '하단' : direction === 'SHORT' ? '상단' : '상·하단')} ` +
          (item.kind === 'PERCENTILE_OUT' ? '밴드 밖으로 이탈' : '밴드 이탈 후 안으로 복귀');
        if (item.family_combine) description += ' · 지표 계열: ' + text(item.family_combine);
        break;
      }
      case 'OZ_ALERT': description = `${at}${profile(item)} 알림 발생`; break;
      case 'REGIME_BAND': {
        const families = (item.regime_families || [item.regime_family]).filter(Boolean).map(value => text(value, '지정 지표')).join(' · ');
        const bandRelation = ({ABOVE:'밴드 상단 초과',BELOW:'밴드 하단 미만'})[item.relation] || relation(item.relation);
        description = `${at}${families} 레짐 밴드 · ${bandRelation}`;
        if (item.family_combine) description += ' · 지표 계열: ' + text(item.family_combine);
        break;
      }
      case 'PRICE_LEVEL': case 'LIQUIDITY_LEVEL': description = `${at}${level(item.level)} 기준 · ${relation(item.relation)}`; break;
      default: description = '추가 조건 (상세 보기에서 확인)';
    }
    const notes = [];
    if (item.negated) description = `다음 조건을 만족하지 않음: ${description}`;
    if (item.ref) notes.push('선행 사건에서 기록한 동일한 영역만 사용');
    if (item.scope_ref) notes.push('선행 사건의 영역 안에서만 성립');
    if (item.kind !== 'TREND' && direction) notes.push((item.kind === 'CANDLE_STATE' ? '전략 방향: ' : '판정 방향: ') + text(direction));
    if (item.bar_state && item.bar_state !== 'UNSPECIFIED') notes.push(text(item.bar_state));
    else if (['MA_CROSS','MA_PRICE_CROSS','MA_PRICE_TOUCH'].includes(item.kind)) notes.push('확정봉 기준');
    if ((item.tfs || []).length > 1) notes.push('시간봉 연결: ' +
      ({ALL:'모든 시간봉',ANY:'시간봉 중 하나 이상',INDEPENDENT:'시간봉별 독립'})[item.tf_combine || 'ANY']);
    return [description.replace(/\s+/g,' ').trim(), ...notes].join(' · ');
  }

  function element(tag, content, className) {
    const node = document.createElement(tag);
    if (content !== undefined) node.textContent = content;
    if (className) node.className = className;
    return node;
  }
  function row(parent, name, value) {
    const line = element('div', undefined, 'ai-meaning-row');
    line.append(element('dt', name), element('dd', value)); parent.append(line);
  }
  function conditions(parent, heading, items, direction) {
    if (!items?.length) return;
    const group = element('section', undefined, 'ai-condition-group');
    group.append(element('h4', heading));
    const list = element('ol');
    items.forEach((item, index) => list.append(element('li', `조건 ${index + 1} · ${condition(item, direction)}`)));
    group.append(list); parent.append(group);
  }
  function lifecycle(parent, rules, direction) {
    if (!rules || !Object.keys(rules).length) return;
    const rows = element('dl', undefined, 'ai-meaning-summary');
    if (rules.expires?.seconds) row(rows, '감시 수명', duration(rules.expires.seconds));
    if (rules.expires?.bars) row(rows, '감시 수명', `${frame(rules.expires.tf)} ${rules.expires.bars}개 봉`);
    if (rules.replace) row(rows, '새 조건 발생', rules.replace.scope === 'SYMBOL' ?
      '같은 종목의 이전 감시를 교체' : '같은 종목·방향의 이전 감시를 교체');
    if (rules.first_success) row(rows, '알림 후 처리', '하위 감시 중 처음 성립한 알림 이후 같은 묶음의 감시 종료');
    if (rules.invalidate_refs) row(rows, '영역 무효화', '선행 영역이 무효화되면 연결된 후속 감시도 무효화');
    if (Object.keys(rules.snapshots || {}).length) row(rows, '고정 기준값',
      Object.values(rules.snapshots).map(value => `${frame(value.tf)} 확정봉 ` +
        ({open:'시가',high:'고가',low:'저가',close:'종가',ATR14:'ATR14'})[value.field]).join(' · '));
    if (rules.excursion) row(rows, '이동 폭 취소', `시작 시점의 기준값에서 ` +
      (rules.excursion.direction === 'ADVERSE' ? '반대 방향' : '진행 방향') +
      `으로 고정 변동폭의 ${rules.excursion.multiplier}배 이동하면 취소`);
    parent.append(element('h4', '감시 수명과 취소'), rows);
    conditions(parent, '새 감시 주기 시작', rules.restart_on, direction);
  }

  function meaning(parent, item, branch=false) {
    const summary = element('dl', undefined, 'ai-meaning-summary');
    if (!branch) row(summary, '종목', (item.symbols || []).join(', ') +
      (item.symbol_source === 'CURRENT_DEFAULT' ? ' (현재 기본값)' : ''));
    row(summary, '방향', text(item.direction));
    row(summary, '조건 관계', text(item.order_mode || 'SIMULTANEOUS') + ' · ' + text(item.global_combine || 'ALL'));
    const limits = [];
    if (item.within_sec) limits.push(`조건 사건 사이 ${duration(item.within_sec)} 이내`);
    else if (item.order_mode === 'SEQUENTIAL' || item.order_mode === 'UNORDERED') limits.push('사건 사이 대기 시간 제한 없음');
    if (item.final_window_sec) limits.push(`최종 감시 유효시간 ${duration(item.final_window_sec)}`);
    row(summary, '시간 제한', limits.join(' · ') || '별도 시간 제한 지정 없음');
    const final = item.final || {};
    const action = final.kind === 'NOTIFY' ? '조건 충족 시 알림' : final.kind === 'DEFINE' ?
      '조건 정의만 저장 · 알림 및 감시 실행 없음' : `${frames(final)} ${profile(final)} 감시`.trim();
    row(summary, '최종 행동', action + (final.direction ? ' · ' + text(final.direction) : '') +
      (final.regime_family ? ' · ' + text(final.regime_family) + ' 지표 계열' : '') +
      (final.scope_ref ? ' · 선행 사건의 동일 영역 안에서만 허용' : ''));
    row(summary, '감시 모드', item.persistent === false ? '1회 감시' : '반복 감시');
    if (item.final_after) row(summary, '최종 감시 시작', text(item.final_after) + ' 이후');
    if (item.preset) row(summary, '기반 프리셋', item.preset);
    parent.append(summary);
    conditions(parent, '조건', item.steps, item.direction);
    if (!item.steps?.length) parent.append(element('p', item.branches?.length ? '조건은 아래 독립 분기에서 확인하세요.' :
      '선행 조건 없이 최종 행동을 감시합니다.', 'muted'));
    conditions(parent, '선행 사건 충족 후 유지할 조건', item.after_conditions, item.direction);
    conditions(parent, '최종 행동 시 다시 확인할 조건', item.final_conditions, item.direction);
    conditions(parent, '감시 취소 조건', item.cancel_conditions, item.direction);
    lifecycle(parent, item.lifecycle, item.direction);
    if (!branch) (item.branches || []).forEach((value, index) => {
      const group = element('section', undefined, 'ai-branch');
      group.append(element('h4', `독립 분기 ${index + 1}`));
      meaning(group, {...item,...value}, true); parent.append(group);
    });
  }

  function render(response) {
    const card = element('div', undefined, 'ai-msg ai-assistant ai-interpretation');
    card.append(element('h3', 'AI 해석 결과'));
    const result = response.result || {};
    if (result.supported && result.interpretation) meaning(card, result.interpretation);
    else card.append(element('p', readable(result.message_ko) || '현재 지원 범위에 없는 전략입니다. 표현을 확인해 주세요.'));
    if (result.needs_clarification) card.append(element('p', '확인 필요: ' + readable(result.clarification_question), 'ai-clarification'));
    if (response.application_error) card.append(element('p',
      '현재 해석을 전략 파일로 적용할 수 없습니다. 상세 보기에서 제한 사항을 확인하세요.', 'ai-error'));
    if ((response.actions || []).some(action => !action.ok)) card.append(element('p',
      '조회 중 일부 항목을 확인하지 못했습니다. 상세 보기에서 오류 내용을 확인하세요.', 'ai-error'));
    const details = element('details', undefined, 'ai-intent-details');
    details.append(element('summary', '상세 보기 · 원본 해석 / JSON'), element('pre', JSON.stringify(response, null, 2)));
    card.append(details);
    return card;
  }
  function explain(item, branch=false) {
    if (!item) return [];
    const sentences = [];
    if (!branch) sentences.push(`${(item.symbols || []).join(', ') || '지정 종목'}에서 ${text(item.direction, '지정 방향')} 조건을 확인합니다.`);
    else sentences.push(`이 분기는 ${text(item.direction, '지정 방향')} 조건을 확인합니다.`);
    const describe = value => [condition(value,item.direction),
      value.capture ? `사건을 ${value.capture}로 기록` : '',
      value.ref ? `동일 사건 참조: ${value.ref}` : '',
      value.scope_ref ? `발생 범위 참조: ${value.scope_ref}` : ''].filter(Boolean).join(' · ');
    const steps = (item.steps || []).map(describe);
    if (steps.length) {
      const order = item.order_mode === 'SEQUENTIAL' ? '순서대로' : item.order_mode === 'UNORDERED' ? '발생 순서와 관계없이' : '동시에';
      const combine = item.global_combine === 'ANY' ? '하나 이상 충족되는지' : item.global_combine === 'INDEPENDENT' ? '각 조건을 독립적으로' : '모두 충족되는지';
      sentences.push(`${steps.map((value,index) => `조건 ${index+1}: ${value}`).join(' / ')}. 이 조건을 ${order} 확인하며, ${combine} 판단합니다.`);
    } else if (!item.branches?.length) sentences.push('선행 조건 없이 최종 행동을 감시합니다.');
    if (item.within_sec) sentences.push(`첫 조건부터 전체 조건이 충족될 때까지의 제한시간은 ${duration(item.within_sec)}입니다.`);
    else if (['SEQUENTIAL','UNORDERED'].includes(item.order_mode)) sentences.push('조건 사건 사이의 대기 시간 제한은 없습니다.');
    if (item.final_after) sentences.push(`${text(item.final_after,item.final_after)} 이후 최종 감시를 시작합니다.`);
    const final = item.final;
    if (final) {
      if (final.kind === 'DEFINE') sentences.push('조건 체인만 정의하며 알림이나 최종 감시를 실행하지 않습니다.');
      else if (final.kind === 'NOTIFY') sentences.push(`조건이 충족되면 ${text(final.direction || item.direction, '지정 방향')} 알림을 보냅니다.`);
      else if (final.kind === 'OZ') sentences.push(`${frames(final) || '지정 시간봉'} ${profile(final)}를 감시하고, 조건이 성립하면 ${text(final.direction || item.direction, '지정 방향')} 알림을 보냅니다.`);
      else sentences.push('최종 행동은 상세보기에서 확인해 주세요.');
      if (final.kind === 'OZ' && item.final_window_sec) sentences.push(item.lifecycle?.expires?.seconds ?
        `최종 감시 설정은 ${duration(item.final_window_sec)}이며, 감시 수명 ${duration(item.lifecycle.expires.seconds)}이 우선 적용됩니다.` :
        `최종 감시 유효시간은 ${duration(item.final_window_sec)}입니다.`);
      if ((final.tfs || []).length > 1) sentences.push(`최종 시간봉은 ${text(final.tf_combine || 'ANY')} 방식으로 판단합니다.`);
      if (final.scope_ref) sentences.push(`최종 행동은 기록된 사건 ${final.scope_ref}의 동일 영역 안에서만 허용합니다.`);
      if (final.regime_family) sentences.push(`최종 행동의 지표 계열은 ${text(final.regime_family)}입니다.`);
    }
    for (const [key, heading] of [['after_conditions','선행 조건 충족 후 추가 확인할 조건'],['final_conditions','최종 행동 시 다시 확인할 조건'],['cancel_conditions','감시 취소 조건']]) {
      if (item[key]?.length) sentences.push(`${heading}: ${item[key].map(describe).join(' / ')}.`);
    }
    const life = item.lifecycle || {};
    if (life.expires?.seconds) sentences.push(`감시 수명은 ${duration(life.expires.seconds)}입니다.`);
    if (life.expires?.bars) sentences.push(`감시 수명은 ${frame(life.expires.tf)} ${life.expires.bars}개 봉입니다.`);
    if (life.invalidate_refs) sentences.push('선행 영역이 무효화되면 연결된 후속 감시를 취소합니다.');
    if (life.first_success) sentences.push('같은 묶음에서 첫 알림이 성립하면 나머지 감시를 종료합니다.');
    if (life.replace) sentences.push(`${life.replace.scope === 'SYMBOL' ? '같은 종목' : '같은 종목·방향'}에 새 조건이 발생하면 이전 감시를 교체합니다.`);
    if (life.snapshots) sentences.push(`시작 시 고정하는 기준값: ${Object.entries(life.snapshots).map(([id,value]) => `${id} (${frame(value.tf)} 확정봉 ${({open:'시가',high:'고가',low:'저가',close:'종가',ATR14:'ATR14'})[value.field] || value.field})`).join(' / ')}.`);
    if (life.excursion) sentences.push(`시작점 ${life.excursion.anchor}에서 ${life.excursion.direction === 'ADVERSE' ? '반대 방향' : '진행 방향'}으로 고정 기준값 ${life.excursion.snapshot}의 ${life.excursion.multiplier}배 이동하면 감시를 취소합니다.`);
    if (life.restart_on?.length) sentences.push(`새 감시 주기 시작 조건: ${life.restart_on.map(describe).join(' / ')}.`);
    const times = value => Array.isArray(value) ? value.map(readable).join(', ') : typeof value === 'object' && value ? Object.entries(value).map(([key,row]) => {
      const title = text(key,key);
      if (row === false || row?.enabled === false) return title + ' 사용 안 함';
      if (row === true) return title + ' 사용';
      if (typeof row === 'object') return title + ' ' + (row.start || '기본 시작') + ' ~ ' + (row.end || '기본 종료');
      return title + ' ' + readable(row);
    }).join(', ') : value === 0 ? '거래시간 필터 없음' : readable(value);
    for (const [key,title] of [['time_filters','조건 거래시간'],['final_time_filters','최종 행동 거래시간']]) {
      if (item[key] !== undefined && item[key] !== null) sentences.push(`${title}: ${times(item[key]) || '별도 설정 없음'}.`);
    }
    sentences.push(item.persistent === false ? '1회 감시합니다.' : '반복 감시합니다.');
    const paragraphs = item.branches?.length ? [`${(item.symbols || []).join(', ') || '지정 종목'}에서 ${item.branches.length}개 독립 분기를 각각 감시합니다. 아래 설명은 공통 설정을 반영한 각 분기의 조건입니다.`] : [sentences.join(' ')];
    const merge = (base, patch) => {
      if (Object.prototype.hasOwnProperty.call(patch,'kind') && base.kind !== patch.kind) return JSON.parse(JSON.stringify(patch));
      const result=JSON.parse(JSON.stringify(base));
      for (const [key,value] of Object.entries(patch)) result[key]=value && typeof value==='object' && !Array.isArray(value) && result[key] && typeof result[key]==='object' && !Array.isArray(result[key]) ? merge(result[key],value) : JSON.parse(JSON.stringify(value));
      return result;
    };
    const base=Object.fromEntries(Object.entries(item).filter(([key])=>!['branches','preset'].includes(key)));
    (item.branches || []).forEach((value,index) => paragraphs.push(`독립 분기 ${index+1}: ${explain(merge(base,value),true).join(' ')}`));
    return paragraphs;
  }
  function virtualEntry(policy) {
    if (!policy) return '가상진입 정책: 저장 설정 사용';
    const tf = value => value === 'SIGNAL' ? '신호 시간봉' : frame(value);
    const ma = item => `${tf(item.tf)} ${item.family}${item.period}`;
    const rows = ['가상진입: ' + ({AUTO:'자동 (올존 확인 / 다른 알림 즉시)',IMMEDIATE:'즉시 · 알림 시점 가격',CONFIRM:'확인 진입'})[policy.mode]];
    if (policy.mode !== 'IMMEDIATE') {
      rows.push(`${tf(policy.tf)} 직전 봉 매수 양봉 / 매도 음봉 마감 → 현재봉 시가`);
      const options = (policy.conditions || []).map(item => {
        switch (item.kind) {
          case 'MA_POSITION': return `현재봉 시가가 ${ma(item)} 위(매수) / 아래(매도)`;
          case 'MA_CROSS': return `직전 확정봉 종가가 ${ma(item)} 상향(매수) / 하향(매도) 돌파`;
          case 'MA_TOUCH': return `${ma(item)} 터치 후 방향 확인`;
          case 'ENGULFING': return '직전 확정봉 인걸핑';
          case 'NECKLINE_BREAK': return '해당 올존 넥라인 확정봉 종가 돌파';
          default: return '확인 조건 확인 필요';
        }
      });
      if (options.length) rows.push('추가 확인: ' + options.join(' + '));
      else if (policy.mode === 'AUTO') rows.push('올존 기본 확인: 신호 시간봉 HMA6 시가 위치');
    }
    rows.push(`ATR: ${tf(policy.atr.tf)} ATR${policy.atr.period}`);
    for (const item of policy.filters || []) {
      const title = item.kind === 'CANDLE_ATR' ? '직전 확정봉 ' + ({BODY:'몸통',RANGE:'전체 길이'})[item.measure]
        : '현재봉 시가와 ' + ma(item) + ' 거리';
      const limits = ['min','max'].filter(key => item[key] !== null).map(key => `${item[key]}배 ${key === 'min' ? '이상' : '이하'}`);
      rows.push('ATR 필터: ' + title + ' · ' + limits.join(' / '));
    }
    const stop = policy.stop;
    rows.push('손절: ' + ({AUTO:`자동 (올존 B0 / 다른 알림 ATR ${stop.multiplier}배)`,
      OZ_B0:'해당 올존 B0 저점(매수) / 고점(매도)',
      RECENT_EXTREME:`${tf(stop.tf)} 직전 ${stop.bars}개 확정봉 극값`,
      ATR:`${tf(stop.tf)} ATR${policy.atr.period} × ${stop.multiplier}`})[stop.kind]);
    rows.push('익절: 손익비 1:1 / 1:1.5 / 1:2');
    return rows.join('\n');
  }
  window.part3IntentDisplay = Object.freeze({render, condition, explain, virtualEntry});
})();
