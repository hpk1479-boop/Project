from pathlib import Path
import ast,json
r=Path('수정본20');out=r/'검증결과/oz_rewrite'
p=r/'Part1/program/event_engine/history.py';s=p.read_text('utf-8')
a=s.index('def _episode_anchors');b=s.index('def required_rows',a);s=s[:a]+s[b:]
a=s.index('    if indicators is None:');b=s.index('    for name in indicators or ():',a);s=s[:a]+s[b:]
s=s.replace(", anchors=()",'').replace('from collections.abc import Mapping\n','').replace('from dataclasses import fields, is_dataclass\n','').replace('import json\n','').replace('import numpy as np\n','').replace('Event anchors and contiguous OZ structures are never cut to a fixed window.','OZ now reads full NumPy Snapshot history independently of these windows.')
p.write_text(s,encoding='utf-8')
p=r/'tests/test_event_perf1.py';s=p.read_text('utf-8');(out/'retired_perf1_source.py.txt').write_text(s,encoding='utf-8')
retired=['test_oz_owner_reuse_cannot_contaminate_another_consumer','test_oz_cached_tail_matches_full650_at_decision_rows','test_history_windows_keep_structures_and_explicit_anchors','test_startup_file_anchor_retained_before_first_profile_restore','test_out_episode_extreme_retained_before_it_becomes_b0','test_percentile_inside_outside_and_missing_values','test_truncated_oz_local_values_match_full650']
lines=s.splitlines(True)
for node in sorted([n for n in ast.parse(s).body if isinstance(n,ast.FunctionDef) and n.name in retired],key=lambda n:n.lineno,reverse=True):
    start=min([node.lineno]+[x.lineno for x in node.decorator_list])-1
    del lines[start:node.end_lineno]
s=''.join(lines).replace('from event_engine.preparation import OZDerivedCache\n','').replace("small=f.tail(required_rows(f,'1m',None,anchors=(f.time.iloc[300],))).reset_index(drop=True)","small=f  # OZ now retains all available Snapshot rows.")
p.write_text(s,encoding='utf-8')
(out/'retired_tests.json').write_text(json.dumps({'reason':'OZ-only DataFrame window/cache implementation removed; intended-logic cases replaced in test_oz_rewrite.py. Other consumer isolation/logic tests retained.','retired_functions':retired,'preserved_source':'retired_perf1_source.py.txt'},ensure_ascii=False,indent=2),encoding='utf-8')
