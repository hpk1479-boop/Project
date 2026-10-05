"""Read completed Part2 artifacts without calculating or rewriting results."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path


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

    def analytics(self):
        if not self.paths['analytics']:
            return None, 'missing', '이 실행에는 성과 분석이 없습니다. 알림·거래 원본은 아래에서 확인할 수 있습니다.'
        try:
            data = json.loads(self.artifact('analytics').read_text('utf-8-sig'))
            if not isinstance(data, dict) or not isinstance(data.get('rr_results'), dict):
                raise ValueError('분석 구조 없음')
            return data, 'ready', ''
        except (ValueError, TypeError, OSError):
            return None, 'unavailable', '성과 분석 파일을 읽지 못했습니다. 저장된 결과와 원본 파일을 확인하세요.'

    def trades(self, rr=None, offset=0, limit=100):
        try:
            offset, limit = int(offset), int(limit)
            chosen = None if rr in (None, '') else float(rr)
        except (ValueError, TypeError, OverflowError):
            raise ValueError('거래내역 조회 조건을 확인하세요.') from None
        if offset < 0 or not 1 <= limit <= 200 or (chosen is not None and (not math.isfinite(chosen) or chosen <= 0)):
            raise ValueError('거래내역 조회 조건을 확인하세요.')
        if not self.paths['trades']:
            return {'rows': [], 'offset': offset, 'next_offset': None, 'available': False,
                    'message': '이 실행에는 거래내역 CSV가 없습니다.'}
        rows = []
        matched = 0
        with self.artifact('trades').open(encoding='utf-8-sig', newline='') as handle:
            for row in csv.DictReader(handle):
                if chosen is not None:
                    try:
                        if float(row.get('rr', '')) != chosen:
                            continue
                    except (ValueError, TypeError):
                        continue
                if matched >= offset:
                    rows.append(row)
                    if len(rows) > limit:
                        break
                matched += 1
        return {'rows': rows[:limit], 'offset': offset,
                'next_offset': offset + limit if len(rows) > limit else None,
                'available': True, 'message': ''}
