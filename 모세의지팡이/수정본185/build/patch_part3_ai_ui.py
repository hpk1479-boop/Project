"""Add the AI chat tab to Part3/web/index.html (byte-level, keeps existing markup)."""
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'Part3/web/index.html';t=p.read_bytes().decode('utf-8')
a='<button data-action="tab" data-tab="docs">▤ &nbsp; GPT 작업 문서</button></div>'
assert t.count(a)==1
t=t.replace(a,'<button data-action="tab" data-tab="docs">▤ &nbsp; GPT 작업 문서</button><button data-action="tab" data-tab="ai">✦ &nbsp; AI 채팅</button></div>')
b='<div id="tab-docs" class="tab-content hidden">'
assert t.count(b)==1
panel=('<div id="tab-ai" class="tab-content hidden"><section class="panel ai-panel"><h2>✦ &nbsp; AI 채팅 — 말로 전략 만들기 <span id="ai-mode" class="badge">SPECIAL</span></h2>'
 '<p class="muted">AI는 코드를 직접 쓰지 않고 이 프로그램의 기능(불러오기·슬롯 변경·검사·생성·백테스트)을 사용합니다. 파일 생성과 백테스트는 아래 확인 버튼을 눌러야 실행됩니다. "알려줘/감시" 같은 즉석 감시는 WATCH 모드로 처리합니다.</p>'
 '<div id="ai-messages" class="ai-messages" role="log"></div>'
 '<div id="ai-pending" class="ai-pending hidden"><span id="ai-pending-text"></span><button id="ai-confirm" class="primary">확인 · 실행</button><button id="ai-cancel">취소</button></div>'
 '<div class="ai-input"><textarea id="ai-text" rows="3" placeholder="예: 골드 15분 추세 상승이고 1분 브레이커 올존 나오면 알려주는 전략 만들어줘"></textarea><button id="ai-send" class="primary">보내기</button></div>'
 '<details class="ai-settings"><summary>AI 연결 설정</summary><label>모델 제공처<select id="ai-provider"><option value="gemini">Gemini (무료 API)</option><option value="ollama">Ollama (Qwen3-8B 로컬)</option></select></label>'
 '<label>모델 이름<input id="ai-model" placeholder="비우면 기본값"></label><label>API 키 (Gemini만, 이 컴퓨터에만 저장)<input id="ai-key" type="password" autocomplete="off"></label>'
 '<button id="ai-save">설정 저장</button><button id="ai-reset">대화 새로 시작</button><span id="ai-settings-state" class="muted"></span></details></section></div>')
t=t.replace(b,panel+b)
c='<script src="app.js"></script>'
assert t.count(c)==1
t=t.replace(c,c+'<script src="ai_chat.js"></script>')
p.write_bytes(t.encode('utf-8'));print('ok')
