from pathlib import Path
from pypdf import PdfReader
import json
import re

ROOT = Path(__file__).resolve().parents[4]
previous = PdfReader(ROOT.parent / '수정본112' / '매뉴얼' / '모세_사용자_매뉴얼.pdf')
current = PdfReader(ROOT / '매뉴얼' / '모세_사용자_매뉴얼.pdf')
assert len(previous.pages) == len(current.pages) == 25
changed = []
for index, (before, after) in enumerate(zip(previous.pages, current.pages), 1):
    normalize = lambda text: re.sub(r'수정본\d+', '수정본', text)
    if normalize(before.extract_text()) != normalize(after.extract_text()):
        changed.append(index)
assert changed == [16, 17, 19], changed
report_path = Path(__file__).parent / 'pdf_checks.json'
report = json.loads(report_path.read_text('utf-8'))
report.update(visual_review='passed: rendered pages 16, 17, 19',
              body_changed_pages=changed, other_chapters_unchanged=True)
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'body_changed_pages': changed, 'other_chapters_unchanged': True}))
