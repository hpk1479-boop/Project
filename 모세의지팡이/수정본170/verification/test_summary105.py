"""Natural summaries preserve the canonical intent and runtime inheritance rules."""
from pathlib import Path
import json
import shutil
import subprocess


def test_summary_explains_branches_and_lifecycle_without_changing_intent():
    root = Path(__file__).resolve().parents[1]
    node = shutil.which('node')
    assert node, 'Part3 JS 검증에 필요한 Node.js를 찾을 수 없습니다.'
    result = subprocess.run(
        [node, str(root / 'verification/summary105_explain_check.cjs'),
         str(root / 'Part3/web/ai_display.js')],
        cwd=root, text=True, encoding='utf-8', capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report['checks'] == report['passed'] == 11
    assert not report['failures']
