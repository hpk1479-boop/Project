"""Part1 live-control API shared by the integrated web UI and independent CLI."""
# -*- coding: utf-8 -*-
import importlib.util
import json
import logging
import sys
import os
import re
import time
import subprocess
import threading
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ICON_NAME = "THE_STAFF_OF_MOSES.ico"
LOG_DIR = BASE_DIR / "logs"
PYCACHE_DIR = LOG_DIR / "pycache"
PROGRAMS = [("EVENT ENGINE", "event_host.py", 0.0)]

# 예전 이름으로 실행 중인 프로세스. 새 프로그램과 동시에 돌면 같은 명령을 두 번 처리하므로
# 전체 시작 전에 종료하고, 전체 종료 때도 함께 종료합니다. (strategy_TREND.py는 호환 래퍼)
LEGACY_PROGRAMS = ()

# manager_KIM은 아래 두 파일을 별도 프로세스로 실행하지 않고 Python 모듈로 import합니다.
MANAGER_REQUIRED_MODULES = (
    "command_interpreter.py",
    "watch_orchestrator.py",
)

# [전략 설정] SPECIAL별 실행 여부 + 최종 OZ 트리거 슬롯 저장 파일.
SPECIAL_SETTINGS_PATH = BASE_DIR / "special_settings.json"
SPECIAL_SETTINGS_VERSION = 1

class SearchError(RuntimeError):
    pass


class EngineConflictError(ValueError):
    """The caller must confirm the listed process identities before restart."""

    def __init__(self, engines, message=None):
        self.engines = engines
        super().__init__(message or '이전에 작동중인 엔진을 종료하고 다시 시작하시겠습니까?')


class LiveStartResult(tuple):
    """Keep the independent (ok, message) contract and expose restart warnings."""

    def __new__(cls, ok, message, *, warnings=()):
        result = super().__new__(cls, (ok, message))
        result.warnings = list(warnings)
        return result


