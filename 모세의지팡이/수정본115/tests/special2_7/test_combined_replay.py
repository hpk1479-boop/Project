"""Shared SPECIAL registration and symbol isolation, using current Part1 decisions."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'Part2'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pytest
from live_replay import synthetic,synthetic_specials
from live_replay.runtime import Replay
from oracle import original_replay,assert_decision_state_equal

@pytest.mark.parametrize('number',[5,7])
def test_all_seven_share_original_state_and_nonempty_event_order(number):
    actual=Replay(specials=tuple(range(1,8)),synthetic=True)
    expected=original_replay(specials=tuple(range(1,8)))
    for row in getattr(synthetic_specials,f'special{number}_trace')():
        actual.step(row);expected.step(row);assert_decision_state_equal(actual,expected)
    assert actual.delivered and any(x['strategy']==f'SPECIAL{number}' for x in actual.delivered)
    assert [x['event_order'] for x in actual.delivered]==list(range(1,len(actual.delivered)+1))

@pytest.mark.parametrize('number',range(1,8))
def test_nas100_positive_and_no_cross_symbol_state(number,monkeypatch):
    monkeypatch.setattr(synthetic,'SYMBOL','NAS100')
    monkeypatch.setattr(synthetic,'ORDER',{'NAS100':['1h','2h','3h','4h']})
    monkeypatch.setattr(synthetic_specials,'SYMBOL','NAS100')
    rows=(synthetic.synthetic_trace(two_sessions=False) if number==1 else
          getattr(synthetic_specials,f'special{number}_trace')())
    actual=Replay(specials=(number,),synthetic=True);expected=original_replay(specials=(number,))
    for row in rows:actual.step(row);expected.step(row);assert_decision_state_equal(actual,expected)
    assert len(actual.delivered)==1 and actual.delivered[0]['symbol']=='NAS100'
    assert actual.delivered[0]['strategy']==f'SPECIAL{number}'
    assert actual.oz_staff['NAS100'] is not actual.oz_staff['XAUUSD+']
