from pathlib import Path
p=Path(__file__).resolve().parents[1]/'Part1/program/manager_KIM.py';s=p.read_text('utf-8')
s=s.replace('from __future__ import annotations','from __future__ import annotations\nimport domain_memory',1)
a='''        notifier: Optional[NotificationService] = None,
    ):
        super().__init__(name="KIM-Composer", daemon=True)'''
b='''        notifier: Optional[NotificationService] = None,
        *, event_services=None,
    ):
        super().__init__(name="KIM-Composer", daemon=True)
        self.event_services = event_services'''
assert s.count(a)==1;s=s.replace(a,b)
s=s.replace('special_base_dir = Path(__file__).resolve().parent',
            'special_base_dir = Path(__file__).resolve().parent if event_services is None else event_services.root',1)
s=s.replace('        self.special_dir.mkdir(parents=True, exist_ok=True)',
            '        if event_services is None:self.special_dir.mkdir(parents=True, exist_ok=True)',1)
s=s.replace('self.command_aliases_path = self._resolve_config_path(aliases_name)',
            'self.command_aliases_path = self._resolve_config_path(aliases_name) if event_services is None else event_services.alias_path',1)
a='''        self.command_interpreter = CommandInterpreter(
            self.system_config,
            self.command_aliases_path,
            allowed_symbols_provider=self._allowed_symbols,
        )'''
b='''        self.command_interpreter = (CommandInterpreter(
            self.system_config,
            self.command_aliases_path,
            allowed_symbols_provider=self._allowed_symbols,
        ) if event_services is None else event_services.interpreter(self))'''
assert s.count(a)==1;s=s.replace(a,b)
for name in ('staff','_config_chain_event_staff','_special_event_staff'):
    a=f'self.{name} = StaffClientV2('
    b=f'self.{name} = event_services.staff if event_services is not None else StaffClientV2('
    assert s.count(a)==1;s=s.replace(a,b,1)
a='''        self._load_private_state()
        self._load_timed_chain_state()
        self._load_fvg_created_watch_state()
        self._load_active_children_state()
        self._scan_special_strategies()'''
b='''        if event_services is None:
            self._load_private_state()
            self._load_timed_chain_state()
            self._load_fvg_created_watch_state()
            self._load_active_children_state()
        self._scan_special_strategies()'''
assert s.count(a)==1;s=s.replace(a,b)
for expression in ('self._state_path','self._config_chain_state_path','self._fvg_created_watch_state_path',
                   'self._active_children_state_path'):
    s=s.replace(expression+'.is_file()',f'domain_memory.exists({expression})')
p.write_text(s,encoding='utf-8')
print('Composer startup dependency injection added; polling defaults retained')
