"""Part3 right-bottom actions: drop the duplicate PART1 button; name the Test-strategy backtest clearly."""
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'Part3/web/index.html';t=p.read_bytes().decode('utf-8')
old=('<div class="action-pair"><button class="teal" data-action="launch" data-part="1">PART 1 호출<small>라이브 감시 화면</small></button>'
     '<button class="blue" data-action="backtest">PART 2 백테스트<small>선택한 Test 전략 실행</small></button></div>')
assert t.count(old)==1
t=t.replace(old,'<div class="action-pair single"><button class="blue" data-action="backtest">▶ &nbsp; 이 Test 전략 백테스트<small>생성된 Test 전략을 PART2 엔진으로 실행</small></button></div>')
p.write_bytes(t.encode('utf-8'));print('ok')
