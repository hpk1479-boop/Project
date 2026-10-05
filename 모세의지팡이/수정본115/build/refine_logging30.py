from pathlib import Path
R=Path(__file__).resolve().parents[1];P=R/'Part1/program'
def save(p,s):p.write_bytes(s.encode('utf8'))
p=P/'module_diagnostics.py';s=p.read_text('utf8');s=s.replace("'WATCH_CONDITIONS':'WATCH'","'TREND':'INDICATOR','WATCH_CONDITIONS':'WATCH'");save(p,s)
p=P/'event_composer_domain.py';s=p.read_bytes().decode('utf8');s=s.replace("r'SPECIAL[1-7]',spec.spec_id.upper()","r'(?:SPECIAL|PIPELINE_|MAIN_)([1-7])',spec.spec_id.upper()");s=s.replace("_trace_match[0] if _trace_match else 'ENGINE'","'SPECIAL'+_trace_match[1] if _trace_match else 'ENGINE'")
s=s.replace('                result = callback(dict(event))','''                result = callback(dict(event))
                if logging.getLogger().isEnabledFor(logging.INFO):
                    _trace_match=re.search(r'(?:SPECIAL|PIPELINE_)([1-7])',spec_id.upper())
                    logging.info("최종 OZ 판정 · %s · 결과=%s",spec_id,result,
                        extra={'trace_module':'SPECIAL'+_trace_match[1] if _trace_match else 'ENGINE'})''')
s=s.replace('logging.exception("[SPECIAL OZ Hook] 처리 실패 | spec=%s", spec_id)','logging.exception("[SPECIAL OZ Hook] 처리 실패 | spec=%s", spec_id, extra={"trace_module": "SPECIAL"+re.search(r"(?:SPECIAL|PIPELINE_)([1-7])",spec_id)[1] if re.search(r"(?:SPECIAL|PIPELINE_)([1-7])",spec_id) else "ENGINE"})')
s=s.replace('                poll(tuple(targets))','''                poll(tuple(targets))
                if logging.getLogger().isEnabledFor(logging.INFO):
                    _trace_match=re.search(r'SPECIAL([1-7])',condition_type.upper())
                    logging.info("연쇄 상태 처리 · %s · targets=%s",condition_type,targets,
                        extra={'trace_module':'SPECIAL'+_trace_match[1] if _trace_match else 'ENGINE'})''')
save(p,s)
# Copy the one old test into the isolated before runtime, not the prior revision.
b=R/'검증결과/log_restore/before29';(b/'tests').mkdir(exist_ok=True)
for n in ('test_event_e1.py',):
 (b/'tests'/n).write_bytes((R.with_name('수정본29')/'tests'/n).read_bytes())
