from pathlib import Path
R=Path(__file__).resolve().parents[1]
p=R/'tests/test_ui_slots.py';s=p.read_text('utf8');s=s.replace("    new=(ROOT/'Part1/program/SPECIAL/SPECIAL5.py').read_bytes()", "    # The approved slot merge remains eight lines; revision30 additionally guards trace-only formatting.\n    new=(ROOT.parent/'수정본29/Part1/program/SPECIAL/SPECIAL5.py').read_bytes()",1)
needle="    assert len(ops)==2 and all(t=='insert' and v-u==4 for t,a,b,u,v in ops)"
s=s.replace(needle,needle+'''
    import ast
    class WithoutLogs(ast.NodeTransformer):
        def visit_If(self,node):
            if ast.unparse(node.test).startswith('logging.getLogger().isEnabledFor('):return None
            return self.generic_visit(node)
        def visit_Expr(self,node):
            if isinstance(node.value,ast.Call) and ast.unparse(node.value.func).startswith('logging.'):return None
            return self.generic_visit(node)
    current=(ROOT/'Part1/program/SPECIAL/SPECIAL5.py').read_text('utf-8-sig')
    assert ast.dump(WithoutLogs().visit(ast.parse(new.decode('utf-8-sig'))))==ast.dump(WithoutLogs().visit(ast.parse(current)))
''',1);p.write_text(s,encoding='utf8')
p=R/'Part2/event_backtest/workflow.py';s=p.read_text('utf8');s=s.replace("'status':'기존' if c['capture_id'] in reused else '새로 구축'", "'status':'실패' if c.get('history_missing') else '기존' if c['capture_id'] in reused else '새로 구축'")
s=s.replace("if error and current[0]:rows.append", "if error and current[0] and not any(r['start']==current[0]['start'] and r['end']==current[0]['end'] for r in rows):rows.append")
s=s.replace("emit=progress,cancel=cancel)\n    except", "emit=progress,cancel=cancel)\n        require_complete(captures)\n    except")
s=s.replace("    require_complete(captures)\n    emit", "    emit")
p.write_text(s,encoding='utf8')
p=R/'Part2/event_backtest/virtual_entry.py';s=p.read_text('utf8');s=s.replace("    summary,details=calculator.results()",'''    summary,details=calculator.results()
    present={row['strategy'] for row in summary}
    selected=[f'SPECIAL{i}' for i in range(1,8)] if s['strategies']==['ALL'] else s['strategies']
    for name in selected:
        if name in present:continue
        for rr in RATIOS:
            summary.append(dict(strategy=name,rr=rr,alerts=0,entries=0,passes=0,pass_cross=0,pass_risk=0,
                waiting=0,wins=0,losses=0,unclosed=0,win_rate=None,average_r=None,total_r=0.))
    summary.sort(key=lambda row:(row['strategy'],row['rr']))''')
p.write_text(s,encoding='utf8')