def process_control():
    """Lazy public Part1 process API, also used by the integrated web bridge."""
    root = str(BASE_DIR.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    from Part1 import engine_processes
    return engine_processes


class SpecialSettingsError(ValueError):
    """An existing strategy settings file cannot be used safely."""

    def __init__(self, message, *, kind="structure"):
        self.kind = kind
        super().__init__(message)

def _inside_base(path: Path) -> bool:
    """해결된 실제 경로가 SYSTEM CONTROL의 메인 폴더 내부인지 확인합니다."""
    try:
        path.resolve().relative_to(BASE_DIR.resolve())
        return True
    except (ValueError, OSError):
        return False

def _dedupe_paths(paths):
    seen = set()
    out = []
    for path in paths:
        try:
            key = str(path.resolve()).casefold()
        except OSError:
            continue
        if key in seen or not _inside_base(path):
            continue
        seen.add(key)
        out.append(path)
    return out

def _variant_matches_in_dir(directory: Path, filename: str):
    """기존 호환성: 정확한 파일이 없을 때 이름 뒤에 버전표기가 붙은 파일을 찾습니다."""
    target = Path(filename)
    stem = target.stem.casefold()
    suffix = target.suffix.casefold()
    matches = []
    try:
        for path in directory.iterdir():
            if not path.is_file():
                continue
            if path.suffix.casefold() != suffix:
                continue
            if path.stem.casefold().startswith(stem):
                matches.append(path)
    except OSError:
        pass
    return _dedupe_paths(matches)

def _descendant_matches(filename: str, exact_only: bool):
    """BASE_DIR 아래만 재귀검색합니다. 상위 폴더는 어떤 경우에도 검색하지 않습니다."""
    target = Path(filename)
    stem = target.stem.casefold()
    suffix = target.suffix.casefold()
    matches = []
    try:
        for path in BASE_DIR.rglob("*"):
            if not path.is_file() or path.parent == BASE_DIR:
                continue
            if not _inside_base(path):
                continue
            if exact_only:
                if path.name.casefold() == target.name.casefold():
                    matches.append(path)
            else:
                if path.suffix.casefold() == suffix and path.stem.casefold().startswith(stem):
                    matches.append(path)
    except OSError:
        pass
    return _dedupe_paths(matches)

def resolve_file_downward(filename: str):
    """
    검색 순서:
      1) 메인 폴더의 정확한 파일명
      2) 메인 폴더의 버전표기 변형 파일명
      3) 하위 폴더 전체의 정확한 파일명
      4) 하위 폴더 전체의 버전표기 변형 파일명

    하위 검색에서 같은 우선순위의 후보가 여러 개면 임의 선택하지 않습니다.
    BASE_DIR의 상위 폴더는 절대 검색하지 않습니다.
    """
    direct = BASE_DIR / filename
    if direct.is_file() and _inside_base(direct):
        return direct

    root_variants = [p for p in _variant_matches_in_dir(BASE_DIR, filename)
                     if p.name.casefold() != Path(filename).name.casefold()]
    if len(root_variants) == 1:
        return root_variants[0]
    if len(root_variants) > 1:
        raise SearchError(
            f"메인 폴더에 '{filename}' 후보가 여러 개 있습니다: "
            + ", ".join(p.name for p in root_variants)
        )

    exact = _descendant_matches(filename, exact_only=True)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        rels = [str(p.relative_to(BASE_DIR)) for p in exact]
        raise SearchError(f"'{filename}'가 하위 폴더에 여러 개 있습니다: " + " | ".join(rels))

    variants = _descendant_matches(filename, exact_only=False)
    variants = [p for p in variants if p.name.casefold() != Path(filename).name.casefold()]
    if len(variants) == 1:
        return variants[0]
    if len(variants) > 1:
        rels = [str(p.relative_to(BASE_DIR)) for p in variants]
        raise SearchError(f"'{filename}' 후보가 하위 폴더에 여러 개 있습니다: " + " | ".join(rels))
    return None

def resolve_script_path(script_name):
    return resolve_file_downward(script_name)

def resolve_manager_modules():
    found = {}
    errors = []
    for name in MANAGER_REQUIRED_MODULES:
        try:
            path = resolve_file_downward(name)
        except SearchError as e:
            errors.append(str(e))
            continue
        if path is None:
            errors.append(f"필수 모듈 없음: {name}")
        else:
            found[name] = path
    return found, errors

def missing_manager_modules():
    _found, errors = resolve_manager_modules()
    return errors

def resolve_icon_path():
    try:
        return resolve_file_downward(ICON_NAME)
    except SearchError:
        return None

def discover_live_specials():
    """Registered Recipe presets, in registry order; no source-file scan."""
    program = BASE_DIR / 'program'
    if str(program) not in sys.path: sys.path.insert(0, str(program))
    from strategy_recipe.registry import list_presets
    return list(list_presets('Part1'))

def skipped_special_files():
    """SPECIAL files left out because they could not be read; the other strategies still run."""
    program = BASE_DIR / 'program'
    if str(program) not in sys.path: sys.path.insert(0, str(program))
    from strategy_recipe.registry import skipped_builtins
    return skipped_builtins()

def user_strategy_ids():
    """The installation's own strategies (스페셜/내 전략): off until the saved settings turn them on."""
    program = BASE_DIR / 'program'
    if str(program) not in sys.path: sys.path.insert(0, str(program))
    from strategy_recipe.registry import user_strategies
    return user_strategies()
_OZ_PROFILES = None

def load_oz_profiles():
    """program 폴더의 공용 OZ 프로필 어휘(oz_profiles.py)를 파일 경로로 불러옵니다."""
    global _OZ_PROFILES
    if _OZ_PROFILES is not None:
        return _OZ_PROFILES
    try:
        path = resolve_file_downward("oz_profiles.py")
    except SearchError:
        return None
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location("_oz_control_profiles", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _OZ_PROFILES = module
    return module

def special_code_default_trigger(name):
    """The registry's final OZ default; never execute preset Python source."""
    program = BASE_DIR / 'program'
    if str(program) not in sys.path: sys.path.insert(0, str(program))
    from strategy_recipe.registry import default_settings
    return default_settings(name)[0]
def load_special_settings():
    """파일이 없을 때만 코드 기본값을 사용하고, 기존 파일 손상은 보고합니다."""
    try:
        text = SPECIAL_SETTINGS_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except UnicodeDecodeError as exc:
        raise SpecialSettingsError(
            "전략 설정 파일의 문자 인코딩이 올바르지 않습니다. UTF-8 설정 파일을 확인하세요.",
            kind="encoding",
        ) from exc
    except OSError as exc:
        raise SpecialSettingsError(
            "전략 설정 파일을 읽을 수 없습니다. 접근 권한과 파일 상태를 확인하세요.",
            kind="read",
        ) from exc
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SpecialSettingsError(
                    f"전략 설정 파일 형식이 올바르지 않습니다. 중복된 항목이 있습니다: {key}"
                )
            result[key] = value
        return result

    try:
        raw = json.loads(text, object_pairs_hook=unique_object)
    except json.JSONDecodeError as exc:
        raise SpecialSettingsError(
            f"전략 설정 파일의 JSON 형식이 올바르지 않습니다. {exc.lineno}행 {exc.colno}열을 확인하세요.",
            kind="json",
        ) from exc
    items = raw.get("specials") if isinstance(raw, dict) else None
    if not isinstance(items, dict):
        raise SpecialSettingsError("전략 설정 파일 형식이 올바르지 않습니다. specials 항목은 객체여야 합니다.")
    names = set()
    for name, item in items.items():
        canonical_name = name.upper()
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", canonical_name) is None:
            raise SpecialSettingsError(f"전략 설정 ID 형식이 올바르지 않습니다: {name}")
        if canonical_name in names:
            raise SpecialSettingsError(f"전략 설정 파일 형식이 올바르지 않습니다. 같은 전략이 중복되었습니다: {canonical_name}")
        names.add(canonical_name)
        if not isinstance(item, dict):
            raise SpecialSettingsError(f"전략 설정 파일 형식이 올바르지 않습니다. {name} 항목은 객체여야 합니다.")
        if "enabled" in item and type(item["enabled"]) is not bool:
            raise SpecialSettingsError(f"전략 설정 파일 형식이 올바르지 않습니다. {name} 실행 여부는 참/거짓 값이어야 합니다.")
        if item.get("trigger") is not None and not isinstance(item["trigger"], str):
            raise SpecialSettingsError(f"전략 설정 파일 형식이 올바르지 않습니다. {name} OZ 트리거는 문자열이어야 합니다.")
        times = item.get("time_filters")
        if times is not None and not isinstance(times, dict):
            raise SpecialSettingsError(f"전략 설정 파일 형식이 올바르지 않습니다. {name} 거래시간은 세션별 객체여야 합니다.")
        for session, value in (times or {}).items():
            # The engine's existing bool shorthand and omitted enabled/default time values remain valid.
            if isinstance(value, bool):
                continue
            if not isinstance(value, dict):
                raise SpecialSettingsError(f"전략 설정 파일 형식이 올바르지 않습니다. {name}.{session} 거래시간은 객체여야 합니다.")
            if "enabled" in value and type(value["enabled"]) is not bool:
                raise SpecialSettingsError(f"전략 설정 파일 형식이 올바르지 않습니다. {name}.{session} 사용 여부는 참/거짓 값이어야 합니다.")
    profiles = load_oz_profiles()
    if profiles is None:raise SearchError("공용 OZ 프로필 파일을 찾지 못했습니다")
    program = str(Path(profiles.__file__).parent)
    if program not in sys.path:sys.path.insert(0, program)
    from special_time_slot import _hhmm
    for name, item in items.items():
        for session, value in (item.get("time_filters") or {}).items():
            if isinstance(value, dict):
                try:
                    for field in ("start", "end"):
                        _hhmm(value.get(field))
                except ValueError as exc:
                    raise SpecialSettingsError(
                        f"전략 설정 파일 형식이 올바르지 않습니다. {name}.{session} 시각은 HH:MM 또는 HHMM 형식이어야 합니다."
                    ) from exc
    from oz_profile_loader import load_saved_oz
    return load_saved_oz(items, kind="specials", path="specials")

def save_special_settings(settings):
    oz_profiles = load_oz_profiles()
    if oz_profiles is None:raise SearchError("공용 OZ 프로필 파일을 찾지 못했습니다")
    settings = oz_profiles.normalize_special_settings(settings)
    payload = {"version": SPECIAL_SETTINGS_VERSION, "specials": settings}
    tmp = SPECIAL_SETTINGS_PATH.with_name(SPECIAL_SETTINGS_PATH.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, SPECIAL_SETTINGS_PATH)

def special_trigger_env(settings):
    """manager_KIM에 넘길 트리거 슬롯(JSON). 코드 기본값과 다른 입력만 담습니다."""
    oz_profiles = load_oz_profiles()
    if oz_profiles is None:raise SearchError("공용 OZ 프로필 파일을 찾지 못했습니다")
    settings = oz_profiles.normalize_special_settings({name: item for name, item in (settings or {}).items() if not item.get("load_error")})
    triggers = {
        name: item["trigger"]
        for name, item in (settings or {}).items()
        if item.get("trigger")
    }
    return json.dumps(triggers, ensure_ascii=False, sort_keys=True) if triggers else ""

CREATE_NO_WINDOW = 0x08000000

def get_python_exe():
    current = Path(sys.executable)
    if current.name.lower() == "pythonw.exe":
        candidate = current.with_name("python.exe")
        if candidate.exists():
            return str(candidate)
    return str(current)

PYTHON_EXE = get_python_exe()

def ps_escape(text):
    return str(text).replace("'", "''")

def find_process_ids(script_path):
    target = str(Path(script_path).resolve()).casefold()
    return [row['pid'] for row in process_control().find_engine_instances()
            if str(row['script'].resolve()).casefold() == target]

def find_program_process_ids(script_name):
    try:
        path = resolve_script_path(script_name)
    except SearchError:
        return []
    if path is None:
        return []
    return sorted(set(find_process_ids(path)))

def start_program(script_name, enabled_specials=None, special_triggers="", special_times="",
                  *, restart=False, expected_engines=None, ui_owner=None):
    try:
        script = resolve_script_path(script_name)
    except SearchError as e:
        return False, f"파일 중복: {e}"
    if script is None:
        return False, f"파일 없음: {script_name}"

    module_paths = {}
    if script_name == "event_host.py":
        if str(script.parent) not in sys.path:
            sys.path.insert(0, str(script.parent))
        from event_selection import strategy_dependencies
        requested = set(discover_live_specials()) if enabled_specials is None else set(enabled_specials)
        unsupported = requested - set(strategy_dependencies())
        if unsupported:
            return False, "기존 로더/의존 선언 미지원: " + ", ".join(sorted(unsupported))
        module_paths, module_errors = resolve_manager_modules()
        if module_errors:
            return False, " / ".join(module_errors)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        PYCACHE_DIR.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["PYTHONPYCACHEPREFIX"] = str(PYCACHE_DIR)
        env["OZ_SYSTEM_ROOT"] = str(BASE_DIR)
        env["OZ_SYSTEM_LOG_DIR"] = str(LOG_DIR)

        if script_name == "event_host.py" and enabled_specials is not None:
            env["OZ_SPECIAL_SELECTION_ACTIVE"] = "1"
            env["OZ_ENABLED_SPECIALS"] = ",".join(sorted(enabled_specials))
        if script_name == "event_host.py":
            # [전략 설정] SPECIAL별 최종 OZ 트리거 슬롯. 비어 있으면 모든 SPECIAL이 코드 기본값을 씁니다.
            env["OZ_SPECIAL_TRIGGERS"] = special_triggers or ""
            env["OZ_SPECIAL_TIME_FILTERS"] = special_times or ""
        run_id = secrets.token_hex(24)
        env['MOSES_ENGINE_RUN_ID'] = run_id
        # An independent CLI is short lived and must never become the owner.
        env.pop('MOSES_UI_OWNER_PID', None)
        env.pop('MOSES_UI_OWNER_CREATED', None)
        if ui_owner is not None:
            if (not isinstance(ui_owner, dict) or type(ui_owner.get('pid')) is not int
                    or ui_owner['pid'] <= 0):
                raise ValueError('웹 UI 프로세스 수명 정보를 확인하세요.')
            created = int(ui_owner.get('created'))
            if created <= 0:
                raise ValueError('웹 UI 프로세스 수명 정보를 확인하세요.')
            env['MOSES_UI_OWNER_PID'] = str(ui_owner['pid'])
            env['MOSES_UI_OWNER_CREATED'] = str(created)

        # manager의 보조 모듈이 다른 하위 폴더에 있어도 import 가능하도록
        # 컨트롤러가 실제로 찾은 모듈 폴더만 PYTHONPATH에 추가합니다.
        if module_paths:
            import_dirs = []
            for path in module_paths.values():
                folder = str(path.parent.resolve())
                if folder not in import_dirs:
                    import_dirs.append(folder)
            old_pythonpath = env.get("PYTHONPATH", "")
            if old_pythonpath:
                import_dirs.append(old_pythonpath)
            env["PYTHONPATH"] = os.pathsep.join(import_dirs)

        processes = process_control()
        with processes.lifecycle_lock():
            restart_warnings = []
            existing = processes.find_engine_instances()
            try:
                processes.confirm_restart(existing, BASE_DIR, restart=restart,
                                           expected_engines=expected_engines)
            except processes.EngineConflictError as exc:
                raise EngineConflictError(exc.engines, str(exc)) from exc
            if existing:
                stopped = processes.stop_engine_instances(existing)
                if not stopped['ok']:
                    return False, stopped['message']
                restart_warnings = stopped.get('warnings', [])
                # Older controllers do not honor this mutex. Never stop an
                # engine that appeared after the user's confirmation.
                remaining = processes.find_engine_instances()
                if remaining:
                    raise EngineConflictError(processes.public_instances(remaining, BASE_DIR),
                                              '종료 중 새 엔진이 실행되었습니다. 엔진 목록을 다시 확인하세요.')
            process = subprocess.Popen(
                [PYTHON_EXE, "-X", f"pycache_prefix={PYCACHE_DIR}", str(script)],
                cwd=str(BASE_DIR), creationflags=CREATE_NO_WINDOW, env=env,
            )
            try:
                ready, message, instance = processes.wait_until_ready(process, BASE_DIR, run_id)
            except Exception as exc:
                cleanup = processes.cleanup_spawned_process(process, BASE_DIR, run_id)
                message = f'엔진 준비 상태 확인에 실패했습니다. {exc}'
                if not cleanup['ok'] or cleanup.get('forced_pids'):
                    message += ' / ' + cleanup['message']
                if restart_warnings:
                    message += ' / ' + ' / '.join(restart_warnings)
                return LiveStartResult(False, message,
                    warnings=[*restart_warnings, *cleanup.get('warnings', [])])
            if not ready:
                cleanup_warnings = []
                if process.poll() is None:
                    cleanup = processes.stop_engine_instances([instance])
                    cleanup_warnings = cleanup.get('warnings', [])
                    if not cleanup['ok']:
                        message += ' / ' + cleanup['message']
                    elif cleanup.get('forced_pids'):
                        message += ' / 준비 실패 엔진을 제한시간 후 강제 종료했습니다.'
                if restart_warnings:
                    message += ' / ' + ' / '.join(restart_warnings)
                return LiveStartResult(False, message, warnings=[*restart_warnings, *cleanup_warnings])
            rel = script.relative_to(BASE_DIR)
            message = f"{message} · {rel}"
            if restart_warnings:
                message += ' / ' + ' / '.join(restart_warnings)
            return LiveStartResult(True, message, warnings=restart_warnings)
    except EngineConflictError:
        raise
    except Exception as e:
        return False, f"실행 실패: {e}"

def stop_program(script_name):
    try:
        script = resolve_script_path(script_name)
    except SearchError as e:
        return False, f"파일 중복: {e}"
    if script is None:
        return True, "파일 없음 / 실행 중 아님"

    processes = process_control()
    target = str(script.resolve()).casefold()
    with processes.lifecycle_lock():
        existing = [row for row in processes.find_engine_instances()
                    if str(row['script'].resolve()).casefold() == target]
        if not existing:
            return True, '실행 중 아님'
        stopped = processes.stop_engine_instances(existing)
        return stopped['ok'], stopped['message']


def start_live(*, restart=False, expected_engines=None, ui_owner=None):
    """Start the live engine using Part1's saved SPECIAL/OZ/time settings."""
    settings = load_special_settings()
    available = set(discover_live_specials())
    skipped = skipped_special_files()
    excluded = {row['id'] for row in skipped if row['id']}
    if settings is not None:
        # A skipped file's saved settings stay in the file; they are not passed to the engine.
        settings = {name: item for name, item in settings.items() if name not in excluded - available}
    unknown = set(settings or {}) - available
    if unknown:
        raise SpecialSettingsError(
            "전략 설정 파일의 전략 목록이 현재 등록된 목록과 다릅니다. "
            "전략 설정을 확인한 뒤 다시 저장하세요. 미등록 항목: " + ", ".join(sorted(unknown)),
            kind="registry",
        )
    own = user_strategy_ids()
    selection = None if settings is None else {
        name for name in available
        if settings.get(name, {}).get("enabled", name not in own)
    }
    triggers = special_trigger_env(settings)
    times = json.dumps({name: item["time_filters"] for name, item in (settings or {}).items()
                        if item.get("time_filters") is not None}, ensure_ascii=False)
    options = {}
    if restart is not False or expected_engines is not None:
        options.update(restart=restart, expected_engines=expected_engines)
    if ui_owner is not None:
        options['ui_owner'] = ui_owner
    result = start_program("event_host.py", enabled_specials=selection,
                           special_triggers=triggers, special_times=times, **options)
    notices = [row['notice'] for row in skipped]
    if not notices:
        return result
    return LiveStartResult(result[0], ' / '.join([result[1], *notices]),
                           warnings=[*getattr(result, 'warnings', ()), *notices])


def stop_live(*, all_copies=False):
    """Request a saved shutdown; force only if the shutdown deadline expires."""
    if all_copies:
        result = process_control().stop_all_engines()
        return result['ok'], result['message']
    return stop_program("event_host.py")


def list_live_engines():
    """List running live engines from every copy without revealing root paths."""
    return process_control().list_live_engines(BASE_DIR)


def live_status():
    """Report this Part1 engine's processes without starting the engine or a UI."""
    pids = find_program_process_ids("event_host.py")
    settings = load_special_settings()
    available = discover_live_specials()
    own = user_strategy_ids()
    enabled = [name for name in available
               if (settings or {}).get(name, {}).get("enabled", name not in own)]
    return {"running": bool(pids), "pids": pids, "enabled_specials": enabled}


def main(argv=None):
    """Independent console entry point; Part1 never needs Part3 to run."""
    import argparse
    parser = argparse.ArgumentParser(description="Part1 실시간 감시 독립 실행·조회·종료")
    parser.add_argument("action", choices=("start", "status", "stop"))
    args = parser.parse_args(argv)
    try:
        if args.action == "status":
            result = {"ok": True, **live_status()}
        else:
            ok, message = (start_live() if args.action == "start" else stop_live())
            result = {"ok": ok, "message": message}
    except Exception as exc:
        result = {"ok": False, "message": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
