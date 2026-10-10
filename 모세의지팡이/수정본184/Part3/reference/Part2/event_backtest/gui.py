"""One action: confirm missing/rebuilt data, then run selected strategies."""
import json,queue,subprocess,sys,threading,tkinter as tk
from tkinter import ttk,filedialog,messagebox
from .settings import ROOT,default_dates,settings

def main():
    root=tk.Tk();root.title('선택 전략 백테스트');root.geometry('900x710')
    f=ttk.Frame(root,padding=16);f.pack(fill='both',expand=True)
    a,b=default_dates();variables={k:tk.StringVar(value=v) for k,v in dict(symbol='XAUUSD+',start=a,end=b,mode='BAR',scenario='',warehouse=settings()['warehouse']).items()}
    labels=['종목','시작일 UTC','종료일 UTC (미포함)','모드','시나리오 JSON','창고 루트']
    for row,((name,var),label) in enumerate(zip(variables.items(),labels)):
        ttk.Label(f,text=label).grid(row=row,column=0,sticky='w')
        control=ttk.Combobox(f,textvariable=var,values=('XAUUSD+','NAS100','BTCUSD') if name=='symbol' else ('BAR','TIMER'),state='readonly') if name in ('symbol','mode') else ttk.Entry(f,textvariable=var)
        control.grid(row=row,column=1,columnspan=2,sticky='ew')
    ttk.Button(f,text='파일',command=lambda:variables['scenario'].set(filedialog.askopenfilename(filetypes=[('JSON','*.json')]) or variables['scenario'].get())).grid(row=4,column=3)
    ttk.Button(f,text='폴더',command=lambda:variables['warehouse'].set(filedialog.askdirectory() or variables['warehouse'].get())).grid(row=5,column=3)
    selection=ttk.LabelFrame(f,text='실행할 전략 (전체 감시는 전체 선택을 명시)');selection.grid(row=6,column=0,columnspan=4,sticky='ew',pady=8)
    from .runner import runtime_config
    config=runtime_config({'symbol':variables['symbol'].get()})
    from event_selection import available
    selected={};names=list(available(config))+['ALL']
    for i,name in enumerate(names):
        var=selected[name]=tk.BooleanVar(value=name=='SPECIAL1')
        ttk.Checkbutton(selection,text=name,variable=var).grid(row=i//5,column=i%5,sticky='w')
    extra=tk.StringVar();ttk.Label(f,text='공식 전략 ID (쉼표 구분)').grid(row=7,column=0,sticky='w')
    ttk.Entry(f,textvariable=extra).grid(row=7,column=1,columnspan=3,sticky='ew')
    rebuild=tk.BooleanVar(value=False);ttk.Checkbutton(f,text='데이터 구축 — 선택 기간 전체 다시 녹화 (검증 후 교체)',variable=rebuild).grid(row=8,column=0,columnspan=4,sticky='w')
    status=tk.StringVar(value='전략을 선택하고 실행하세요. 필요한 녹화는 먼저 확인합니다.')
    ttk.Label(f,textvariable=status).grid(row=10,column=0,columnspan=4,sticky='w')
    bar=ttk.Progressbar(f,maximum=100);bar.grid(row=11,column=0,columnspan=4,sticky='ew')
    log=tk.Text(f,height=16);log.grid(row=12,column=0,columnspan=4,sticky='nsew')
    result=tk.StringVar();ttk.Label(f,textvariable=result,wraplength=830).grid(row=13,column=0,columnspan=4,sticky='w')
    f.rowconfigure(12,weight=1);f.columnconfigure(1,weight=1)
    q=queue.Queue();active=[False];saved_args=[None];last_result=[None]
    def start_process(args,phase):
        def worker():
            try:
                proc=subprocess.Popen(args,cwd=ROOT/'Part2',stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                for line in proc.stdout:q.put(('line',phase,line))
                q.put(('done',phase,proc.wait()))
            except Exception as exc:q.put(('line',phase,str(exc)));q.put(('done',phase,1))
        threading.Thread(target=worker,daemon=True).start()
    def launch():
        if active[0]:return
        names=['ALL'] if selected['ALL'].get() else [n for n,v in selected.items() if v.get()]
        if names!=['ALL']:names +=[n.strip() for n in extra.get().split(',') if n.strip()]
        if not names:messagebox.showerror('전략 필요','실행할 전략을 선택하세요.');return
        args=[sys.executable,'-B','-X','utf8','-m','event_backtest','plan']
        for name,var in variables.items():
            if var.get():args+=['--'+name,var.get()]
        args+=['--strategies',','.join(names)]
        if rebuild.get():args+=['--rebuild']
        saved_args[0]=args;active[0]=True;button.configure(state='disabled');bar.start();status.set('데이터 조각과 예상 구축 비용 확인 중')
        start_process(args,'plan')
    button=ttk.Button(f,text='실행',command=launch);button.grid(row=9,column=0,pady=8)
    def show_result():
        if last_result[0]:
            import os
            os.startfile(str(last_result[0]))
    ttk.Button(f,text='결과 보기',command=show_result).grid(row=9,column=1)
    def idle():active[0]=False;bar.stop();button.configure(state='normal')
    def poll():
        while not q.empty():
            kind,phase,value=q.get()
            if kind=='done':
                if phase=='run' or value!=0:idle();status.set('완료' if value==0 else '중단/오류: 상세 로그 확인')
                continue
            log.insert('end',value);log.see('end')
            try:data=json.loads(value)
            except ValueError:continue
            if phase=='plan' and data.get('event')=='COMPLETE':
                plan=data['result'];estimate=plan['estimate'];pieces=plan['record']
                lines='\n'.join(p['start']+' ~ '+p['end'] for p in pieces)
                final=estimate.get('delta_storage_bytes')
                size_text=f'{final/1e9:.2f}GB (실측 기반 추정)' if final is not None else 'TIMER 차분 실측 자료 없음'
                prompt=f"새 녹화 {len(pieces)}조각\n{lines}\n예상 녹화 {estimate['recording_seconds']/60:.1f}분\n임시 데이터 {estimate['temporary_msp3_bytes']/1e9:.2f}GB\n차분 보관 {size_text}\n권장 여유 {estimate['recommended_free_bytes']/1e9:.2f}GB\n변환·검증 시간 별도. MT5 종료 후 원래대로 복원합니다."
                if pieces and not messagebox.askyesno('데이터 구축 확인',prompt):idle();status.set('녹화하지 않았습니다.');continue
                args=list(saved_args[0]);args[6]='run';args+=['--approved-token',plan['approval_token']]
                status.set('데이터 구축 → 검증 → 선택 전략 실행');start_process(args,'run')
            if data.get('event')=='RUN_PROGRESS':bar.stop();bar['value']=data['percent'];status.set(f"백테스트 {data['percent']:.1f}%")
            if data.get('event')=='CONVERSION_START':status.set('차분 변환·원본 복원 해시 확인 중')
            if data.get('event')=='COMPLETE' and phase=='run':
                path=data['result'].get('alerts_csv','');result.set(path)
                if path:
                    from pathlib import Path
                    last_result[0]=Path(variables['warehouse'].get())/path
        root.after(150,poll)
    def close():
        if active[0]:messagebox.showinfo('실행 중','MT5 복원과 실행 완료 후 닫아 주세요.');return
        root.destroy()
    root.protocol('WM_DELETE_WINDOW',close);poll();root.mainloop()
