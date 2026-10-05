from pathlib import Path
p=Path(__file__).resolve().parents[1]/'Part1/program/module_status_ui.py'
s=p.read_text('utf8').replace("            if error:status.set('엔진 연결 대기');continue", "            if error:\n                status.set('엔진 상태 연결 끊김 / 시작 대기')\n                for name in ('STAFF','ENGINE'):\n                    old=tree.item(name,'values');tree.item(name,values=('오류·끊김',old[1],old[2]))\n                continue")
p.write_text(s,encoding='utf8')
