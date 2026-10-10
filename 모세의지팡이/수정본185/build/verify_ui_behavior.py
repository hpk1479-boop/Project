"""Established offline LIVE/replay test with isolated time-slot inputs."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/'build/run_engine_behavior.py').read_text('utf-8')
source=source.replace("OUT=ROOT/'검증결과/engine_optimization'","OUT=ROOT/'검증결과/ui_slots'")
source=source.replace("default='25',choices=('22','25')","default='27',choices=('22','27')")
source=source.replace("a.revision=='25'","a.revision=='27'")
source=source.replace("selection=['ALL'],backtest=True)","selection=['ALL'],backtest=True,time_overrides={'SPECIAL1':{},'SPECIAL5':{'MAIN_ASIA':{'enabled':True,'start':'08:30','end':'11:30'}}})")
exec(compile(source,str(ROOT/'build/run_engine_behavior.py'),'exec'),{'__name__':'__main__','__file__':str(ROOT/'build/run_engine_behavior.py')})
