"""Tabs: 전략 설정 → AI 채팅 → 코드 편집 → 연결 설정. The GPT docs tab button is hidden
(the MD files stay; the AI reads them). The docs panel markup is kept because app.js initializes it."""
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'Part3/web/index.html';t=p.read_bytes().decode('utf-8')
old=('<div class="tabs"><button class="active" data-action="tab" data-tab="builder">⚙ &nbsp; 전략 설정</button>'
     '<button data-action="tab" data-tab="editor">〈/〉 &nbsp; 코드 편집</button>'
     '<button data-action="tab" data-tab="connections">▦ &nbsp; 연결 설정</button>'
     '<button data-action="tab" data-tab="docs">▤ &nbsp; GPT 작업 문서</button>'
     '<button data-action="tab" data-tab="ai">✦ &nbsp; AI 채팅</button></div>')
assert t.count(old)==1
new=('<div class="tabs"><button class="active" data-action="tab" data-tab="builder">⚙ &nbsp; 전략 설정</button>'
     '<button data-action="tab" data-tab="ai">✦ &nbsp; AI 채팅</button>'
     '<button data-action="tab" data-tab="editor">〈/〉 &nbsp; 코드 편집</button>'
     '<button data-action="tab" data-tab="connections">▦ &nbsp; 연결 설정</button></div>')
t=t.replace(old,new)
p.write_bytes(t.encode('utf-8'));print('ok')
