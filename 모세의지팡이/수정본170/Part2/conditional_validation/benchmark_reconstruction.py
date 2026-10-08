"""Actual native catch-up costs, explicitly not a full-strategy/month speed test."""
from pathlib import Path
import json,sys,time
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT)]
from conditional_validation.test_conditional import setup,bind
from generic_backtest import conditional_client as client
from generic_backtest.context import frozen
from validation_suite.test_gate import tick


def main():
    req,core,eager,lazy=setup('TICK')
    original=client._rpc
    client._rpc=lambda ctx,body:lazy.handle(dict(body,binding=dict(ctx._conditional)))
    client._receivers.clear()
    expected=[];snapshots=[];eager_seconds=0.;input_seconds=0.;restore_wall=0.;equal=0
    active={80,81,82,120,121,122}
    try:
        for i in range(1,151):
            t=tick(i*60,i,120. if i==111 else 100+(i%13)*.1)
            view,change=core.step(t,gap=i==99)
            started=time.perf_counter();lazy.on_raw(t,i==99,view)
            markers=lazy.advance(view,change);input_seconds+=time.perf_counter()-started
            started=time.perf_counter();eager.advance(view,change,materialize=False)
            values=eager.export_snapshot(eager.snapshot(),view.token)
            eager_seconds+=time.perf_counter()-started;expected.append(values)
            ctx=bind(lazy,req,view,markers,op='resume_after_session_gap' if i==99 else 'observe')
            if i<80:assert not lazy.streams
            if i in active:
                started=time.perf_counter()
                for old,current,gap in client.stage_observations(ctx,'MEASURE_RECONSTRUCTION',tuple(values)):
                    j=old.token.source_ordinal-1
                    assert all(old.feature(k)==frozen(v) for k,v in expected[j].items())
                    assert old.token.source_ordinal<=i and old.token.now_ns<=ctx.token.now_ns
                    equal+=1
                restore_wall+=time.perf_counter()-started
                snapshots.append({'gate_observation':i,'diagnostics':lazy.diagnostics()})
    finally:client._rpc=original;client._receivers.clear()
    result={'scope':'SYNTHETIC_NATIVE_REPLAY_MICROBENCH_NOT_FULL_STRATEGY_OR_MARKET_MONTH',
        'observations':150,'active_observations':sorted(active),'feature_timeframes':['1m','3m'],
        'note':'One HMA6 dependency and one four-family native bundle. Timings measured together, not independent end-to-end runs.',
        'equivalent_replayed_contexts':equal,'eager_native_bundle_calls':150,'eager_family_calls':600,
        'eager_advance_and_export_seconds':eager_seconds,'lazy_input_recording_seconds':input_seconds,
        'lazy_history_roundtrip_and_comparison_seconds':restore_wall,
        'snapshots':snapshots,'final':lazy.diagnostics(),
        'not_proven':['positive full-strategy events','native terminal seed/poll parity','month speedup']}
    out=ROOT/'conditional_validation/reconstruction_benchmark.json'
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result['final'],indent=2))
if __name__=='__main__':main()
