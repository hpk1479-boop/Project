// The model only interprets. This UI asks the trusted Part3 server to apply a
// validated intent to the existing editor after an explicit user click.
(() => {
  const token = sessionStorage.getItem('part3-token') || '';
  const session = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now());
  const $ = (s) => document.querySelector(s);
  let busy = false, revision = null, currentProvider = 'gemini', epoch = 0, kind = 'STRATEGY';
  let researchMode = 'strategy';
  let editor = null, editorVersion = 0, savedEditorVersion = 0, editorGeneration = 0;
  let editorValid = false, editTimer = null, editInFlight = null;
  let presetItems = [], presetSelectedId = '', presetRevision = null, presetDialogGeneration = 0, presetLoading = false;
  const presetGroupOpen = {builtin: true, generated: true};
  try {
    const saved = JSON.parse(sessionStorage.getItem('part3-strategy-groups') || '{}');
    for (const source of Object.keys(presetGroupOpen)) {
      if (typeof saved[source] === 'boolean') presetGroupOpen[source] = saved[source];
    }
  } catch (_) { /* A missing or invalid preference keeps both groups open. */ }
  const sequences = new Map();
  const phaseLabels = {planning:'계획 확인 중', plan:'계획 확인 중', starting:'시작 중', run:'실행 중',
    confirm:'데이터 구축 확인 필요', complete:'완료', cancelled:'중단됨', interrupted:'연결 중단', error:'오류', planned:'계획 조회 완료'};
  function setBusy(value) {
    busy = value;
    for (const id of ['ai-send', 'ai-confirm', 'ai-cancel', 'ai-reset', 'ai-research-strategy', 'ai-research-chat']) $('#' + id).disabled = value;
    const presetButton = $('#ai-preset-open');
    if (presetButton) presetButton.disabled = value;
    document.querySelectorAll('#ai-preset-list button').forEach(button => { button.disabled = value; });
    window.dispatchEvent(new CustomEvent('moses-strategy-list-busy', {detail: {busy: value}}));
    editor?.setDisabled(value);
    if (editor) $('#ai-confirm').disabled = value || !editorValid || editorVersion !== savedEditorVersion;
  }
  function closeEditor() {
    clearTimeout(editTimer);
    editor?.setDisabled(true);
    editor = null;
    editorGeneration += 1;
    editorVersion = savedEditorVersion = 0;
    editorValid = false;
  }
  function showEditorStatus(response) {
    const operation = response.operation || response.kind || kind;
    kind = operation;
    editorValid = response.ok !== false && !!(response.can_apply || response.can_confirm);
    $('#ai-pending').classList.remove('hidden');
    $('#ai-confirm').textContent = operation === 'BACKTEST' ? '확인 후 백테스트 실행' : '확인 후 전략 생성';
    $('#ai-pending-text').textContent = editorValid
      ? '표의 조건과 진행할 작업을 확인하세요. 최종 확인 전에는 파일을 생성하거나 실행하지 않습니다.'
      : '붉게 표시된 항목을 수정해 주세요. 수정이 어려우면 자연어로 다시 설명할 수 있습니다.';
    $('#ai-confirm').disabled = busy || !editorValid || editorVersion !== savedEditorVersion;
  }
  async function saveEditor() {
    clearTimeout(editTimer);
    if (editInFlight) {
      await editInFlight;
      return editor && editorVersion !== savedEditorVersion ? saveEditor() : editorValid;
    }
    if (!editor || editorVersion === savedEditorVersion) return editorValid;
    const active = editor, generation = editorGeneration, version = editorVersion, requestEpoch = epoch;
    const draft = active.getDraft();
    const pending = call('ai/edit', {session, research:true, revision, ...draft}).then(response => {
      if (requestEpoch !== epoch || generation !== editorGeneration || active !== editor) return;
      revision = response.revision ?? revision;
      if (version === editorVersion) {
        savedEditorVersion = version;
        active.setErrors(response.errors || []);
        active.setVirtualDraftFrames(response.ok !== false ? response.virtual_draft_frames : null);
        showEditorStatus(response);
      }
    }).catch(error => {
      if (requestEpoch !== epoch || generation !== editorGeneration || active !== editor) return;
      editorValid = false;
      if (version === editorVersion) active.setVirtualDraftFrames(null);
      $('#ai-confirm').disabled = true;
      $('#ai-pending-text').textContent = '표를 검증하지 못했습니다. 항목을 다시 수정하거나 자연어로 다시 설명해 주세요.';
      add('error', error.message);
      error.pendingPreserved = true;
      error.reported = true;
      throw error;
    });
    editInFlight = pending;
    try { await pending; }
    finally { if (editInFlight === pending) editInFlight = null; }
    return editor && editorVersion !== savedEditorVersion ? saveEditor() : editorValid;
  }
  async function mountEditor(response) {
    const result = response.editor_strategy || response.strategy?.result || response.result;
    const operation = response.operation || response.kind || 'STRATEGY';
    const rows = response.plan?.steps;
    const startPlan = Array.isArray(rows) && rows.length > 0 && rows.every(row =>
      row?.command?.action === 'START' && row.command.request && typeof row.command.request === 'object' &&
      !Array.isArray(row.command.request));
    if (!window.part3IntentEditor) return false;
    if (operation === 'BACKTEST') {
      if (!startPlan) return false;
    } else if (!result?.interpretation) return false;
    const requestEpoch = epoch;
    const contract = response.editor_contract || await call('ai/editor', {session, research:true});
    if (requestEpoch !== epoch) return true;
    if (operation === 'BACKTEST' ? !contract.plan_schema : !contract.schema)
      throw new Error('편집에 필요한 선택 항목을 불러오지 못했습니다. 다시 요청해 주세요.');
    closeEditor();
    const generation = editorGeneration;
    editor = window.part3IntentEditor.create({response, contract, onChange: () => {
      if (!editor || generation !== editorGeneration) return;
      editorVersion += 1;
      editorValid = false;
      window.part3InvalidateAIRecipe();
      $('#ai-pending').classList.remove('hidden');
      $('#ai-confirm').disabled = true;
      $('#ai-pending-text').textContent = '수정한 표를 검증하고 있습니다.';
      clearTimeout(editTimer);
      editTimer = setTimeout(() => { saveEditor().catch(() => {}); }, 250);
    }});
    $('#ai-messages').appendChild(editor.element);
    editor.element.scrollIntoView({block:'end'});
    editor.setErrors(response.errors || contract.errors || []);
    showEditorStatus(response);
    return true;
  }
  function selectMode(mode) {
    if (busy) return;
    researchMode = mode;
    for (const choice of ['strategy', 'chat']) {
      const button = $('#ai-research-' + choice);
      button.classList.toggle('active', choice === mode);
      button.setAttribute('aria-pressed', String(choice === mode));
    }
    $('#ai-description').textContent = mode === 'chat'
      ? '전략 아이디어와 조건을 대화로 논의하세요. 전략 작성이나 백테스트를 요청하면 해석을 확인한 뒤 진행합니다.'
      : '전략 조건을 해석·수정하고 백테스트를 요청하세요. 실행 조건과 순서를 확인한 뒤 진행합니다. 다른 전략은 ‘초기화’를 누른 뒤 시작합니다.';
    $('#ai-text').placeholder = mode === 'chat'
      ? '전략에 대해 이야기해 보세요. 작성·백테스트도 요청할 수 있습니다.'
      : '전략 설명·수정 또는 백테스트 요청 (예: 스페셜1 골드 최근 1년 백테스트)';
  }
  async function call(path, data) {
    const res = await fetch('/api/' + path, { method: data === undefined ? 'GET' : 'POST',
      headers: { 'X-Lab-Token': token, 'Content-Type': 'application/json' },
      body: data === undefined ? undefined : JSON.stringify(data) });
    const body = await res.json();
    if (!res.ok) {
      const error = new Error(body.error || res.statusText);
      error.pendingPreserved = body.pending_preserved === true;
      throw error;
    }
    return body;
  }
  function presetMessage(text, error = false) {
    const message = $('#ai-preset-message');
    if (!message) return;
    message.textContent = text;
    message.classList.toggle('has-error', error);
    message.setAttribute('role', error ? 'alert' : 'status');
  }
  function closePresetDialog(force = false) {
    if ((presetLoading || window.mosesStrategyListBusy?.()) && !force) return;
    presetDialogGeneration += 1;
    $('#ai-preset-dialog')?.close?.();
  }
  window.mosesCloseStrategyList = closePresetDialog;
  function updatePresetExample() {
    const selected = presetItems.find(item => item.id === presetSelectedId);
    $('#ai-preset-detail').hidden = !selected;
    $('#ai-preset-detail-title').textContent = selected?.name || '전략 상세';
    $('#ai-preset-example').textContent = selected?.example || '';
    $('#ai-preset-load').disabled = busy || !selected;
    document.querySelectorAll('#ai-preset-list [data-preset-id]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.presetId === selected?.id));
    });
    window.dispatchEvent(new CustomEvent('moses-strategy-list-selected', {detail: {item: selected || null}}));
  }
  function renderPresetNames() {
    const list = $('#ai-preset-list'); list.replaceChildren();
    for (const [source, title] of [['builtin', '스페셜 전략'], ['generated', '새로운 전략']]) {
      const section = document.createElement('details'); section.className = 'ai-strategy-group';
      section.open = presetGroupOpen[source];
      const heading = document.createElement('summary'); heading.textContent = title; section.append(heading);
      section.addEventListener('toggle', () => {
        presetGroupOpen[source] = section.open;
        try { sessionStorage.setItem('part3-strategy-groups', JSON.stringify(presetGroupOpen)); } catch (_) {}
      });
      const names = document.createElement('div'); names.className = 'ai-strategy-names'; section.append(names);
      const items = presetItems.filter(item => (item.source || 'builtin') === source);
      for (const item of items) {
        const button = document.createElement('button'); button.type = 'button';
        button.className = 'ai-strategy-name'; button.dataset.presetId = item.id;
        button.textContent = item.name; button.setAttribute('aria-pressed', 'false');
        button.addEventListener('click', () => {
          if (busy || presetLoading) return;
          presetSelectedId = item.id; updatePresetExample();
        });
        names.append(button);
      }
      if (!items.length) {
        const empty = document.createElement('p'); empty.className = 'muted';
        empty.textContent = source === 'generated' ? '생성한 전략이 없습니다.' : '등록된 스페셜이 없습니다.';
        names.append(empty);
      }
      list.append(section);
    }
  }
  async function openPresets({keepSelection = false} = {}) {
    const dialog = $('#ai-preset-dialog');
    if (busy || !dialog) return;
    const generation = ++presetDialogGeneration, requestEpoch = epoch;
    const selectedId = keepSelection ? presetSelectedId : '';
    presetItems = []; presetSelectedId = ''; presetRevision = null;
    $('#ai-preset-example').textContent = '';
    $('#ai-preset-list').replaceChildren(); $('#ai-preset-detail').hidden = true;
    $('#strategy-library-message').textContent = '';
    updatePresetExample();
    $('#ai-preset-load').disabled = true;
    presetMessage('전략 목록을 불러오는 중입니다.');
    if (!dialog.open) dialog.showModal();
    setBusy(true);
    try {
      const response = await call('ai/presets', {session, research:true});
      if (generation !== presetDialogGeneration || requestEpoch !== epoch) return;
      presetItems = (response.items || []).filter(item => typeof item.id === 'string' && typeof item.name === 'string' && typeof item.example === 'string');
      presetRevision = response.revision ?? null;
      presetSelectedId = presetItems.some(item => item.id === selectedId) ? selectedId : '';
      renderPresetNames();
      presetMessage('전략 이름을 선택하면 상세 내용이 표시됩니다.');
    } catch (error) {
      if (generation === presetDialogGeneration && requestEpoch === epoch) presetMessage(error.message, true);
    } finally {
      setBusy(false);
      if (generation === presetDialogGeneration && requestEpoch === epoch) updatePresetExample();
    }
  }
  window.addEventListener('moses-strategies-changed', () => {
    if ($('#ai-preset-dialog')?.open && !busy && !presetLoading) openPresets({keepSelection: true});
  });
  async function loadPreset() {
    if (busy || presetLoading) return;
    const selected = presetItems.find(item => item.id === presetSelectedId);
    if (!selected) return;
    const generation = presetDialogGeneration, requestEpoch = epoch;
    let loaded = false;
    presetLoading = true;
    setBusy(true);
    for (const id of ['ai-preset-load', 'ai-preset-cancel', 'ai-preset-close']) $('#' + id).disabled = true;
    presetMessage('선택한 전략의 조건을 검증하고 있습니다.');
    try {
      if (editor) await saveEditor();
      if (generation !== presetDialogGeneration || requestEpoch !== epoch) return;
      const response = await call('ai/preset/load', {session, research:true, preset_id:selected.id, revision:revision ?? presetRevision});
      if (generation !== presetDialogGeneration || requestEpoch !== epoch) return;
      if (response.ok === false) throw new Error((response.errors || []).map(error => error.message).filter(Boolean).join('\n') || response.message_ko || '전략을 불러오지 못했습니다. 목록을 다시 열어 주세요.');
      const example = response.example || selected.example;
      epoch += 1;
      closeEditor();
      const messages = $('#ai-messages');
      messages.textContent = '';
      // Keep existing job monitors; loading a strategy never stops a backtest.
      for (const state of sequences.values()) messages.appendChild(state.card);
      $('#ai-pending').classList.add('hidden');
      $('#ai-pending-text').textContent = '';
      window.part3InvalidateAIRecipe();
      $('#ai-text').value = example;
      add('user', example);
      add('tool', (response.preset?.name || selected.name) + '을 새 전략 초안으로 불러왔습니다. 문장이나 표를 수정할 수 있습니다. 원본은 보존됩니다.');
      await show(response);
      loaded = true;
      closePresetDialog(true);
    } catch (error) {
      if (!loaded) presetMessage(error.message, true);
    } finally {
      presetLoading = false;
      for (const id of ['ai-preset-cancel', 'ai-preset-close']) $('#' + id).disabled = false;
      setBusy(false);
      updatePresetExample();
      if (loaded) { selectMode('strategy'); $('#ai-text').focus(); }
    }
  }
  function add(role, text) {
    const div = document.createElement('div');
    div.className = 'ai-msg ai-' + role;
    div.textContent = text; // never inject model HTML
    $('#ai-messages').appendChild(div);
    div.scrollIntoView({ block: 'end' });
    return div;
  }
  async function show(response) {
    if (response.kind === 'CHAT') {
      add('assistant', response.message_ko);
      $('#ai-mode').textContent = '챗';
      if (!$('#ai-pending').classList.contains('hidden')) {
        add('tool', '대화에서 논의한 내용은 아직 적용되지 않았습니다. 아래 확인 버튼은 직전 해석에 대한 것입니다. 변경하려면 수정 또는 전략 작성을 요청하세요.');
      }
      return;
    }
    revision = response.revision ?? null;
    kind = response.kind || 'STRATEGY';
    closeEditor();
    const editable = await mountEditor(response);
    if (kind === 'STRATEGY' || response.strategy) {
      if (!editable) {
        const card = window.part3IntentDisplay.render(response.strategy || response);
        $('#ai-messages').appendChild(card);
        card.scrollIntoView({block: 'end'});
      }
    }
    if (kind === 'STRATEGY') {
      $('#ai-mode').textContent = response.result.supported ? (response.is_revision ? '수정된 전략 의도' : '전략 의도') : '지원 범위 밖';
      $('#ai-pending-text').textContent = response.can_apply ? '전체 해석을 다시 확인해 주세요. 수정할 내용은 대화로 요청하고, 확인 후 적용하세요. 아직 파일은 만들지 않습니다.' : '';
      $('#ai-confirm').textContent = '해석 확인 후 적용';
    } else {
      $('#ai-mode').textContent = kind === 'BACKTEST' ? '백테스트 연구' : '확인 필요';
      for (const text of [response.message_ko, response.preview].filter(Boolean)) add('tool', text);
      $('#ai-pending-text').textContent = response.can_confirm
        ? '전략·종목·기간·실행 순서와 가상진입·손절 설정을 확인하세요. 새 전략은 확인 후 생성하며, 데이터 구축이 필요하면 진행 화면에서 추가 확인합니다.' : '';
      $('#ai-confirm').textContent = response.action === 'STOP' ? '대상 확인 후 중지' : '조건 확인 후 실행';
      showJobResult(response.result);
    }
    $('#ai-pending').classList.toggle('hidden', !(response.can_apply || response.can_confirm));
    if (editable && editor) showEditorStatus(response);
  }
  function jobLink(parent, id) {
    const open = document.createElement('button');
    open.type = 'button'; open.textContent = '진행 / 결과 보기';
    open.addEventListener('click', async () => {
      open.disabled = true;
      try { await window.MosesBacktestJobs.reconnect(id); }
      catch (error) { add('error', error.message); }
      finally { open.disabled = false; }
    });
    parent.appendChild(open);
  }
  function showJobResult(result) {
    if (!result) return;
    if (result.sequence_id) { watchSequence(result); return; }
    const items = result.items || result.jobs || (result.job_id ? [result] : []);
    if (!items.length) {add('tool', result.message || '저장된 백테스트 작업이 없습니다.'); return;}
    for (const item of items) {
      const card = add('tool', [item.symbol, phaseLabels[item.phase] || item.phase, item.message].filter(Boolean).join(' · '));
      if (item.job_id) jobLink(card, item.job_id);
    }
  }
  function watchSequence(result) {
    const id = result.sequence_id;
    if (sequences.has(id)) return;
    const card = add('tool', ''), state = {card, timer: null, active: true};
    sequences.set(id, state);
    const announcedGeneratedFiles = new Set();
    async function update(value) {
      if (!state.active) return;
      card.textContent = '백테스트 ' + (phaseLabels[value.phase] || value.phase) + (value.message ? '\n' + value.message : '');
      const generatedFilename = value.generated_filename || result.generated_filename;
      if (generatedFilename) {
        card.textContent += '\n생성 전략: ' + generatedFilename;
        if (!announcedGeneratedFiles.has(generatedFilename)) {
          announcedGeneratedFiles.add(generatedFilename);
          window.dispatchEvent(new Event('moses-strategies-changed'));
        }
      }
      for (const [index, item] of (value.jobs || []).entries()) {
        const row = document.createElement('div');
        row.textContent = '\n' + (index + 1) + '. ' + (item.symbol || '') + ' · ' + (phaseLabels[item.phase] || item.phase);
        // Stored Part2 statistics only. Full charts and original files stay in the result dashboard.
        const stats = item.result;
        if (stats) {
          if (stats.alert_statistics?.total !== undefined) row.textContent += '\n총 알림 ' + stats.alert_statistics.total + '건';
          const summaries = Object.entries(stats.by_rr || {});
          if (!summaries.length) for (const value of stats.virtual_entry?.summary || []) summaries.push([value.rr || '', value]);
          for (const [rr, value] of summaries) {
            const line = document.createElement('p');
            line.textContent = [rr ? 'R:R ' + rr : '', value.total_trades !== undefined ? '거래 ' + value.total_trades + '건' : '',
              value.win_rate !== undefined && value.win_rate !== null ? '승률 ' + value.win_rate + '%' : '',
              value.total_r !== undefined && value.total_r !== null ? 'Total R ' + value.total_r : ''].filter(Boolean).join(' · ');
            row.appendChild(line);
          }
          const details = document.createElement('details'), summary = document.createElement('summary');
          summary.textContent = '결과 요약'; details.appendChild(summary);
          const values = document.createElement('pre'); values.textContent = JSON.stringify(stats, null, 2);
          details.appendChild(values); row.appendChild(details);
        }
        if (item.job_id) jobLink(row, item.job_id);
        card.appendChild(row);
      }
      if (value.phase === 'run') {
        const waiting = value.jobs?.at(-1);
        if (waiting?.phase === 'confirm') {
          const note = document.createElement('p'); note.textContent = '데이터 구축 승인이 필요합니다. 진행 / 결과 보기에서 구축 계획을 확인하세요.';
          card.appendChild(note);
        }
        const stop = document.createElement('button'); stop.type = 'button'; stop.textContent = '순차 실행 중지';
        stop.addEventListener('click', async () => {
          if (!window.confirm('현재 백테스트와 이후 예정된 실행을 중지하시겠습니까?')) return;
          stop.disabled = true;
          try {await call('ai/sequence/stop', {sequence_id: id});}
          catch (error) {add('error', error.message);}
          finally {stop.disabled = false;}
        });
        card.appendChild(stop);
        state.timer = setTimeout(async () => {
          try {await update(await call('ai/sequence?id=' + encodeURIComponent(id)));}
          catch (error) {if (state.active) {card.textContent = '작업 조회 실패: ' + error.message; state.active = false;}}
        }, 1500);
      } else state.active = false;
    }
    update(result);
  }
  async function send() {
    if (busy) return;
    const text = $('#ai-text').value.trim();
    if (!text) return;
    if (currentProvider === 'disabled') {
      add('error', 'AI 사용이 꺼져 있습니다. 공통 AI 설정에서 실행 방식을 선택하고 저장하세요.');
      return;
    }
    setBusy(true);
    const requestEpoch = epoch;
    const waiting = currentProvider === 'local_gguf'
      ? add('tool', 'GGUF 응답을 기다리는 중입니다. 첫 요청은 모델 준비 시간이 더 필요할 수 있습니다.') : null;
    waiting?.setAttribute('role', 'status');
    try {
      if (editor) await saveEditor();
      if (requestEpoch !== epoch) return;
      $('#ai-text').value = '';
      add('user', text);
      const response = await call('ai/chat', { session, message: text, research: true, mode: researchMode });
      if (requestEpoch !== epoch) return;
      if ((response.kind || 'STRATEGY') === 'STRATEGY' || response.strategy) window.part3InvalidateAIRecipe();
      await show(response);
    }
    catch (error) { if (requestEpoch === epoch) {
      if (!error.pendingPreserved) {
        revision = null;
        $('#ai-pending').classList.add('hidden');
      }
      if (!error.reported) add('error', error.message);
    } }
    finally { waiting?.remove(); setBusy(false); }
  }
  async function apply() {
    if (busy || $('#ai-pending').classList.contains('hidden')) return;
    setBusy(true);
    const requestEpoch = epoch;
    const generateFromTable = !!editor;
    const generation = editorGeneration;
    try {
      if (editor && !await saveEditor()) return;
      if (requestEpoch !== epoch || generation !== editorGeneration) return;
      $('#ai-pending-text').textContent = kind === 'STRATEGY' ? 'Part3가 Recipe와 생성 코드를 검증하고 있습니다.' : '확인한 백테스트 요청을 전달하고 있습니다.';
      $('#ai-pending').classList.add('hidden');
      const response = await call('ai/apply', { session, revision, research: true });
      if (requestEpoch !== epoch) return;
      if ((response.kind || 'STRATEGY') === 'STRATEGY') {
        if (generateFromTable) {
          const generated = await window.part3GenerateAIRecipe(response.recipe);
          add('tool', '✓ 확인한 표로 전략 파일을 생성했습니다: ' + generated.filename);
        } else {
          await window.part3ApplyAIRecipe(response.recipe);
          add('tool', '✓ Part3가 전략 의도를 검증했습니다. 생성 버튼을 누르기 전에는 파일을 만들지 않습니다.');
        }
      } else {
        showJobResult(response.result);
        window.MosesBacktestJobs?.refresh();
      }
      closeEditor();
    } catch (error) { if (!error.reported) add('error', error.message); }
    finally { setBusy(false); }
  }
  async function refreshProvider() {
    try {
      const response = await call('ai/settings');
      currentProvider = response.settings.provider || 'gemini';
    } catch {}
  }
  async function checkSession() {
    const res = await fetch('/api/ping', { headers: { 'X-Lab-Token': token } }).catch(() => null);
    if (res && res.status !== 403) return;
    const bar = document.createElement('div');
    bar.className = 'session-expired';
    bar.textContent = res ? '이 화면은 이전 실행의 것입니다. START_PART3를 다시 실행하세요.'
                          : 'PART3 서버가 꺼져 있습니다. START_PART3를 다시 실행하세요.';
    document.body.prepend(bar);
  }
  window.addEventListener('part3-ai-settings-changed', () => {
    epoch += 1; revision = null;
    closePresetDialog(true);
    closeEditor();
    $('#ai-pending').classList.add('hidden');
    window.part3InvalidateAIRecipe();
    add('tool', 'AI 설정이 변경되었습니다. 요청을 다시 해석하세요.');
    refreshProvider();
  });
  document.addEventListener('DOMContentLoaded', () => {
    checkSession();
    $('#ai-send').addEventListener('click', send);
    $('#ai-preset-open')?.addEventListener('click', () => openPresets());
    $('#ai-preset-load')?.addEventListener('click', loadPreset);
    $('#ai-preset-close')?.addEventListener('click', () => closePresetDialog());
    $('#ai-preset-cancel')?.addEventListener('click', () => closePresetDialog());
    $('#ai-preset-dialog')?.addEventListener('cancel', (event) => { event.preventDefault(); closePresetDialog(); });
    $('#ai-research-strategy').addEventListener('click', () => selectMode('strategy'));
    $('#ai-research-chat').addEventListener('click', () => selectMode('chat'));
    $('#ai-text').addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) send(); });
    $('#ai-confirm').addEventListener('click', apply);
    $('#ai-cancel').addEventListener('click', async () => {
      if (busy) return;
      setBusy(true);
      try {
        await call('ai/cancel', { session, research: true });
        revision = null;
        closeEditor();
        $('#ai-pending').classList.add('hidden');
        window.part3InvalidateAIRecipe();
        add('tool', '적용을 취소했습니다. 직전 해석은 유지되어 계속 수정할 수 있습니다.');
      } catch (error) { add('error', error.message); }
      finally { setBusy(false); }
    });
    $('#ai-reset').addEventListener('click', async () => {
      if (busy) return;
      setBusy(true);
      try {
        await call('ai/reset', { session, research: true });
        epoch += 1;
        closeEditor();
        for (const state of sequences.values()) {state.active = false; clearTimeout(state.timer);}
        sequences.clear();
        revision = null;
        $('#ai-messages').textContent = '';
        $('#ai-pending').classList.add('hidden');
        $('#ai-pending-text').textContent = '';
        $('#ai-text').value = '';
        $('#ai-mode').textContent = '새 전략 대화';
        window.part3InvalidateAIRecipe();
        $('#ai-text').focus();
      } catch (error) { add('error', error.message); }
      finally { setBusy(false); }
    });
    refreshProvider();
    setInterval(() => { call('ping').catch(() => {}); }, 20000);
    window.addEventListener('pagehide', () => {
      for (const state of sequences.values()) {state.active = false; clearTimeout(state.timer);}
      fetch('/api/bye', { method: 'POST', keepalive: true,
        headers: { 'X-Lab-Token': token, 'Content-Type': 'application/json' }, body: '{}' }).catch(() => {});
    });
  });
})();
