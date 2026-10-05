/* Current shipped HTML/JS in isolated Edge; no engines, user files or AI service. */
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const root = path.resolve(__dirname, '..'), web = path.join(root, 'Part3/web');
const evidence = process.argv[3];
const probeSource = fs.readFileSync(path.join(root,'통합설치/releasekit/validation_probe.py'),'utf8');
const releaseState = probeSource.match(/JS_STATE = """([\s\S]*?)"""/)[1];
const releaseDetail = probeSource.match(/JS_STRATEGY_DETAIL = """([\s\S]*?)"""/)[1];
const checks = [], pass = name => checks.push(name);
(async () => {
  const browser = await chromium.launch({channel:'msedge',headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:1000}}), errors=[], confirmations=[];
  try {
    page.on('pageerror', e => errors.push(e.message));
    page.on('dialog', async d => {confirmations.push(d.message()); await d.accept();});
    await page.route('**/*', route => {
      const name = new URL(route.request().url()).pathname.slice(1) || 'index.html';
      const file = path.join(web, name);
      return fs.existsSync(file) && path.dirname(file) === web ? route.fulfill({path:file}) : route.abort();
    });
    await page.addInitScript(() => {
      let generated=[], number=0;
      const builtins=[{id:'SPECIAL1',name:'기본 전략 하나',example:'기존 전략 하나의 상세 조건',source:'builtin'},
        {id:'SPECIAL2',name:'기본 전략 둘',example:'기존 전략 둘의 상세 조건',source:'builtin'}];
      window.hosts={Part1:['SPECIAL1','SPECIAL2'],Part2:['SPECIAL1','SPECIAL2']};
      window.requests=[]; window.failMutation=false;
      const names=()=>Object.fromEntries([...builtins,...generated].map(x=>[x.id,x.name]));
      const entries=()=>generated.map(x=>({...x,registrations:['Part1','Part2'].filter(p=>hosts[p].includes(x.id)),valid:true}));
      window.fetch=async (url,options={})=>{
        const route=String(url).replace('/api/',''), body=options.body?JSON.parse(options.body):null;
        requests.push({route,body}); let data={ok:true};
        if(route==='ai/presets') data={items:[...builtins,...entries()],revision:'fixture:0'};
        else if(route==='ai/preset/load') {
          const item=[...builtins,...generated].find(x=>x.id===body.preset_id);
          data={kind:'STRATEGY',revision:'fixture:1',can_apply:false,preset:item,example:item.example,editor_contract:{schema:{type:'object'}},
            result:{supported:true,interpretation:{direction:'BOTH',symbols:['XAUUSD+'],steps:[],final:{kind:'OZ',tfs:['1m'],validation_mode:'NORMAL',trigger_mode:'BREAKER'}}}};
        } else if(route==='preview') data={code:'# preview '+body.recipe.name,filename:'Test_SPECIAL001.py'};
        else if(route==='generate') {
          const id='TEST_SPECIAL'+String(++number).padStart(3,'0');
          generated.push({id,filename:'Test_SPECIAL'+String(number).padStart(3,'0')+'.py',name:body.recipe.name,example:'생성한 전략의 상세 조건',source:'generated'});
          data={code:'# generated '+body.recipe.name,filename:generated.at(-1).filename,strategy_id:id};
        } else if(route==='strategies') {
          if(body && window.failMutation) {window.failMutation=false;return {ok:false,json:async()=>({error:'모의 저장 실패'})};}
          if(body?.action==='rename') generated.find(x=>x.id===body.id).name=body.name;
          if(body?.action==='promote') for(const p of ['Part1','Part2']) for(const id of body.ids) if(!hosts[p].includes(id)) hosts[p].push(id);
          if(body?.action==='delete') {generated=generated.filter(x=>!body.ids.includes(x.id)); for(const p of ['Part1','Part2']) hosts[p]=hosts[p].filter(id=>!body.ids.includes(id));}
          data={items:entries(),errors:[]};
        } else if(route==='mo/strategies') {
          hosts[body.part]=body.action==='reset'?['SPECIAL1','SPECIAL2']:hosts[body.part].filter(id=>!body.ids.includes(id));
          data={message:body.action==='reset'?'초기화했습니다.':'선택한 전략을 삭제했습니다.'};
        } else if(route==='mo/live/specials' || route==='mo/backtest/options') {
          const part=route==='mo/live/specials'?'Part1':'Part2';
          const items=Object.fromEntries(hosts[part].map(id=>[id,{name:names()[id],enabled:false,trigger:'일반 올존',default_trigger:'일반 올존',time_filters:null}]));
          const meta={strategy_names:names(),trigger_choices:['일반 올존'],session_labels:{MAIN_ASIA:'아시아'},session_times:{MAIN_ASIA:'0900-1500'},default_time_filters:{}};
          data=part==='Part1'?{...meta,items}:{...meta,specials:hosts.Part2,special_settings:items,default_triggers:Object.fromEntries(hosts.Part2.map(id=>[id,'일반 올존'])),symbol:'XAUUSD+',symbols:['XAUUSD+'],mode:'BAR',start:'2026-09-01',end:'2026-10-01',spread_points:{}};
        } else if(route==='init') data={connections:{warehouse:'fixture'}};
        else if(route==='ai/settings') data={settings:{provider:'gemini',gemini_model:'fixture',gemini_api_key_configured:true}};
        else if(route==='ai/chat') data={kind:'BACKTEST',revision:'fixture:2',can_confirm:true,action:'START',message_ko:'미승급 전략 백테스트 계획'};
        else if(route==='ai/apply') data={kind:'BACKTEST',result:{job_id:'fake-job',phase:'run',message:'백테스트 시작'}};
        else if(route.startsWith('mo/live/status')) data={modules:{STAFF:{name:'STAFF',state:'연결 대기'},ENGINE:{name:'ENGINE',state:'연결 대기'}},lines:[]};
        else if(route.startsWith('mo/backtest/recent')) data={jobs:[],items:[]};
        return {ok:true,status:200,json:async()=>data};
      };
    });
    await page.goto('http://127.0.0.1:8763/');
    for (const mode of ['live','backtest']) {
      await page.evaluate(mode=>moOpenStrategyPopup(mode),mode);
      const selectAll=page.locator('#strategy-list-select-all');
      const toggles=page.locator('#strategy-settings-cards .moses-strategy-toggle input[type=checkbox]');
      assert.ok(await toggles.count()>0);
      assert.equal(await selectAll.innerText(),'전체선택');
      assert.equal(await selectAll.isEnabled(),true);
      await selectAll.click();
      assert.equal(await toggles.evaluateAll(nodes=>nodes.every(node=>node.checked)),true);
      assert.equal(await selectAll.innerText(),'전체해제');
      await toggles.first().uncheck();
      assert.equal(await selectAll.innerText(),'전체선택');
      await selectAll.click();
      assert.equal(await selectAll.innerText(),'전체해제');
      await selectAll.click();
      assert.equal(await toggles.evaluateAll(nodes=>nodes.every(node=>!node.checked)),true);
      assert.equal(await selectAll.innerText(),'전체선택');
      await page.locator('#strategy-settings-cancel').click();
      assert.equal(await page.locator('#strategy-settings-dialog').evaluate(dialog=>dialog.open),false);
    }
    pass('Part1·Part2 전략설정 전체선택·전체해제·개별 체크 문구 동기화·취소 닫기');
    await page.locator('[data-moses-view=strategy]').click();
    assert.equal(await page.locator('#view-strategy .strategy-library').count(),0);
    assert.equal(await page.locator('#view-strategy [data-action=backtest]').count(),0);
    assert.equal(await page.locator('.ai-preview > section').count(),2);
    assert.equal(await page.locator('.ai-preview > section').first().getAttribute('class'),'panel code-panel');
    assert.equal(await page.locator('#ai-preset-open').innerText(),'목록');
    pass('97 코드 미리보기 배치·생성 버튼 유지·별도 목록과 수동 백테스트 버튼 제거');
    await page.locator('#ai-preset-open').click();
    await page.waitForSelector('[data-preset-id=SPECIAL1]');
    assert.equal(await page.locator('#ai-preset-detail').isVisible(),false);
    assert.match(await page.locator('#ai-preset-list').innerText(),/생성한 전략이 없습니다/);
    assert.equal((await page.evaluate(releaseState)).strategy,true);
    await page.locator('[data-preset-id=SPECIAL1]').click();
    assert.equal(await page.evaluate(releaseDetail),true);
    assert.equal(await page.locator('#ai-preset-example').innerText(),'기존 전략 하나의 상세 조건');
    assert.equal(await page.locator('#strategy-library-actions').isVisible(),false);
    await page.locator('#ai-preset-load').click();
    await page.waitForFunction(()=>!document.querySelector('#ai-preset-dialog').open);
    assert.match(await page.locator('#ai-text').inputValue(),/기존 전략 하나/);
    pass('이름 목록 우선 표시·선택 후 상세 표시·기존 스페셜 불러오기');
    await page.evaluate(()=>window.part3ApplyAIRecipe({name:'새 연구 전략',base:'AI',schema_version:2,strategy_intent:{}}));
    await page.locator('#generate-button').click();
    await page.waitForFunction(()=>requests.some(x=>x.route==='generate'));
    await page.locator('#ai-preset-open').click();
    await page.waitForSelector('[data-preset-id=TEST_SPECIAL001]');
    await page.locator('[data-preset-id=TEST_SPECIAL001]').click();
    assert.equal(await page.locator('#strategy-library-actions').isVisible(),true);
    assert.equal(await page.evaluate(()=>hosts.Part1.includes('TEST_SPECIAL001')),false);
    await page.locator('#ai-preset-load').click();
    await page.waitForFunction(()=>!document.querySelector('#ai-preset-dialog').open);
    assert.ok(await page.evaluate(()=>requests.some(x=>x.route==='ai/preset/load'&&x.body.preset_id==='TEST_SPECIAL001')));
    pass('생성한 미승급 전략이 목록에 표시되고 승급 없이 불러오기 가능');
    await page.locator('#ai-preset-open').click();
    await page.locator('[data-preset-id=TEST_SPECIAL001]').click();
    await page.locator('#strategy-library-rename').click();
    await page.locator('#strategy-library-name').fill('이름 변경한 전략');
    await page.locator('#strategy-library-rename-save').click();
    await page.waitForFunction(()=>document.querySelector('[data-preset-id=TEST_SPECIAL001]')?.textContent==='이름 변경한 전략');
    await page.locator('#strategy-library-promote').click();
    await page.waitForFunction(()=>document.querySelector('#strategy-settings-dialog').open && !moStrategyPopup.busy);
    assert.deepEqual(await page.evaluate(()=>['Part1','Part2'].map(p=>hosts[p].includes('TEST_SPECIAL001'))),[true,true]);
    assert.equal(await page.locator('.moses-strategy-delete-toggle').count(),0);
    assert.equal(await page.locator('#strategy-settings-cards [data-strategy=TEST_SPECIAL001] .moses-strategy-card-head input').count(),1);
    await page.locator('[data-strategy=TEST_SPECIAL001] .moses-strategy-toggle input').check();
    await page.locator('#strategy-list-delete').click();
    await page.waitForFunction(()=>!moStrategyPopup.busy && !hosts.Part1.includes('TEST_SPECIAL001'));
    assert.equal(await page.evaluate(()=>hosts.Part2.includes('TEST_SPECIAL001')),true);
    assert.equal(await page.locator('#strategy-list-delete').innerText(),'선택삭제');
    assert.equal(await page.locator('#strategy-list-reset').innerText(),'초기화');
    await page.locator('[data-strategy=SPECIAL2] .moses-strategy-toggle input').check();
    await page.locator('#strategy-list-delete').click();
    await page.waitForFunction(()=>!moStrategyPopup.busy && !hosts.Part1.includes('SPECIAL2'));
    await page.locator('#strategy-list-reset').click();
    await page.waitForFunction(()=>!moStrategyPopup.busy && hosts.Part1.includes('SPECIAL2'));
    assert.equal(await page.evaluate(()=>hosts.Part1.includes('TEST_SPECIAL001')),false);
    pass('이름 변경·양쪽 승급·기본 체크박스 선택삭제·초기화는 기본만 복구');
    await page.evaluate(()=>moCloseStrategyPopup());
    await page.locator('[data-moses-view=strategy]').click();
    await page.locator('#ai-preset-open').click();
    await page.locator('[data-preset-id=TEST_SPECIAL001]').click();
    await page.locator('#strategy-library-delete').click();
    await page.waitForFunction(()=>!document.querySelector('[data-preset-id=TEST_SPECIAL001]'));
    assert.match(confirmations.at(-1),/파트1·파트2 등록과 파트3 전략 원본/);
    assert.equal(await page.evaluate(()=>hosts.Part2.includes('TEST_SPECIAL001')),false);
    assert.match(await page.locator('#ai-preset-list').innerText(),/생성한 전략이 없습니다/);
    pass('파트별 선택삭제는 원본 유지·파트3 삭제는 경고 후 모든 등록 삭제');
    await page.locator('#ai-preset-close').click();
    await page.locator('#ai-text').fill('미승급 전략을 골드로 지난달 BAR 백테스트해 주세요');
    await page.locator('#ai-send').click();
    await page.waitForSelector('#ai-pending:not(.hidden)');
    await page.locator('#ai-confirm').click();
    await page.waitForFunction(()=>requests.some(x=>x.route==='ai/apply'));
    assert.ok(await page.evaluate(()=>requests.some(x=>x.route==='ai/chat')));
    pass('수동 버튼 제거 후 AI 백테스트 요청·확인 실행 경로 유지');
    if(evidence) {
      fs.mkdirSync(evidence,{recursive:true});
      for(const width of [1440,900,600]) {
        await page.setViewportSize({width,height:1000});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
        await page.screenshot({path:path.join(evidence,'preview_'+width+'.png')});
      }
      await page.locator('#ai-preset-open').click();
      await page.waitForSelector('[data-preset-id=SPECIAL1]');
      await page.screenshot({path:path.join(evidence,'list_600.png')});
      await page.locator('[data-preset-id=SPECIAL1]').click();
      await page.screenshot({path:path.join(evidence,'detail_600.png')});
      pass('1440·900·600px 배치 확인·가로 넘침 없음');
    }
    if (!await page.locator('#ai-preset-dialog').evaluate(dialog=>dialog.open)) {
      await page.locator('#ai-preset-open').click();
      await page.waitForSelector('[data-preset-id=SPECIAL1]');
    }
    // Exercise the release's actual JavaScript against shipped HTML, including failures.
    await page.locator('#ai-preset-message').evaluate(node=>node.classList.add('has-error'));
    assert.equal((await page.evaluate(releaseState)).strategy,false,'list API error must block release');
    await page.locator('#ai-preset-message').evaluate(node=>node.classList.remove('has-error'));
    await page.evaluate(()=>{
      window.savedPreview=document.querySelector('#code-preview'); savedPreview.remove();
    });
    assert.equal((await page.evaluate(releaseState)).strategy,false,'missing preview must block release');
    await page.evaluate(()=>document.querySelector('.code-panel').append(savedPreview));
    await page.evaluate(()=>{
      const first=document.querySelector('#ai-preset-list [data-preset-id]');
      window.savedName=first; const clone=first.cloneNode(true); clone.setAttribute('aria-pressed','false');
      first.replaceWith(clone); document.querySelector('#ai-preset-detail').hidden=true;
      document.querySelector('#ai-preset-load').disabled=true;
    });
    assert.equal(await page.evaluate(releaseDetail),false,'missing name click handler must block release');
    await page.evaluate(()=>document.querySelector('#ai-preset-list [data-preset-id]').replaceWith(savedName));
    assert.equal(await page.evaluate(releaseDetail),true);
    await page.evaluate(()=>{
      for(const section of document.querySelectorAll('#ai-preset-list > details')) {
        section.querySelectorAll('button').forEach(node=>node.remove());
        if(!section.querySelector('p')) {const p=document.createElement('p');p.textContent='등록된 스페셜이 없습니다.';section.append(p);}
      }
      document.querySelector('#ai-preset-detail').hidden=true;document.querySelector('#ai-preset-load').disabled=true;
    });
    assert.equal((await page.evaluate(releaseState)).strategy,true,'zero strategies is supported');
    assert.equal(await page.evaluate(releaseDetail),true);
    await page.locator('#ai-preset-cancel').click();
    assert.equal((await page.evaluate(releaseState)).strategy,false,'closed list has not been validated');
    await page.evaluate(()=>{
      const open=document.querySelector('#ai-preset-open');open.replaceWith(open.cloneNode(true));
      document.querySelector('#ai-preset-list').replaceChildren();
      document.querySelector('#ai-preset-open').click();
    });
    assert.equal((await page.evaluate(releaseState)).strategy,false,'missing open handler must block release');
    pass('실제 배포 검증 JS: 현재 목록·상세 통과, 목록 오류·미리보기 누락·클릭 고장 거절, 전략 0개 허용');
    if(evidence) fs.writeFileSync(path.join(evidence,'checks.json'),JSON.stringify({checks,errors},null,2));
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,checks:checks.length}));
  } catch(error) {
    console.error(JSON.stringify({checks,errors,details:await page.evaluate(()=>({message:document.querySelector('#ai-preset-message')?.textContent,library:document.querySelector('#strategy-library-message')?.textContent,log:document.querySelector('#log')?.textContent,requests:requests.slice(-8)}))}));
    throw error;
  } finally {await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
