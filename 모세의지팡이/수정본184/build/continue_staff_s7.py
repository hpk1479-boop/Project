from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'Part2/calculations/common.py';s=p.read_text(encoding='utf-8-sig');s=s[:s.index('def add_wonbi_features(')];p.write_text(s,encoding='utf-8')
script=(ROOT/'build/implement_staff_s7.py').read_text(encoding='utf-8')
exec(script[:script.index("edit('Part1/program/staff_schema.py'")] + script[script.index("edit('Part1/program/monitor_OZ.py'"):])
