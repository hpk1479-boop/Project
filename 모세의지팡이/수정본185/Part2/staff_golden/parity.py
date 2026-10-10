"""Three-way original LIVE/current LIVE/current capture replay on fixed inputs."""
from __future__ import annotations

import contextlib
import hashlib
import itertools
import json
import logging
import time
import uuid
from pathlib import Path
from unittest.mock import patch

from part1_host import capture, engine, runtime
from . import baseline_host
from part1_host.synthetic import TIMEFRAMES
from .contracts import json_bytes, normalize
from .record import metric_summary, timed
from .scenario import START, SYMBOL, WATCHES, WINDOW, market


def plain(value):
    return json.loads(json_bytes(value))


def run(data, part1, feed, specials, window):
    counter = itertools.count(1)
    previous_logging = logging.root.manager.disable
    metrics = {}
    # Offline identity source only. Keep all event IDs/references in comparisons.
    with patch.object(uuid, 'uuid4', side_effect=lambda: uuid.UUID(int=next(counter))):
        rt = baseline_host.Part1Runtime(symbols=[SYMBOL], start_epoch=START - 1,
                                  specials=specials, trigger_overrides={'SPECIAL7': '무지성 올존'},
                                  part1_root=Path(part1), log_level=logging.CRITICAL)
        logging.disable(logging.CRITICAL)
        try:
            manager_events = []
            handler = rt._manager_handle

            def spy(request):
                reply = handler(request)
                manager_events.append(plain({'time': rt.clock.time(), 'request': request, 'reply': reply}))
                return reply

            rt.bus.handlers[rt.manager.alert_endpoint] = spy
            staff_handler = rt.staff_server.handle
            rt.staff_server.handle = lambda request: timed(metrics, 'staff_request', staff_handler, request)
            for chat, command in WATCHES:
                rt.manager.handle_command(command, chat)
            rt.start_services()
            sequence = 0
            start_wall, start_cpu = time.perf_counter(), time.process_time()
            for offset in range(window):
                second = START + offset
                rt.clock.set(second)
                if feed == 'live':
                    wires = []
                    for tf in TIMEFRAMES:
                        sequence += 1
                        wires.append((tf, capture.pack_wire(SYMBOL, tf, *data.payload(tf, second), snapshot=sequence)))
                else:
                    feed.advance_to(second)
                    wires = feed.wires()
                for tf, wire in wires:
                    timed(metrics, 'staff_parser', rt.publish, wire)
                rt.run_until(second + 0.999)
                if offset % 60 == 59:
                    print(f'parity {"live" if feed == "live" else "backtest"}: {offset + 1}/{window}s', flush=True)
            wall, cpu = time.perf_counter() - start_wall, time.process_time() - start_cpu
            oz = rt.modules['monitor_OZ']
            monitors = rt.oz_monitors[SYMBOL]
            fvg = rt.engines['FVG'].engine
            # Full persisted Watch/Composer state complements the complete manager
            # event stream and Telegram outputs. Exclude logs and infrastructure files.
            watch_state = {}
            for path in sorted((rt.program / 'logs').glob('*.json')):
                if any(token in path.name for token in ('watch', 'composer', 'chain')):
                    watch_state[path.name] = json.loads(path.read_text('utf-8'))
            evidence = {
                'finals': [a.to_json() for a in rt.alerts],
                'special': [a.to_json() for a in rt.special_alerts()],
                'telegram': [(d['virtual_time'], str(d['data'].get('chat_id')),
                              str(d['data'].get('text', ''))) for d in rt.http.deliveries],
                'oz_state': {f'{m.validation_mode}/{m.trigger_mode}':
                             {name: oz._observed_encode(getattr(m, name)) for name in m._checkpoint_fields}
                             for m in monitors},
                'fvg_state': {'active': {'|'.join(k): v for k, v in fvg._active_zones.items()},
                              'touch': fvg._touch_state, 'seen': sorted(fvg._seen_created),
                              'closed': {'|'.join(k): v for k, v in fvg._last_closed_time.items()}},
                'manager_events': manager_events,
                'watch_state': watch_state,
                'source_health': rt._staff_handle({'kind': 'SOURCE_HEALTH', 'symbol': SYMBOL,
                                                  'timeframes': list(TIMEFRAMES)}),
            }
            facts = monitors[0]._oz_facts
            return {'evidence': plain(evidence), 'metrics': {'wall_s': wall, 'cpu_s': cpu,
                    'staff': metric_summary(metrics), 'oz_facts_computed': facts.computed,
                    'oz_facts_reused': facts.reused}, 'source_sha256': rt.source_hashes,
                    'loaded_specials': rt.loaded_specials}
        finally:
            rt.close()
            logging.disable(previous_logging)


def three_way(before, after, out, *, specials=('SPECIAL7',), window=WINDOW):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    data = market()
    folder = out / 'synthetic_capture'
    writer = capture.CaptureWriter(folder, SYMBOL, TIMEFRAMES)
    for second in range(START, START + window):
        for index, tf in enumerate(TIMEFRAMES):
            writer.write(index, second, *data.payload(tf, second))
    writer.close()
    records = {}
    for name, part1, feed in (('before_live', before, 'live'), ('after_live', after, 'live'),
                              ('after_backtest', after, engine.SecondFeed(folder, SYMBOL))):
        print('START ' + name, flush=True)
        result = run(data, part1, feed, list(specials), window)
        (out / (name + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        records[name] = result
    reference = records['before_live']['evidence']
    fields = {field: all(record['evidence'][field] == reference[field] for record in records.values())
              for field in reference}
    current = records['after_live']['evidence']
    oz_text = json.dumps(current['oz_state'])
    # Require actual outcomes, not merely successfully registering commands.
    nonempty = {'finals': bool(current['finals']), 'special': bool(current['special']),
                'oz_16_profiles': len(current['oz_state']) == 16,
                'oz_candidate': 'Candidate' in oz_text,
                'fvg_event': any(e['request'].get('strategy') == 'FVG' and
                                 e['request'].get('kind') != 'FVG_CURRENT'
                                 for e in current['manager_events']),
                'ma_watch_delivery': any(chat == '881009' and 'SMA17' in text and '기울기' in text and '감시' not in text
                                         for _, chat, text in current['telegram']),
                'wonbi_delivery': any(chat in ('881010', '881011') and '터치' in text and '감시' not in text
                                      for _, chat, text in current['telegram'])}
    summary = {'equal': all(fields.values()), 'fields': fields, 'nonempty': nonempty,
               'window_s': window, 'specials': list(specials), 'watch_count': len(WATCHES),
               'metrics': {k: v['metrics'] for k, v in records.items()},
               'evidence_sha256': {k: hashlib.sha256(json_bytes(v['evidence'])).hexdigest()
                                   for k, v in records.items()},
               'actual_mt5_verified': False,
               'identity_policy': 'Offline deterministic uuid4; STAFF random session prefix normalized only'}
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary
