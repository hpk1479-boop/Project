"""The offline verifier must not discover, start or contact working-project AI."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'verification'))
from current_worker import block_workspace_ai


@pytest.mark.parametrize('method', ['_read_record', '_start', '_request'])
def test_workspace_ai_blocked_before_any_side_effect(tmp_path, method):
    calls = []
    class Client:
        def __init__(self, root):self.root = root
        def _read_record(self):calls.append('endpoint');return 'record'
        def _start(self):calls.append('start');return 'service'
        def _request(self):calls.append('send');return 'reply'
    project = tmp_path / 'workspace'
    block_workspace_ai(project, Client)
    with pytest.raises(AssertionError, match='작업본 AI 서비스'):
        getattr(Client(project), method)()
    assert calls == []
    assert getattr(Client(tmp_path / 'isolated-test'), method)() in ('record', 'service', 'reply')
    assert len(calls) == 1


def test_minimal_verifier_fixture_without_ai_stays_supported(tmp_path):
    block_workspace_ai(tmp_path)
