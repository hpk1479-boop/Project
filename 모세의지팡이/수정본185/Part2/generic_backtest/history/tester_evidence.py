"""Diagnose one launch from newly appended selected-terminal Tester journals.

A successful empty history query is not proof of a closed market. Only the
Tester's explicit, symbol-scoped available-range report can bound a missing
tail. These bounds describe this terminal's reported history, not a guarantee
that the broker has no newer history elsewhere. There are no weekday guesses,
terminal/market mutations or history API calls in this module.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re


_STAMP = r'(\d{4}[.]\d{2}[.]\d{2}\s+\d{2}:\d{2}(?::\d{2})?)'
_DOWNLOAD_FAILURE = re.compile(
    r'(?:download|synchron\w*|connect\w*|history).*(?:fail\w*|error|timeout|timed out|cannot|unavailable)'
    r'|(?:fail\w*|error|timeout|timed out|cannot).*(?:download|synchron\w*|connect\w*|history)',
    re.I)


class NativeHistoryUnavailable(ValueError):
    """No native export; available_end, when proven, is an exclusive UTC day."""
    def __init__(self, symbol, start_ns, end_ns, *, available_start=None,
                 available_end=None, evidence=()):
        self.symbol = symbol
        self.start_ns, self.end_ns = int(start_ns), int(end_ns)
        self.start, self.end = _request_dates(start_ns, end_ns)
        self.available_start, self.available_end = available_start, available_end
        self.evidence = tuple(evidence)
        super().__init__('NATIVE_TESTER_HISTORY_UNAVAILABLE: ' + symbol + ' ' +
            self.start + '~' + self.end +
            ' 구간에 MT5가 제공하는 이력이 없습니다. 완료된 조각은 보존됩니다.')


def _request_dates(start_ns, end_ns):
    first = datetime.fromtimestamp(int(start_ns) / 10**9, timezone.utc).date()
    day_ns = 86400 * 10**9
    last = datetime.fromtimestamp(((int(end_ns) + day_ns - 1) // day_ns) * 86400,
                                  timezone.utc).date()
    return first.isoformat(), last.isoformat()


def _journals(profile):
    # Global tester-agent journals may belong to other running terminals.
    root = profile.get('data_root')
    if not root:
        return ()
    return sorted((Path(root) / 'Tester' / 'logs').glob('*.log'))


def journal_positions(profile):
    result = {}
    for path in _journals(profile):
        try:
            result[str(path)] = path.stat().st_size
        except OSError:
            pass  # Unreadable evidence must never approve a shorter period.
    return result


def fresh_lines(profile, before):
    result = []
    for path in _journals(profile):
        try:
            offset = before.get(str(path), 0)
            if path.stat().st_size <= offset:
                continue  # A truncated/replaced old journal is not fresh proof.
            with path.open('rb') as stream:
                stream.seek(offset)
                raw = stream.read()
            if b'\x00' in raw[:200] or raw.startswith((b'\xff\xfe', b'\xfe\xff')):
                encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-16-le'
            else:
                encoding = 'utf-8-sig'
            result.extend(raw.decode(encoding, errors='replace').splitlines())
        except OSError:
            pass
    return result


def _stamp(text):
    return datetime.strptime(text, '%Y.%m.%d %H:%M:%S' if len(text) == 19
                              else '%Y.%m.%d %H:%M').replace(tzinfo=timezone.utc)


def history_unavailable(lines, symbol, start_ns, end_ns, *, today=None):
    """Return a dedicated error; provide tail bounds only for exact fresh proof.

The caller supplies only bytes appended after this launch began. A range-free
no-history message can diagnose history absence, but cannot shorten a request.
Download/synchronization failures and mismatched request ranges also withhold
bounds, so callers retain their normal missing-history failure behavior.
"""
    messages = [line.rsplit('\t', 1)[-1].strip() for line in lines]
    prefix = re.escape(symbol) + r'\s*:\s*'
    missing = re.compile(r'^' + prefix + r'no history data(?:\s+from\s+' + _STAMP +
                         r'\s+to\s+' + _STAMP + r')?(?:[.,\s].*)?$', re.I)
    bounds = re.compile(r'^' + prefix + r'found history data from\s+' + _STAMP +
                        r'\s+to\s+' + _STAMP + r',\s*specified period is out of this range\s*$', re.I)
    requested_start, requested_end = _request_dates(start_ns, end_ns)
    own_missing = []
    for index, message in enumerate(messages):
        match = missing.fullmatch(message)
        if not match:
            continue
        if match.group(1) and match.group(2):
            try:
                first, last = map(_stamp, match.groups())
            except ValueError:
                continue
            if (first.date().isoformat(), last.date().isoformat()) != (requested_start, requested_end):
                continue  # Another request's diagnosis is not this launch's proof.
        own_missing.append((index, message, match))
    if not own_missing:
        return None
    evidence = [message for _, message, _ in own_missing]
    error = NativeHistoryUnavailable(symbol, start_ns, end_ns, evidence=evidence)
    # Treat any fresh history-connection failure as an incomplete acquisition,
    # even if its line lacks a symbol. Never transform that into a normal tail.
    if any(_DOWNLOAD_FAILURE.search(message) for message in messages):
        return error
    limit = today or datetime.now(timezone.utc).date()
    for index, message, match in own_missing:
        if not match.group(1) or not match.group(2):
            continue
        try:
            first, last = map(_stamp, match.groups())
        except ValueError:
            continue
        if (first.date().isoformat(), last.date().isoformat()) != (requested_start, requested_end):
            continue
        if first.time().replace(tzinfo=None) != datetime.min.time() or last.time().replace(tzinfo=None) != datetime.min.time():
            continue
        # The adjacent available-range report must precede this no-history
        # diagnosis, never come from a later launch or another symbol.
        previous = next((candidate for candidate in reversed(messages[:index])
                         if bounds.fullmatch(candidate)), None)
        if previous is None:
            continue
        try:
            available_first, available_last = map(_stamp, bounds.fullmatch(previous).groups())
        except ValueError:
            continue
        if available_first > available_last:
            continue
        # Tester reports the last stored day's date (usually its D1 open).
        # Our request boundary excludes its end, so retain that whole last day.
        available_end = min(available_last.date() + timedelta(days=1), limit)
        error.available_start = available_first.date().isoformat()
        error.available_end = available_end.isoformat()
        error.evidence = (previous, message)
        return error
    return error
