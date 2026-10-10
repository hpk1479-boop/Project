from pathlib import Path
R=Path(__file__).resolve().parents[1];p=R/'Part2/event_backtest/gui.py';s=p.read_text('utf8');s=s[:s.index('    rebuild=tk.BooleanVar')]+(R/'build/gui_tail30.txt').read_text('utf-8-sig');p.write_text(s,encoding='utf8')
