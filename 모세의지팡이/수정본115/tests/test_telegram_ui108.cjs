/* Real unified.js and settings DOM. No Telegram or other external requests. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.argv[2]);
const root=path.resolve(__dirname,'..'),proof=path.join(root,'검증결과/telegram108');
const html=fs.readFileSync(path.join(root,'Part3/web/index.html'),'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const passed=[],errors=[],pass=name=>passed.push(name);
  try{
    const page=await browser.newPage({viewport:{width:1280,height:1000}});
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',route=>route.request().url()==='http://127.0.0.1:8763/'?
      route.fulfill({contentType:'text/html; charset=utf-8',body:html}):route.abort());
    await page.goto('http://127.0.0.1:8763/');
    await page.addStyleTag({content:fs.readFileSync(path.join(root,'Part3/web/style.css'),'utf8')});
    await page.evaluate(()=>{
      window.config={live:[{key:'SYMBOLS',secret:false,value:'XAUUSD+'},
        ...['TELEGRAM_TOKEN','TELEGRAM_CHAT_ID','TELEGRAM_COMMAND_CHAT_IDS'].map(key=>({key,secret:true,configured:false}))],
        part2:{},connections:{},ai:{provider:'ollama',model:'synthetic:model',timeout:90}};
      window.calls=[];window.telegramDeferred=false;window.pendingReplies=[];window.rejectTelegram=false;
      window.settingsDeferred=false;window.pendingLoads=[];window.setInterval=()=>0;
      window.part3InvalidateAIRecipe=()=>{};
      window.fetch=async()=>({ok:true,json:async()=>({settings:{...config.ai}})});
      window.api=async(name,body)=>{
        calls.push({name,body});
        if(name==='mo/settings/telegram'){
          if(telegramDeferred)return new Promise((resolve,reject)=>pendingReplies.push({resolve,reject,body}));
          if(rejectTelegram)throw Error('입력 '+body.value+'을 확인하지 못했습니다. https://api.telegram.org/bot123:synthetic_secret/getMe');
          const row=config.live.find(row=>row.key===body.key);row.configured=body.value!==null;
          return {ok:true,key:body.key,configured:row.configured,message:body.key==='TELEGRAM_TOKEN'?
            '봇 연결 키 확인 및 저장 완료. 알림/명령 채팅방도 확인해 주세요. 실행 중인 라이브 감시는 재시작 후 적용됩니다.':'채팅방 확인 및 저장 완료. 실행 중인 라이브 감시는 재시작 후 적용됩니다.'};
        }
        if(name==='mo/settings'&&body){
          if(body.group==='live'){for(const row of config.live)if(row.key in body.changes)row.value=body.changes[row.key];}
          else Object.assign(config[body.group],body.changes);
          return {ok:true,message:'저장 완료'};
        }
        if(name==='mo/settings'){
          const snapshot=JSON.parse(JSON.stringify(config));
          if(settingsDeferred)return new Promise(resolve=>pendingLoads.push({resolve,snapshot}));
          return snapshot;
        }
        if(name==='ai/models')return {available:true,models:['synthetic:model']};
        return {modules:{},lines:[]};
      };
      window.resolveTelegram=()=>{const row=pendingReplies.shift();config.live.find(item=>item.key===row.body.key).configured=row.body.value!==null;
        row.resolve({ok:true,key:row.body.key,configured:row.body.value!==null,message:'확인 및 저장 완료. 라이브 재시작 후 적용됩니다.'});};
      window.resolveSettings=()=>{const row=pendingLoads.shift();row.resolve(row.snapshot);};
    });
    for(const file of ['ai_chat.js','unified.js'])await page.addScriptTag({content:fs.readFileSync(path.join(root,'Part3/web',file),'utf8')});
    await page.evaluate(async()=>{document.dispatchEvent(new Event('DOMContentLoaded'));await moView('settings');});
    const token=page.locator('[data-setting="TELEGRAM_TOKEN"]'),chat=page.locator('[data-setting="TELEGRAM_CHAT_ID"]'),command=page.locator('[data-setting="TELEGRAM_COMMAND_CHAT_IDS"]');
    const button=key=>page.locator(`[data-setting="${key}"]`).locator('..').locator('button');
    const status=key=>page.locator(`[data-setting="${key}"]`).locator('../..').locator('.moses-telegram-status');
    const count=()=>page.evaluate(()=>calls.filter(row=>row.name==='mo/settings/telegram').length);
    assert.equal(await button('TELEGRAM_TOKEN').isVisible(),false);
    await token.fill('123:synthetic_token');await token.blur();await chat.fill('-100123');await chat.blur();
    assert.equal(await count(),0);assert.equal(await button('TELEGRAM_TOKEN').isVisible(),true);
    pass('입력·blur는 통신하지 않고 확인 버튼만 나타남');
    const loads=await page.evaluate(()=>calls.filter(row=>row.name==='mo/settings'&&!row.body).length);
    await button('TELEGRAM_TOKEN').click();await page.waitForFunction(()=>document.querySelector('[data-setting="TELEGRAM_TOKEN"]').value==='');
    assert.equal(await token.getAttribute('placeholder'),'설정됨 · 변경할 때만 입력');
    assert.match(await status('TELEGRAM_TOKEN').innerText(),/알림\/명령 채팅방도 확인/);
    assert.equal(await chat.inputValue(),'-100123');assert.equal(await status('TELEGRAM_TOKEN').getAttribute('role'),'status');
    assert.equal(await page.evaluate(()=>calls.filter(row=>row.name==='mo/settings'&&!row.body).length),loads);
    fs.mkdirSync(proof,{recursive:true});
    await page.locator('[data-settings-group="telegram"]').screenshot({path:path.join(proof,'telegram_success.png')});
    pass('키 성공 후 숨김·저장 피드백·수신칸 안내, 다른 입력 유지');
    await chat.press('Enter');await page.waitForFunction(()=>document.querySelector('[data-setting="TELEGRAM_CHAT_ID"]').value==='');
    const chatBody=await page.evaluate(()=>calls.filter(row=>row.name==='mo/settings/telegram').at(-1).body);
    assert.deepEqual(chatBody,{key:'TELEGRAM_CHAT_ID',value:'-100123'});
    assert.equal(await button('TELEGRAM_CHAT_ID').isVisible(),true);
    await button('TELEGRAM_CHAT_ID').click();
    assert.deepEqual(await page.evaluate(()=>calls.filter(row=>row.name==='mo/settings/telegram').at(-1).body),{key:'TELEGRAM_CHAT_ID',value:''});
    pass('Enter로 해당 수신칸만 저장, 저장값은 빈 입력으로 재확인 가능');
    await command.fill('1001, 1002');await button('TELEGRAM_COMMAND_CHAT_IDS').click();
    await page.waitForFunction(()=>document.querySelector('[data-setting="TELEGRAM_COMMAND_CHAT_IDS"]').value==='');
    assert.deepEqual(await page.evaluate(()=>calls.filter(row=>row.name==='mo/settings/telegram').at(-1).body),{key:'TELEGRAM_COMMAND_CHAT_IDS',value:'1001, 1002'});
    pass('명령 채팅방 칸도 독립 확인 및 저장');
    await token.fill('unconfirmed-candidate');await chat.fill('-100456');await page.evaluate(()=>rejectTelegram=true);
    await button('TELEGRAM_CHAT_ID').click();await page.waitForFunction(()=>document.querySelector('[data-setting="TELEGRAM_CHAT_ID"]').closest('label').querySelector('.moses-telegram-status').getAttribute('role')==='alert');
    assert.equal(await chat.inputValue(),'-100456');const message=await status('TELEGRAM_CHAT_ID').innerText();
    assert.ok(!message.includes('-100456')&&!message.includes('synthetic_secret')&&!message.includes('api.telegram.org'));assert.ok(message.includes('확인하지 못했습니다'));
    assert.equal(await status('TELEGRAM_CHAT_ID').evaluate(node=>getComputedStyle(node).color),'rgb(255, 179, 168)');
    await page.locator('[data-settings-group="telegram"]').screenshot({path:path.join(proof,'telegram_error.png')});
    assert.equal(await page.evaluate(()=>calls.filter(row=>row.name==='mo/settings/telegram').at(-1).body.token),'unconfirmed-candidate');
    await page.evaluate(()=>rejectTelegram=false);await token.fill('');
    pass('오류 피드백은 입력 유지·비밀 숨김, 후보 키는 별도 전달');
    await page.evaluate(()=>telegramDeferred=true);await button('TELEGRAM_CHAT_ID').click();
    await page.waitForFunction(()=>pendingReplies.length===1);const pendingCount=await count();
    assert.equal(await button('TELEGRAM_CHAT_ID').isDisabled(),true);await chat.press('Enter');assert.equal(await count(),pendingCount);
    await chat.fill('-100789');await page.evaluate(()=>resolveTelegram());
    await page.waitForFunction(()=>!document.querySelector('[data-setting="TELEGRAM_CHAT_ID"]').parentElement.querySelector('button').disabled);
    assert.equal(await chat.inputValue(),'-100789');
    assert.equal(await status('TELEGRAM_CHAT_ID').innerText(),'');await page.evaluate(()=>telegramDeferred=false);
    await button('TELEGRAM_CHAT_ID').click();await page.waitForFunction(()=>document.querySelector('[data-setting="TELEGRAM_CHAT_ID"]').value==='');
    pass('대기 중 중복 전송 차단, 이전 성공 응답이 새 입력을 지우지 않음');
    await token.fill('pending-key-draft');
    await command.locator('../..').locator('.mo-secret-clear').check();
    await page.locator('[data-setting="SYMBOLS"]').fill('XAUUSD+,EURUSD');const telegramBeforeSave=await count();
    await page.evaluate(async()=>{await moSettingsSave('live');});
    assert.equal(await count(),telegramBeforeSave);
    assert.deepEqual(await page.evaluate(()=>calls.filter(row=>row.name==='mo/settings'&&row.body).at(-1).body),{group:'live',changes:{SYMBOLS:'XAUUSD+,EURUSD'}});
    assert.equal(await token.inputValue(),'pending-key-draft');assert.equal(await command.locator('../..').locator('.mo-secret-clear').isChecked(),true);
    pass('큰 LIVE 저장은 텔레그램 제외, 미확정 입력·삭제 선택 보존');
    await token.fill('');await button('TELEGRAM_COMMAND_CHAT_IDS').click();
    await page.waitForFunction(()=>document.querySelector('[data-setting="TELEGRAM_COMMAND_CHAT_IDS"]').dataset.configured==='false');
    assert.deepEqual(await page.evaluate(()=>calls.filter(row=>row.name==='mo/settings/telegram').at(-1).body),{key:'TELEGRAM_COMMAND_CHAT_IDS',value:null});
    assert.equal(await command.getAttribute('placeholder'),'미설정');assert.equal(await button('TELEGRAM_COMMAND_CHAT_IDS').isVisible(),false);
    pass('저장값 삭제도 해당 칸 확인으로 처리');
    await page.evaluate(()=>{settingsDeferred=true;window.loadPromise=moSettingsLoad();});
    await page.waitForFunction(()=>pendingLoads.length===1);await token.fill('new-key');await button('TELEGRAM_TOKEN').click();
    await page.waitForFunction(()=>document.querySelector('[data-setting="TELEGRAM_TOKEN"]').value==='');
    const savedMessage=await status('TELEGRAM_TOKEN').innerText();await chat.fill('new-chat-draft');
    await page.evaluate(async()=>{resolveSettings();await loadPromise;settingsDeferred=false;});
    assert.equal(await status('TELEGRAM_TOKEN').innerText(),savedMessage);assert.equal(await chat.inputValue(),'new-chat-draft');
    pass('저장 이전에 시작한 설정 조회 응답을 폐기');
    await page.evaluate(()=>telegramDeferred=true);await command.fill('1003');await button('TELEGRAM_COMMAND_CHAT_IDS').click();
    await page.waitForFunction(()=>pendingReplies.length===1);await page.evaluate(async()=>{await moSettingsLoad();});
    assert.equal(await command.inputValue(),'1003');assert.equal(await button('TELEGRAM_COMMAND_CHAT_IDS').isDisabled(),true);
    await command.fill('1004');await page.evaluate(()=>resolveTelegram());
    await page.waitForFunction(()=>!document.querySelector('[data-setting="TELEGRAM_COMMAND_CHAT_IDS"]').parentElement.querySelector('button').disabled);
    assert.equal(await command.inputValue(),'1004');await page.evaluate(()=>telegramDeferred=false);
    await button('TELEGRAM_COMMAND_CHAT_IDS').click();await page.waitForFunction(()=>document.querySelector('[data-setting="TELEGRAM_COMMAND_CHAT_IDS"]').value==='');
    pass('재렌더 후에도 중복 차단, 이전 필드 응답이 새 필드를 덮지 않음');
    const aiInput=page.locator('[data-setting="timeout"]');await aiInput.fill('120');
    await page.evaluate(async()=>{await moSettingsSave('ai');});
    assert.deepEqual(await page.evaluate(()=>calls.filter(row=>row.name==='mo/settings'&&row.body).at(-1).body),{group:'ai',changes:{timeout:120}});
    assert.equal(await token.getAttribute('type'),'password');
    pass('다른 설정 큰 저장과 비밀 입력 방식 유지');
    fs.mkdirSync(proof,{recursive:true});await chat.fill('recheck-draft');
    await page.locator('[data-settings-group="telegram"]').screenshot({path:path.join(proof,'telegram_wide.png')});
    await page.setViewportSize({width:640,height:1000});
    await page.locator('[data-settings-group="telegram"]').screenshot({path:path.join(proof,'telegram_narrow.png')});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    pass('기존 3칸 배치 유지, 좁은 화면에서 가로 넘침 없음');
    const report={passed:passed.length,checks:passed,errors};fs.writeFileSync(path.join(proof,'ui_report.json'),JSON.stringify(report,null,2));
    assert.deepEqual(errors,[]);console.log(JSON.stringify(report));
  }finally{await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
