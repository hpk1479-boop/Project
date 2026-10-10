from pathlib import Path
p=Path(__file__).resolve().parents[1]/'Part1/program/oz_engine/controllers.py'
b=p.read_bytes();nl='\r\n' if b'\r\n' in b else '\n';s=b.decode().replace('\r\n','\n')
a=s.index('    def validate_external_true_b0(');b=s.index('    def add_manual(',a)
part=s[a:b]
part=part.replace('            linked: list[str] = []','            linked: list[str] = []\n            non_external_allowed = False')
part=part.replace('''                if ext_id is None:
                    return True''','''                if ext_id is None:
                    non_external_allowed = True
                    continue''')
part=part.replace('        passed = False','        passed = non_external_allowed')
s=s[:a]+part+s[b:];p.write_bytes(s.replace('\n',nl).encode())
