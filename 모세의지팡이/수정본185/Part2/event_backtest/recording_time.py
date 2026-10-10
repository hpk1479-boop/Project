"""The broker clock of recordings (수정본172).

A recording keeps the broker's server clock it was made in: 'server_time' in its catalog entry, the
setting of config.txt when it was recorded. Recordings made before 수정본172 have none; they came from
the default broker (server_time.DEFAULT). A replay converts the recorded times with this clock, the
same conversion LIVE applies to the bars it receives.
"""
import sys

from .settings import PROGRAM


def _rule():
    if str(PROGRAM) not in sys.path:
        sys.path.insert(0, str(PROGRAM))
    import server_time
    return server_time


def recording_clock(captures):
    """The one broker clock of a run's recordings."""
    rule = _rule()
    clocks = {rule.ServerTime.from_record(c.get('server_time')) for c in captures}
    if len(clocks) > 1:
        raise ValueError('녹화마다 브로커 서버 시간이 달라 한 번에 재생할 수 없습니다.')
    return next(iter(clocks)) if clocks else rule.DEFAULT


def clocks_of(captures):
    """Each recording's broker clock, in the order given."""
    rule = _rule()
    return [rule.ServerTime.from_record(c.get('server_time')) for c in captures]


def current_setting():
    """The broker clock config.txt names now, recorded with a new recording."""
    rule = _rule()
    from event_composer_domain import load_config
    return rule.ServerTime.from_config(load_config(str(PROGRAM / 'config.txt')))
