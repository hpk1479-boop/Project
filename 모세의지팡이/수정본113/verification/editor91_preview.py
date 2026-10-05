"""Create a local, nonexecuting preview using the shipped editor and schema."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))
from lab.ai.schema import output_schema, validate_intent
from lab.ai.research_editor import contract


def build():
    schema = output_schema()
    symbols = schema['properties']['interpretation']['anyOf'][0]['properties']['symbols']['items']['enum']
    result = {'supported': True, 'intent': 'CREATE_STRATEGY',
        'interpretation': {'direction': 'LONG', 'symbols': [symbols[0]],
            'steps': [{'kind': 'TREND', 'tfs': ['15m'], 'direction': 'LONG'},
                {'kind': 'WONBI_TOUCH', 'tfs': ['3m'], 'side': 'LOWER'}],
            'order_mode': 'SIMULTANEOUS', 'final': {'kind': 'OZ', 'tfs': ['1m'],
                'direction': 'LONG', 'validation_mode': 'NORMAL', 'trigger_mode': 'BREAKER'}},
        'needs_clarification': False, 'clarification_question': None,
        'message_ko': '15분 상승추세에서 3분 하단 원비 터치 후 1분 브레이커 올존'}
    validate_intent(result)
    payload = {'response': {'kind': 'STRATEGY', 'result': result, 'can_apply': True},
        'contract': contract({'today': '2026-10-03', 'options': {'symbols': symbols}})}
    data = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c')
    html = '''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>전략 해석표 확인</title><link rel="stylesheet" href="/Part3/web/style.css">
<link rel="stylesheet" href="/Part3/web/ai_editor.css">
<style>body{padding:22px;max-width:1250px;margin:auto}#status{padding:12px;font-size:13px}</style>
</head><body><h2>AI 전략연구 · 전략 해석표</h2><div id="editor"></div>
<p id="status">확인용 화면입니다. 파일 생성·백테스트 실행은 연결되지 않았습니다.</p>
<script id="fixture" type="application/json">''' + data + '''</script>
<script src="/Part3/web/ai_editor.js"></script><script>
const fixture=JSON.parse(document.getElementById('fixture').textContent);
window.previewEditor=part3IntentEditor.create({...fixture,onChange:()=>{
 document.getElementById('status').textContent='수정한 값이 해석표에 반영되었습니다. 실행은 하지 않습니다.';
}});document.getElementById('editor').appendChild(previewEditor.element);
</script></body></html>'''
    target = ROOT / '검증결과/editor91_preview.html'
    target.parent.mkdir(exist_ok=True)
    target.write_text(html, encoding='utf-8')
    print(target.relative_to(ROOT).as_posix())


if __name__ == '__main__':
    build()
