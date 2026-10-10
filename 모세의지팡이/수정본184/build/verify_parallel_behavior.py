"""Reuse the established offline LIVE/replay scenario, storing revision26 evidence."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/'build/run_engine_behavior.py').read_text('utf-8')
source=source.replace("OUT=ROOT/'검증결과/engine_optimization'","OUT=ROOT/'검증결과/parallel_oz'")
source=source.replace("default='25',choices=('22','25')","default='26',choices=('22','26')")
source=source.replace("a.revision=='25'","a.revision=='26'")
source=source.replace("OUT/'inputs/TIMER'","ROOT/'검증결과/engine_optimization/inputs/TIMER'")
exec(compile(source,str(ROOT/'build/run_engine_behavior.py'),'exec'),{'__name__':'__main__','__file__':str(ROOT/'build/run_engine_behavior.py')})
