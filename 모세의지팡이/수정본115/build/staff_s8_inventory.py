"""Regenerate the current Part1 immutable list only; historical lists are untouched."""
from staff_s8_evidence import *
inventory={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
    if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts
    and p.name!='special_settings.json' and p.suffix!='.ex5'}
write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(inventory.items())))
write(OUT/'immutable_regeneration.json',{'files':len(inventory),'scope':'current Part1 only','historical_manifests_changed':False})
print('Current inventory',len(inventory))
