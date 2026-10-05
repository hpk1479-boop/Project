import subprocess,sys
from event_e2_common import *
mode=sys.argv[1]
folder=sys.argv[2] if len(sys.argv)>2 else 'final'
assert mode in ('live','replay')
for case in ('synthetic240','xau1200','btc1200','actualXAU','actualBTC'):
    args=[sys.executable,'-X','utf8','-B',str(ROOT/'build/run_event_e2_case.py'),mode,case,
          folder+'/'+case+'_'+mode+'.json']
    if mode=='live' and case=='synthetic240':args.append('--checkpoint')
    if mode=='live' and case.startswith('actual'):args.append('--measure')
    subprocess.run(args,check=True)
