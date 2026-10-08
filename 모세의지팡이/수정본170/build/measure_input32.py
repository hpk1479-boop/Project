from pathlib import Path
import argparse,cProfile,hashlib,itertools,json,pstats,sys,time
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/shared_oz_composer_input'
p=argparse.ArgumentParser();p.add_argument('phase',choices=['policy','final']);p.add_argument('--verify',action='store_true');a=p.parse_args()
runtime=OUT/'policy_runtime' if a.phase=='policy' else ROOT
sys.dont_write_bytecode=True;sys.path[:0]=[str(runtime/'Part1/program'),str(runtime/'Part2')]
from event_backtest.system import deny_network
deny_network()
from event_backtest.bridge import CaptureInputs
from event_backtest.keyframes import read_index,read_indexed,verify_indexed
from event_backtest.delta import update_hash
from event_host import load_staff
from event_engine.model import Kind
warehouse=ROOT.parent.with_name(ROOT.parent.name+'_warehouse')
source=next((warehouse/'captures/XAUUSD+/BAR/2025/09').glob('*/capture.delta2'))
if a.verify:
    expected=read_index(source);t=time.perf_counter()
    checked=verify_indexed(source,expected)
    fast_hash=hashlib.sha256();count=0
    for stamp,raw in read_indexed(source,verify_crc=False):update_hash(fast_hash,stamp,raw);count+=1
    assert fast_hash.hexdigest()==checked['bundle_sha256'] and count==checked['bundles']
    checked.update(seconds=time.perf_counter()-t,replay_restoration_hash=fast_hash.hexdigest())
    (OUT/'month_restore_verification.json').write_text(json.dumps(checked,indent=2),encoding='utf-8')
    print(checked);raise SystemExit
def run(profiled):
    staff=load_staff();clock=[0.];cache=staff.StaffPipeCache('',health_session='INPUT_PROBE',monotonic=lambda:clock[0],gap_journal=OUT/(a.phase+'_input_gaps.jsonl'))
    inputs=CaptureInputs(staff,cache,[source.parent],clock=clock,transport='replay',start_ms=0)
    profile=cProfile.Profile() if profiled else None
    h=hashlib.sha256();n=0;cpu=time.process_time();wall=time.perf_counter();input_ns=0
    if profile:profile.enable()
    iterator=iter(inputs)
    while True:
        began=time.perf_counter_ns()
        try:item=next(iterator)
        except StopIteration:break
        input_ns+=time.perf_counter_ns()-began
        if item.kind!=Kind.MARKET_BUNDLE:continue
        h.update(str(item.source_time).encode())
        for tf,snap in item.payload['feeds'].items():
            h.update(tf.encode());h.update(snap.time.tobytes());h.update(snap.volume.tobytes());h.update(snap.values.tobytes())
        n+=1
        if n==800:break
    if profile:profile.disable()
    result={'bundles':n,'wall_ms_per_bundle':(time.perf_counter()-wall)*1000/n,
            'bridge_ms_per_bundle':input_ns/1e6/n,'cpu_ms_per_bundle':(time.process_time()-cpu)*1000/n,'snapshot_sha256':h.hexdigest()}
    if profile:
        path=OUT/(a.phase+'_input_profile.txt')
        with path.open('w',encoding='utf-8') as f:pstats.Stats(profile,stream=f).sort_stats('cumulative').print_stats(40)
        text=path.read_text('utf-8').replace(str(ROOT),'.')
        text=text.replace(str(Path(sys.prefix)),'<python-runtime>')
        path.write_text(text,encoding='utf-8')
    return result
result={'normal':run(False),'profiled':run(True)}
(OUT/(a.phase+'_input_measurement.json')).write_text(json.dumps(result,indent=2),encoding='utf-8');print(result)
