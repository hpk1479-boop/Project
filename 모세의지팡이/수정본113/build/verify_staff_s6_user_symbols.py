"""Verify the user's exact config via public replay injection and command handling."""
import logging, sys
from staff_s6_evidence import *
sys.path.insert(0,str(ROOT/'Part2'))
from part1_host.runtime import Part1Runtime, read_part1_config
from part1_host.synthetic import SyntheticMarket, TIMEFRAMES
from part1_host.wire_v2 import Publisher
from staff_golden.scenario import START

path=ROOT/'Part1/program/config.txt';original=path.read_bytes()
cfg=read_part1_config(ROOT/'Part1');keys=('STAFF_ALLOWED_SYMBOLS','TARGET_SYMBOLS')
expected=['XAUUSD+','NAS100','BTCUSD']
values={key:cfg[key] for key in keys}
assert all([v.strip() for v in value.split(',')]==expected for value in values.values())
rt=Part1Runtime(symbols=expected,start_epoch=START,specials=['SPECIAL7'],
    part1_root=ROOT/'Part1',log_level=logging.CRITICAL)
try:
    assert rt.staff_server.allowed_symbols==set(expected)
    health={};commands={}
    for i,symbol in enumerate(expected):
        market=SyntheticMarket(symbol,START,START+1,history_days=30,seed=0)
        pub=Publisher(symbol)
        rt.publish(pub.bundle((tf,market.payload(tf,START)) for tf in TIMEFRAMES))
        health[symbol]=rt.staff_server.handle({'kind':'SOURCE_HEALTH','symbol':symbol,'timeframes':['1m']})
        assert 'error' not in health[symbol],health[symbol]
        assert rt.manager.command_interpreter.parse_symbol(symbol)==symbol
        command=f'{symbol} 1분 기울기(SMA17) > 0 알려줘'
        rt.manager.handle_command(command,str(889100+i))
        commands[symbol]={'command':command,'chat':str(889100+i)}
    rt.start_services()
    rt.run_until(START+1)
    for symbol,entry in commands.items():
        replies=[str(d['data'].get('text','')) for d in rt.http.deliveries
                 if str(d['data'].get('chat_id'))==entry['chat']]
        assert replies and all('전략을 이해하지 못했습니다' not in text for text in replies),replies
        assert any('감시' in text for text in replies),replies
        entry.update(replies=replies,registered=True)
finally:rt.close()
assert path.read_bytes()==original,'User config must not be rewritten'
write(OUT/'user_symbol_validation.json',{'passed':True,'user_owned_edit':True,
    'values':values,'parsed_symbols':expected,'source_sha256':sha(path),
    'source_unchanged_by_validation':True,'config_overrides':None,
    'external_http':'offline only','health':health,'commands':commands})
print('PASS: exact user config; all three symbols accepted by STAFF and private commands')
