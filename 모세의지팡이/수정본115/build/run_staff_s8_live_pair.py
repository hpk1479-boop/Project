"""Both EA versions see the same overlapping BTC LIVE market interval."""
import os,subprocess,sys,time
from staff_s8_evidence import *
deadline=time.time()+50;workers=[]
for before in (True,False):
    log=(OUT/('live_before.log' if before else 'live_after.log')).open('x',encoding='utf-8')
    args=[sys.executable,'-X','utf8','-B',str(ROOT/'build/live_staff_s8_btc.py'),'--deadline',str(deadline)]
    if before:args.append('--before')
    workers.append((subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}),log))
for p,log in workers:
    assert p.wait(timeout=90)==0
    log.close()
a=read(OUT/'live_BTC_before/result.json');b=read(OUT/'live_BTC/result.json')
start=max(a['started_ms'],b['started_ms'])+15000;end=int(deadline*1000)
def measure(r):
    frames=[f for f in r['frames'] if start<=f['received_ms']<end]
    seconds=(end-start)/1000
    return {'seconds':seconds,'bundles':len(frames),'frames_per_second':len(frames)/seconds,
        'feed_frames':sum(len(f['children']) for f in frames),'pipe_bytes':sum(f['bytes'] for f in frames),
        'receive_cpu_s':sum(f['cpu_s'] for f in frames),
        'status_file_observed_writes':sum(start<=x['observed_ms']<end for x in r['status_file_observed_changes'])}
result={'interval_start_ms':start,'interval_end_ms':end,'S7':measure(a),'S8':measure(b),
        'reference_only':True,'status_count_method':'20ms file mtime observer; observed writes, possible undercount during blocking OS scheduling',
        'receiver_cpu_method':'thread CPU from receive_one entry through validation/publication; disk recorder excluded',
        'actual_live_received':a['actual_live_received'] and b['actual_live_received']}
write(OUT/'live_performance_reference.json',result);print(json.dumps(result,indent=2))
