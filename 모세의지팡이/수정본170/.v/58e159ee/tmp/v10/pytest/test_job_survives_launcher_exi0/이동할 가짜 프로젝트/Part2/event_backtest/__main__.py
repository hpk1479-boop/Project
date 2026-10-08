import argparse
import json
from pathlib import Path
import time

parser = argparse.ArgumentParser()
parser.add_argument('action', choices=('plan', 'run'))
parser.add_argument('--scenario', required=True)
parser.add_argument('--warehouse', required=True)
parser.add_argument('--session-id', required=True)
parser.add_argument('--skip-cleanup', action='store_true')
parser.add_argument('--approved-token')
parser.add_argument('--rebuild', action='store_true')
args = parser.parse_args()
folder = Path(args.warehouse) / 'runs' / args.session_id
def emit(event):
    line = json.dumps(event)
    print(line, flush=True)
    if args.action == 'run':
        with (folder / 'progress.jsonl').open('a', encoding='utf-8') as output:
            output.write(line + '\n')
if args.action == 'plan':
    emit({'event': 'COMPLETE', 'result': {'record': [], 'reuse': [],
        'convert': [], 'estimate': {}, 'approval_token': 'fake-plan'}})
    raise SystemExit(0)
deadline = time.monotonic() + 30
count = 0
while not (folder / 'stop.request').is_file():
    if time.monotonic() >= deadline:
        emit({'event': 'ERROR', 'message': 'fake test watchdog expired'})
        raise SystemExit(1)
    count += 1
    emit({'event': 'FAKE_PROGRESS', 'processed': count})
    time.sleep(.1)
result = {'status': 'CANCELLED', 'run_id': args.session_id,
    'scenario': json.loads(Path(args.scenario).read_text('utf-8')),
    'result_path': 'runs/' + args.session_id + '/result.json',
    'fake_processed': count}
(folder / 'result.json').write_text(json.dumps(result), encoding='utf-8')
emit({'event': 'COMPLETE', 'result': result})
