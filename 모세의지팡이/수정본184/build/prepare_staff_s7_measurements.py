from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=(ROOT/'build/capture_staff_s6_mt5.py').read_text(encoding='utf-8')
s=s.replace('staff_s6','staff_s7').replace("parser.add_argument('--btc-weekend'", "parser.add_argument('--sigma',type=float,default=3.0);parser.add_argument('--btc-weekend'")
s=s.replace("work=OUT/f'{label}_v{version}'", "work=OUT/f'{label}_sigma{options.sigma:g}_v{version}'")
s=s.replace('InpWireVersion={version}\\n', 'InpWireVersion={version}\\nInpWonbiSigma={options.sigma}\\n')
s=s.replace("'wire_version':version,", "'wire_version':version,'wonbi_sigma':options.sigma,")
(ROOT/'build/capture_staff_s7_mt5.py').write_text(s,encoding='utf-8')
s=(ROOT/'build/live_staff_s6_btc.py').read_text(encoding='utf-8').replace('staff_s6','staff_s7').replace('StaffS6_','StaffS7_').replace('s6_live','s7_live')
s=s.replace("'source_health':health,", "'wonbi_samples':{tf:cache.snapshot('BTCUSD',tf).values[-1,[12,45,46,47]].tolist() for _,tf in cache.keys()},\n    'source_health':health,")
(ROOT/'build/live_staff_s7_btc.py').write_text(s,encoding='utf-8')
