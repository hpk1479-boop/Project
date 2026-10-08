from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'Part1/OZ_SYSTEM CONTROL.pyw'
raw=p.read_bytes();text=raw.decode('utf-8');nl='\r\n' if b'\r\n' in raw else '\n'
s=text.replace('\r\n','\n')
s=s.replace('"trigger": str(trigger).strip() if isinstance(trigger, str) and trigger.strip() else None,','"trigger": str(trigger).strip() if isinstance(trigger, str) and trigger.strip() else None,\n            "time_filters": item.get("time_filters"),')
s=s.replace('def start_program(script_name, enabled_specials=None, special_triggers=""):', 'def start_program(script_name, enabled_specials=None, special_triggers="", special_times=""):')
s=s.replace('env["OZ_SPECIAL_TRIGGERS"] = special_triggers or ""','env["OZ_SPECIAL_TRIGGERS"] = special_triggers or ""\n            env["OZ_SPECIAL_TIME_FILTERS"] = special_times or ""')
start=s.index('    def open_special_settings(self):')
end=s.index('\n    def ',start+10)
s=s[:start]+'''    def open_special_settings(self):
        folder = special_dir()
        if folder is None:
            messagebox.showerror("전략 설정", "SPECIAL 폴더를 찾지 못했습니다.")
            return
        if str(folder.parent) not in sys.path:
            sys.path.insert(0, str(folder.parent))
        from special_ui import strategy_dialog
        config = {}
        for line in (folder.parent / "config.txt").read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()
        def apply(settings):
            save_special_settings(settings)
            self.special_settings = settings
            self.message.config(text="전략 설정 저장 완료 · 다음 엔진 시작 시 적용")
        strategy_dialog(self.root, folder, self.special_settings, config, apply, live=True)
''' +s[end:]
s=s.replace('ok, msg = start_program(script, enabled_specials=selection, special_triggers=triggers)', '''times = json.dumps({name: item["time_filters"] for name, item in (self.special_settings or {}).items()
                                if item.get("time_filters") is not None}, ensure_ascii=False)
            ok, msg = start_program(script, enabled_specials=selection, special_triggers=triggers, special_times=times)''')
p.write_bytes(s.replace('\n',nl).encode('utf-8'))
print('Part1 dialog/storage/environment connected; newline style retained')
