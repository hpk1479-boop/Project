"""Existing Tk dashboard renders verified cancellation prefixes; no separate UI."""
import os
from pathlib import Path
import pytest
from generic_backtest import runner
from generic_backtest.gui import GenericDashboard
from generic_backtest.results import verify_result
from validation_suite.integration_fixtures import START
from conditional_validation.test_partial import harness


def labels(widget):
    values=[]
    try:values.append(str(widget.cget('text')))
    except Exception:pass
    for child in widget.winfo_children():values.extend(labels(child))
    return values


@pytest.mark.parametrize('fraction',[.1,.3,.5,.8])
def test_existing_dashboard_displays_committed_prefix(harness,fraction):
    import tkinter as tk
    import tkinter.font as tkfont
    try:root=tk.Tk()
    except tk.TclError as exc:pytest.skip('No Tk display: '+str(exc)+'; use xvfb-run')
    try:
        for name in ('TkDefaultFont','TkTextFont','TkHeadingFont'):
            tkfont.nametofont(name).configure(family='UnDotum',size=10)
        root.withdraw()
        directory,config,state,check=harness
        state['cutoff']=START+int(3600*10**9*fraction)
        result=runner.GenericRunCoordinator(config,cancel=check).run(directory/'dashboard-partial')
        manifest=verify_result(result['result'])
        dashboard=GenericDashboard(root,result)
        root.update_idletasks();root.update()
        text='\n'.join(labels(dashboard.window))
        assert '상태: PARTIAL / CANCELLED' in text
        assert '마지막 정상 commit:' in text and '요청 기간:' in text
        assert 'PARTIAL' in dashboard.window.title()
        assert dashboard.data['execution']==manifest['metadata']['execution']
        assert len(dashboard.alert_tree.get_children())==dashboard.data['alerts']['count']>0
        end=dashboard.data['execution']['computed_interval'][1]
        assert all(row['observed_at_ns']<end for row in dashboard.alert_rows)
        assert dashboard.data['execution']['progress_percent']<fraction*100
        destination=os.environ.get('CONDITIONAL_DASHBOARD_SCREENSHOT')
        if destination and fraction==.5:
            from PIL import ImageGrab
            path=Path(destination);path.parent.mkdir(parents=True,exist_ok=True)
            x,y=dashboard.window.winfo_rootx(),dashboard.window.winfo_rooty()
            ImageGrab.grab(bbox=(x,y,x+dashboard.window.winfo_width(),y+dashboard.window.winfo_height()),
                           xdisplay=os.environ['DISPLAY']).save(path)
        dashboard.window.destroy()
    finally:root.destroy()
