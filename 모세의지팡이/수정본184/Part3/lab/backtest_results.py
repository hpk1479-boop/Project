"""Read completed Part2 artifacts without calculating or rewriting results."""
from __future__ import annotations

from collections import OrderedDict
import csv
import json
import math
from pathlib import Path
import threading

_TRADES = OrderedDict()   # (path, mtime, size) -> Part2 result_analysis.Trades, the last opened runs
_TRADES_LOCK = threading.Lock()
_KEEP = 2
_SCANS = OrderedDict()    # (path, mtime, size, RR, period window) -> _Scan, the last paged selections
_SCANS_LOCK = threading.Lock()
_KEEP_SCANS = 4


def _wanted(row, chosen, window):
    """A trade row of the RR and period asked for (a row that cannot be read is left out)."""
    if chosen is not None:
        try:
            if float(row.get('rr', '')) != chosen:
                return False
        except (ValueError, TypeError):
            return False
    if window is not None:
        try:
            if not window[0] <= int(row.get('alert_time', '')) < window[1]:
                return False
        except (ValueError, TypeError):
            return False
    return True


class _Scan:
    """The rows of one RR and period, found as far as the pages have read (수정본170).

    A page continues the reading where the last one stopped instead of reading the file from its start
    again; the rows found are kept as their positions in the file. A row is one line of the CSV; a
    file with a quoted line break inside a row is read the old way (_scan returns None).
    """

    def __init__(self, path, chosen, window):
        self.path, self.chosen, self.window = path, chosen, window
        self.lock = threading.Lock()
        with path.open('rb') as handle:
            first = handle.readline()
        self.header = next(csv.reader([first.decode('utf-8-sig')]), [])
        self.position, self.found, self.ended, self.plain = len(first), [], False, True

    def _row(self, line):
        """The row csv.DictReader gives for one line, or None for an empty line."""
        text = line.decode('utf-8')
        if text.count('"') % 2:
            self.plain = False       # a quoted line break: this file is read from its start
            return None
        values = next(csv.reader([text]), [])
        if not values:
            return None
        row = dict(zip(self.header, values))
        if len(values) > len(self.header):
            row[None] = values[len(self.header):]
        for key in self.header[len(values):]:
            row[key] = None
        return row

    def reach(self, count):
        with self.lock:
            if self.ended or len(self.found) >= count:
                return
            with self.path.open('rb') as handle:
                handle.seek(self.position)
                while len(self.found) < count and self.plain:
                    start = self.position
                    line = handle.readline()
                    if not line:
                        self.ended = True
                        break
                    self.position += len(line)
                    row = self._row(line)
                    if row is not None and _wanted(row, self.chosen, self.window):
                        self.found.append(start)

    def page(self, offset, limit):
        """Up to limit + 1 rows from the offset (the extra one tells that another page follows)."""
        self.reach(offset + limit + 1)
        if not self.plain:
            return None
        rows = []
        with self.path.open('rb') as handle:
            for position in self.found[offset:offset + limit + 1]:
                handle.seek(position)
                rows.append(self._row(handle.readline()))
        return rows


def _scan(path, chosen, window):
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size, chosen, window)
    with _SCANS_LOCK:
        if key not in _SCANS:
            _SCANS[key] = _Scan(path, chosen, window)
            while len(_SCANS) > _KEEP_SCANS:
                _SCANS.popitem(last=False)
        _SCANS.move_to_end(key)
        return _SCANS[key]


def _trades(path):
    """A run's parsed trade rows, kept so that switching the period or the RR stays instant (수정본165)."""
    from event_backtest.result_analysis import load_trades
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _TRADES_LOCK:
        if key in _TRADES:
            _TRADES.move_to_end(key)
            return _TRADES[key]
    trades = load_trades(path)
    with _TRADES_LOCK:
        _TRADES[key] = trades
        while len(_TRADES) > _KEEP:
            _TRADES.popitem(last=False)
    return trades


