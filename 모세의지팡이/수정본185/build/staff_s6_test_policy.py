"""Explicit collection accounting; no blanket filtering of new failures."""
SKIP_INTERNAL={'test_staff_s3.py::test_600_random_legacy_combinations_exact':
    'User S6+ policy: do not repeat large internal DataFrame combination grids; representative decision inputs and expanded external behavior replace it.'}

def pytest_collection_modifyitems(config,items):
    import json,os
    from pathlib import Path
    root=Path(__file__).resolve().parents[1];out=root/'검증결과/staff_s6/regressions'
    group=os.environ.get('STAFF_TEST_GROUP','unknown')
    collected=[];kept=[];omitted=[]
    for item in items:
        short=item.nodeid.split('/')[-1].split('\\')[-1]
        collected.append(item.nodeid)
        if short in SKIP_INTERNAL:omitted.append(item)
        else:kept.append(item)
    (out/f'{group}_collection.json').write_text(json.dumps({'all_ids':collected,'selected':len(kept),
        'policy_deselected':[{'id':i.nodeid,'reason':SKIP_INTERNAL[i.nodeid.split('/')[-1].split('\\')[-1]]} for i in omitted]},indent=2),encoding='utf-8')
    items[:]=kept
    if omitted:config.hook.pytest_deselected(items=omitted)
