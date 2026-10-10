from pathlib import Path
p=Path(__file__).resolve().parents[1]/'Part1/program/oz_engine/controllers.py'
b=p.read_bytes();nl='\r\n' if b'\r\n' in b else '\n';s=b.decode().replace('\r\n','\n')
needle='''                        w.source == "MANUAL"
                        and w.persistent
                        and w.timeframes == ordered'''
repl='''                        w.source == "MANUAL"
                        and w.persistent
                        and (not explicit_wid or w.watch_id == explicit_wid)
                        and w.timeframes == ordered'''
assert needle in s;s=s.replace(needle,repl,1);p.write_bytes(s.replace('\n',nl).encode())