class ResultFiles:
    def __init__(self, warehouse, folder, resolve):
        self.warehouse = Path(warehouse).resolve()
        self.folder = Path(folder).resolve()
        self.resolve = resolve
        self.path = self.folder / 'result.json'
        if not self.path.is_file():
            raise ValueError('결과 파일이 아직 없습니다.')
        self.data = json.loads(self.path.read_text('utf-8-sig'))
        if not isinstance(self.data, dict):
            raise ValueError('결과 JSON 형식을 확인하세요.')
        virtual = self.data.get('virtual_entry') or {}
        if not isinstance(virtual, dict):
            virtual = {}
        self.paths = {
            'result': self.path.relative_to(self.warehouse).as_posix(),
            'analytics': self.data.get('analytics_path'),
            'alerts': self.data.get('alerts_csv'),
            'summary': virtual.get('summary_csv') or self.data.get('summary_csv'),
            'trades': virtual.get('trades_csv') or self.data.get('trades_csv'),
        }

    def artifact(self, kind):
        if kind not in self.paths or not self.paths[kind]:
            raise ValueError('이 실행에는 요청한 원본 파일이 없습니다.')
        path = self.resolve(self.warehouse, self.paths[kind])
        if not path.is_file():
            raise ValueError('원본 파일을 찾을 수 없습니다. 창고 위치를 확인하세요.')
        return path

    def describe(self):
        files = []
        for kind, relative in self.paths.items():
            if not relative:
                continue
            try:
                path = self.resolve(self.warehouse, relative)
                files.append({'kind': kind, 'path': path.relative_to(self.warehouse).as_posix(),
                              'available': path.is_file()})
            except (ValueError, TypeError, OSError):
                # Invalid paths are neither displayed nor accepted by downloads.
                continue
        return files

    def analysis(self, period='all', rr=None, table=False):
        """The result screen for one period and RR, from the run's trade rows (수정본165).

        The whole analytics.json (curves with every trade) is no longer sent; it stays an original file.
        table=True: the period's RR table alone, for the screen to pick the RR it shows (수정본170).
        """
        if not self.paths['trades']:
            return None, 'missing', '이 실행에는 거래내역이 없어 성과 분석을 표시하지 않습니다. 원본 파일은 아래에서 확인할 수 있습니다.'
        try:
            trades = _trades(self.artifact('trades'))
        except (ValueError, TypeError, OSError, csv.Error):
            return None, 'unavailable', '거래내역을 읽지 못해 성과 분석을 표시하지 않습니다. 원본 파일을 확인하세요.'
        from event_backtest.result_analysis import table_view, view
        return (table_view(trades, period) if table else view(trades, period, rr)), 'ready', ''

    def trades(self, rr=None, offset=0, limit=100, period=None):
        try:
            offset, limit = int(offset), int(limit)
            chosen = None if rr in (None, '') else float(rr)
        except (ValueError, TypeError, OverflowError):
            raise ValueError('거래내역 조회 조건을 확인하세요.') from None
        if offset < 0 or not 1 <= limit <= 200 or (chosen is not None and (not math.isfinite(chosen) or chosen <= 0)):
            raise ValueError('거래내역 조회 조건을 확인하세요.')
        from event_backtest.result_analysis import period_window
        window = period_window(period)
        if not self.paths['trades']:
            return {'rows': [], 'offset': offset, 'next_offset': None, 'available': False,
                    'message': '이 실행에는 거래내역 CSV가 없습니다.'}
        path = self.artifact('trades')
        rows = _scan(path, chosen, window).page(offset, limit)
        if rows is None:
            rows = []
            matched = 0
            with path.open(encoding='utf-8-sig', newline='') as handle:
                for row in csv.DictReader(handle):
                    if not _wanted(row, chosen, window):
                        continue
                    if matched >= offset:
                        rows.append(row)
                        if len(rows) > limit:
                            break
                    matched += 1
        return {'rows': rows[:limit], 'offset': offset,
                'next_offset': offset + limit if len(rows) > limit else None,
                'available': True, 'message': ''}
