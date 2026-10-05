from pathlib import Path
import sys
ROOT=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
p=ROOT/'Part1/program/event_composer_domain.py'
b=p.read_bytes();nl='\r\n' if b'\r\n' in b else '\n';s=b.decode().replace('\r\n','\n')
needle='''                self._manager._active_children.pop(wid, None)
                removed_ids.append(str(wid))'''
repl='''                transaction = getattr(self._manager, '_oz_dispatch_transaction', None)
                if transaction and wid in transaction['ids']:
                    transaction['pending'].add(wid)
                    removed_ids.append(str(wid))
                    continue
                self._manager._active_children.pop(wid, None)
                removed_ids.append(str(wid))'''
assert needle in s;s=s.replace(needle,repl,1)
needle='            self.watch_orchestrator.complete_final_oz(event, delivered=True)'
repl='''            transaction = getattr(self, '_oz_dispatch_transaction', None)
            if transaction is not None:
                transaction['completions'].append(dict(event))
            else:
                self.watch_orchestrator.complete_final_oz(event, delivered=True)'''
assert needle in s;s=s.replace(needle,repl,1)
p.write_bytes(s.replace('\n',nl).encode())
