"""The enlarged LIVE settings view keeps the existing settings actions."""
import sys
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part1/program'))

from modern_widgets import ModernButton
from special_ui import strategy_dialog, trigger_dialog, time_dialog
from strategy_settings_view import StrategyCard, StrategyToggle, TriggerName


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def test_live_settings_cards_size_and_original_actions():
    root=tk.Tk()
    saved=[]
    try:
        with patch('special_ui.trigger_dialog',side_effect=lambda _parent,_current,_default,apply,**_options:apply('브레이커 올존')) as trigger, \
             patch('special_ui.time_dialog',side_effect=lambda _parent,_name,_current,_defaults,_config,apply,**_options:apply({'MAIN_ASIA':{'enabled':True}})) as times:
            strategy_dialog(root,ROOT/'Part1/program/SPECIAL',None,{},saved.append,
                            live=True,diagnostics_root=None)
            window=root.winfo_children()[-1]
            root.update()
            assert (window.winfo_width(),window.winfo_height())==(860,720)
            cards=[item for item in descendants(window) if isinstance(item,StrategyCard)]
            toggles=[item for item in descendants(window) if isinstance(item,StrategyToggle)]
            assert len(cards)==len(toggles)==7
            assert [card.grid_info()['row'] for card in cards]==[0,0,1,1,2,2,3]
            assert [card.grid_info()['column'] for card in cards]==[0,1,0,1,0,1,0]
            window.geometry('740x650')
            root.update()
            assert all(card.winfo_width()>300 for card in cards)
            action_bottom=max(item.winfo_rooty()+item.winfo_height()
                              for item in descendants(cards[-1]) if isinstance(item,ModernButton))
            card_bottom=cards[-1].winfo_rooty()+cards[-1].winfo_height()
            assert action_bottom<=card_bottom,(action_bottom,card_bottom,cards[-1].winfo_height())
            toggles[0].invoke()
            actions=[item for item in descendants(window) if isinstance(item,ModernButton)]
            assert not any('트리거' in item.cget('text') for item in actions)
            names=[item for item in descendants(window) if isinstance(item,TriggerName)]
            assert len(names)==7
            names[0].invoke()
            next(item for item in actions if item.cget('text')=='◷  거래시간').invoke()
            next(item for item in actions if item.cget('text')=='✓  저장').invoke()
            assert len(saved)==1
            assert saved[0]['SPECIAL1']=={
                'enabled':False,
                'trigger':'브레이커 올존',
                'time_filters':{'MAIN_ASIA':{'enabled':True}},
            }
            assert trigger.call_count==times.call_count==1
            assert not window.winfo_exists()
    finally:
        root.destroy()


def test_part2_uses_same_modern_window_without_live_parts():
    from strategy_settings_view import SettingsHeader
    root=tk.Tk()
    saved=[]
    try:
        strategy_dialog(root,ROOT/'Part1/program/SPECIAL',None,{},saved.append,research=True)
        window=root.winfo_children()[-1]
        root.update()
        assert window.title()=='백테스트 전략 설정'
        header=next(item for item in descendants(window) if isinstance(item,SettingsHeader))
        assert header.heading=='백테스트 전략 설정' and header.subtitle=='BACKTEST STRATEGY SETTINGS'
        assert not hasattr(header,'record_folder')
        cards=[item for item in descendants(window) if isinstance(item,StrategyCard)]
        toggles=[item for item in descendants(window) if isinstance(item,StrategyToggle)]
        assert len(cards)==len(toggles)>=7 and not any(t.variable.get() for t in toggles)
        texts=[item.cget('text') for item in descendants(window) if isinstance(item,tk.Label)]
        assert 'Part2 전용 설정 · Part1 실전 설정은 바꾸지 않습니다.' in texts
        toggles[0].invoke()
        next(item for item in descendants(window) if isinstance(item,ModernButton) and item.cget('text')=='✓  저장').invoke()
        assert saved[0]['SPECIAL1']['enabled'] is True and saved[0]['SPECIAL2']['enabled'] is False
    finally:
        root.destroy()


def test_compact_pickers_do_not_change_part2_layout():
    root=tk.Tk()
    try:
        trigger_dialog(root,None,'올존',lambda _value:None,compact=True)
        compact_trigger=root.winfo_children()[-1]
        assert [item.cget('text') for item in descendants(compact_trigger)
                if isinstance(item,ttk.Button)]==['기본값','저장','취소']
        compact_trigger.destroy()

        trigger_dialog(root,None,'올존',lambda _value:None)
        legacy_trigger=root.winfo_children()[-1]
        assert [item.cget('text') for item in descendants(legacy_trigger)
                if isinstance(item,ttk.Button)]==['기본값','적용','취소']
        legacy_trigger.destroy()

        config={'MAIN_ASIA':'0800-1200','MAIN_LONDON':'1400-1800',
                'MAIN_NEWYORK':'2100-2400'}
        time_dialog(root,'SPECIAL1',None,0,config,lambda _value:None,compact=True)
        compact_time=root.winfo_children()[-1]
        labels=[item.cget('text') for item in compact_time.winfo_children()
                if isinstance(item,ttk.Label)]
        assert labels==['-','-','-']
        assert len([item for item in compact_time.winfo_children()
                    if isinstance(item,ttk.Entry)])==6
        assert [item.cget('text') for item in descendants(compact_time)
                if isinstance(item,ttk.Button)]==['기본값','저장','취소']
        compact_time.destroy()

        time_dialog(root,'SPECIAL1',None,0,config,lambda _value:None)
        legacy_time=root.winfo_children()[-1]
        labels=[item.cget('text') for item in legacy_time.winfo_children()
                if isinstance(item,ttk.Label)]
        assert len(labels)==2 and all('-'!=text for text in labels)
        legacy_time.destroy()
    finally:
        root.destroy()
