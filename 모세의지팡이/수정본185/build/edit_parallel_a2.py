from pathlib import Path
P=Path(__file__).resolve().parents[1]/'Part1/program'
def replace(name,changes):
    p=P/name;raw=p.read_bytes()
    for a,b in changes:
        if a.encode() not in raw:raise ValueError((name,a))
        raw=raw.replace(a.encode(),b.encode())
    p.write_bytes(raw)
replace('event_composer_domain.py',[('for wid in removed:', 'for wid in sorted(removed):')])
replace('event_selection.py',[('for name in consumers:', 'for name in sorted(consumers):')])
for file in ('oz_engine/controllers.py','monitor_OZ.py'):
    p=P/file;raw=p.read_bytes()
    for name in ('stale_ids','ids','removed_external_ids','replaced_manual_external_ids','removed_manual_external_ids'):
        raw=raw.replace(f'for wid in {name}:'.encode(),f'for wid in sorted({name}):'.encode())
        raw=raw.replace(f'for watch_id in {name}:'.encode(),f'for watch_id in sorted({name}):'.encode())
    for name in ('affected_profiles','removed_profiles','affected'):
        raw=raw.replace(f'in {name}:'.encode(),f'in sorted({name}):'.encode())
    p.write_bytes(raw)
print('unordered side-effect loops fixed; ordered registrations preserved')
