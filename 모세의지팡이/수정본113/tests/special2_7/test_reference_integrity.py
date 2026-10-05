"""Definition equality, not a call index: the current Part1 is the only source."""
from pathlib import Path
import ast,copy
import pytest
ROOT=Path(__file__).resolve().parents[2]
REF=ROOT/'Part2/live_replay/reference'

@pytest.mark.parametrize('number',range(1,8))
def test_special_decisions_are_the_current_part1_file(number):
    """No Part2 copy: LIVE_REPLAY runs Part1/program/SPECIAL/SPECIAL<n>.py itself."""
    import hashlib
    from live_replay import reference
    module=getattr(reference,f'special{number}')
    part1=ROOT/f'Part1/program/SPECIAL/SPECIAL{number}.py'
    assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()==hashlib.sha256(part1.read_bytes()).hexdigest()
    assert reference.SOURCE_HASHES[f'Part1/program/SPECIAL/SPECIAL{number}.py']==hashlib.sha256(part1.read_bytes()).hexdigest()
    assert not (REF/f'special{number}.py').exists()
