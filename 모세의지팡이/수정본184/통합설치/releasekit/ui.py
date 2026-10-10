"""Developer UI, never distributed to recipients."""
from datetime import date, datetime
from pathlib import Path
import json
import os
import queue
import re
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from .builder import BuildSettings
from .workflow import run_release


def months_later(months):
    import calendar
    today = date.today()
    total = today.year * 12 + today.month - 1 + months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(today.day, calendar.monthrange(year, month)[1])).isoformat()


def main():
    root = Path(__file__).resolve().parents[2]
    window = tk.Tk()
    window.title('모세의지팡이 배포')
    window.geometry('570x410')
    version = tk.StringVar(value='v1.0')
    expires = tk.StringVar(value=months_later(12))
    minutes = tk.StringVar(value='10')
    events = queue.Queue()
    building = False
    frame = ttk.Frame(window, padding=18)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='현재 작업 폴더: ' + root.name).grid(row=0, column=0, columnspan=3, sticky='w', pady=8)
    for row, label, variable in ((1, '배포 버전', version), (2, '프로그램 만료일', expires),
                                  (4, '설치파일 유효시간(분)', minutes)):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', pady=7)
        ttk.Entry(frame, textvariable=variable, width=28).grid(row=row, column=1, columnspan=2, sticky='ew')
    shortcuts = ttk.Frame(frame)
    shortcuts.grid(row=3, column=1, columnspan=2, sticky='w')
    for months in (1, 3, 6, 12):
        ttk.Button(shortcuts, text=f'{months}개월', width=7,
                   command=lambda value=months: expires.set(months_later(value))).pack(side='left', padx=(0, 5))
    ttk.Label(frame, text='실행 PC의 달력 기준, 만료일 다음 날 00:00부터 차단됩니다.').grid(row=5, column=0, columnspan=3, sticky='w', pady=8)
    status = tk.Text(frame, height=8, width=66, state='disabled')
    status.grid(row=7, column=0, columnspan=3, sticky='nsew', pady=8)

    def log(text):
        status.config(state='normal')
        status.insert('end', text + '\n')
        status.see('end')
        status.config(state='disabled')

    def start():
        nonlocal building
        try:
            settings = BuildSettings(version.get().strip(), expires.get().strip(), int(minutes.get()))
            settings.validate()
        except Exception as error:
            messagebox.showerror('설정 확인', str(error))
            return
        button.config(state='disabled')
        building = True
        log('빌드 시작 — 원본 프로그램은 읽기만 합니다.')
        def worker():
            try:
                final, report = run_release(root, settings, emit=lambda value: events.put(('log', value)))
                events.put(('done', final.relative_to(root).as_posix()))
            except Exception as error:
                from .builder import _portable
                events.put(('error', _portable(error, root)))
        threading.Thread(target=worker, daemon=True).start()

    button = ttk.Button(frame, text='모세의지팡이 통합설치 만들기', command=start)
    button.grid(row=6, column=0, columnspan=3, sticky='ew', pady=8)

    def poll():
        nonlocal building
        while not events.empty():
            kind, value = events.get_nowait()
            log(value)
            if kind in {'done', 'error'}:
                building = False
                button.config(state='normal')
                if kind == 'error':
                    messagebox.showerror('모세의지팡이 배포', value)
                else:
                    messagebox.showinfo('배포 생성 완료', value)
        window.after(150, poll)
    poll()
    def close():
        if building:
            log('배포 생성과 임시 파일 정리가 끝나면 창을 닫을 수 있습니다.')
            return
        window.destroy()
    window.protocol('WM_DELETE_WINDOW', close)
    def announce_ready():
        token = os.environ.get('MOSES_RELEASE_LAUNCH_TOKEN', '')
        if not re.fullmatch('[0-9a-f]{32}', token):
            return
        path = root / '통합설치' / '.buildtmp' / ('launcher_' + token + '.ready.json')
        if not path.resolve().is_relative_to(root.resolve()):
            return
        if not window.winfo_viewable():
            window.after(100, announce_ready)
            return
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'pid': os.getpid(), 'ready': True,
                                          'window_title': window.title()}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)
    window.after(100, announce_ready)
    window.mainloop()


if __name__ == '__main__':
    main()
