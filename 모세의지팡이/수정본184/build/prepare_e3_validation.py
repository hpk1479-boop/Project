from pathlib import Path
import ast
R=Path(__file__).resolve().parents[1]
s=(R/'build/run_event_perf1_case.py').read_text('utf-8')
s=s.replace('from part1_host import runtime,engine as capture_engine','')
s=s.replace("choices=('polling','live','replay')","choices=('live','replay')").replace("검증결과/event_perf1","검증결과/event_e3")
a=s.index("    if a.mode=='polling':");b=s.index("    else:\n        requests=",a)
end=s.index('    result.update(case=',b)
block=s[b+len('    else:\n'):end]
block=''.join(line[4:] if line.startswith('    ') else line for line in block.splitlines(keepends=True))
s=s[:a]+block+s[end:]
s=s.replace("config=runtime.backtest_config(ROOT/'Part1',{'STAFF_ALLOWED_SYMBOLS':symbol,'TARGET_SYMBOLS':symbol})", """from event_composer_domain import load_config
    config=load_config(str(ROOT/'Part1/program/config.txt'))
    config.update(STAFF_ALLOWED_SYMBOLS=symbol,TARGET_SYMBOLS=symbol,TELEGRAM_TOKEN='OFFLINE',
                  TELEGRAM_CHAT_ID='OFFICIAL',GEMINI_API_KEY='',GEMINI_FALLBACK_ENABLED='false',ECONOMY_ENABLED='false')""")
s=s.replace("    e=create_event_engine(config,symbols=(symbol,),collect_timings=a.measure)","""    e=create_event_engine(config,symbols=(symbol,),collect_timings=a.measure)
    from event_host import EventHost
    from command_interpreter import CommandInterpreter
    sent=[]
    def transport(data):
        sent.append(dict(data))
        return types.SimpleNamespace(status_code=200,json=lambda:{'ok':True,'result':{'message_id':len(sent)}})
    interpreter=CommandInterpreter(config,ROOT/'Part1/program/command_aliases.json')
    host=EventHost(e,config,interpreter,transport=transport)""")
s=s.replace('    e.run()\n    for index,','    e.run();host.drain_outputs()\n    for index,')
s=s.replace('        e.run()\n        if resumed','        e.run();host.drain_outputs()\n        if resumed')
s=s.replace("    result={'signals':", "    result={'output_deliveries':host.output.results,'signals':")
ast.parse(s);(R/'build/run_event_e3_case.py').write_text(s,encoding='utf-8')
for name in ('test_event_e2_domains.py','test_event_e2_oz_pilot.py'):
    p=R/'tests'/name;s=p.read_text('utf-8').replace("ROOT.parent/'수정본16'","ROOT")
    s=s.replace('frozen revision16','current direct decision function')
    p.write_text(s,encoding='utf-8')
p=R/'tests/test_event_e1.py';s=p.read_text('utf-8')
a=s.index("    opt_in={'event_application.py'");b=s.index('    ea=',a)
s=s[:a]+'''    # E3 deliberately replaces the disconnected polling default.
    supervisor=(PROGRAM.parent/'OZ_SYSTEM CONTROL.pyw').read_text('utf-8')
    assert 'PROGRAMS = [("EVENT ENGINE", "event_host.py", 0.0)]' in supervisor
    assert 'from event_engine' in (PROGRAM/'event_host.py').read_text('utf-8')
'''+s[b:]
s=s.replace('test_production_core_static_contract_and_default_disconnected','test_production_core_static_contract_and_event_default')
p.write_text(s,encoding='utf-8')
(R/'AGENTS.md').write_text((R.parent/'AGENTS.md').read_text('utf-8'),encoding='utf-8')
with (R/'AGENTS.md.txt').open('a',encoding='utf-8') as f:f.write('''
## E3 최신 지시 (위의 단계별 제한보다 우선)
- 수정본18 읽기 전용, 수정본19 독립 산출. 기본 실행은 event_host이며 폴링·가상 시계 실행기는 제거한다.
- 루트 AGENTS.md의 검증 원칙·역할 분리·개발 방향을 따른다. 이전 알림/폴링 결과 일치는 합격 기준이 아니다.
- 김매니저는 SIGNAL 출력만, 명령/Gemini는 호스트가 Ingress로 공급한다. 이벤트 전용 상태 경로에만 저장한다.
- 관련 로직 시험, 합성240/보존XAU LIVE 수신=재생, 시작 복원, XAU 참고1회만. 실제 Telegram 금지.
- MT5는 요청한 초기 버퍼/native export 수정 및 컴파일만. 신규 캡처·성능 게이트·Part2 일반 회귀 금지.
- E3 보고 완료 후 별도 승인된 수정본20 OZ 재작성으로 진행한다. E3 중 OZ 판정 재작성 금지.
''')
print('E3 validation runner and policy prepared')
