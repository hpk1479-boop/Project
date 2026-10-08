"""Compile expiry-limited MT5 copies without writing to application sources.

The installed EX5 files need no DLL, server, or machine restriction.  A small
temporary-file timestamp probe runs in OnInit only.  Unlike TimeLocal/TimeGMT,
filesystem timestamps represent real time even inside the Strategy Tester.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid


MT5_PROGRAMS = (
    "THE_STAFF_OF_MOSES",
    "PRICE_of_Moses",
    "RSI_of_Moses",
    "STO_of_Moses",
    "DI_of_Moses",
)
_INIT = re.compile(r"\bint\s+OnInit\s*\(\s*(?:void\s*)?\)\s*\{")
_PREFIX = "MosesDeployment96"
# The builder date picker encodes its selected calendar midnight as Korean UTC.
# Execution compares that calendar value to each PC's actual local wall clock.
_BUILDER_CALENDAR_OFFSET_SECONDS = 9 * 3600


class MT5BuildError(RuntimeError):
    """A deployment build is incomplete; old EX5 files must not be used."""


def _valid_epoch(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 32535216000:
        raise ValueError("MT5 expiry must be a positive UTC epoch second before year 3001")
    return value


def expiry_allows(now_local_wall: int, expiry_epoch_utc: int) -> bool:
    """Compare real local calendar seconds with the selected exclusive midnight.

    ``now_local_wall`` encodes PC-local year/month/day/hour/minute/second as if
    UTC.  The date-picker input retains its UTC representation for the build
    API; adding the fixed Korean picker offset restores its calendar fields.
    Neither actual execution time zone nor historical tick time is used to
    change the selected calendar date.
    """
    cutoff_local = _valid_epoch(expiry_epoch_utc) + _BUILDER_CALENDAR_OFFSET_SECONDS
    return 0 < now_local_wall < cutoff_local


def expiry_helper(expiry_epoch_utc: int, program_name: str, newline: str = "\n") -> str:
    """Generate a one-shot real filesystem clock probe with no licence UI.

    File times are actual PC-local wall-clock datetime values in MT5, including
    inside the Strategy Tester.  TimeLocal/TimeGMT/TimeGMTOffset are deliberately
    not used: Tester time APIs can be historical (or zero in math mode).
    The builder's selected next-day midnight is restored as calendar fields;
    all PCs expire at that date's local midnight, matching the Python licence.
    FileFlush and close occur before inspecting the modification timestamp.
    A freshly written modification time avoids NTFS creation-time tunnelling.
    """
    expiry_local = _valid_epoch(expiry_epoch_utc) + _BUILDER_CALENDAR_OFFSET_SECONDS
    if program_name not in MT5_PROGRAMS and program_name != "clock_probe":
        raise ValueError("Unexpected MT5 deployment program name")
    lines = f"""
// BEGIN generated deployment gate; application calculation code is unchanged.
bool {_PREFIX}Allowed()
{{
   string probe="__moses_{program_name}_"+IntegerToString(ChartID())+"_"+
      IntegerToString((long)GetTickCount64())+"_"+
      IntegerToString((long)GetMicrosecondCount())+".tmp";
   ResetLastError();
   int handle=FileOpen(probe,FILE_WRITE|FILE_BIN);
   if(handle==INVALID_HANDLE) {{ ResetLastError(); return false; }}
   uint written=FileWriteInteger(handle,1,CHAR_VALUE);
   FileFlush(handle);
   FileClose(handle);
   long local_file_time=FileGetInteger(probe,FILE_MODIFY_DATE);
   bool deleted=FileDelete(probe);
   ResetLastError();
   if(written!=1 || local_file_time<=0 || !deleted) return false;
   return (local_file_time>0 && local_file_time<{expiry_local});
}}
// END generated deployment gate.

"""
    return lines.replace("\n", newline)


def licensed_source(source: bytes, expiry_epoch_utc: int, program_name: str) -> bytes:
    """Add only a gate at the beginning of the copied OnInit implementation."""
    _valid_epoch(expiry_epoch_utc)
    has_bom = source.startswith(b"\xef\xbb\xbf")
    text = source.decode("utf-8-sig")
    if _PREFIX in text:
        raise MT5BuildError("MT5 input already contains a generated deployment gate")
    matches = list(_INIT.finditer(text))
    if len(matches) != 1:
        raise MT5BuildError("MT5 source must contain exactly one int OnInit")
    newline = "\r\n" if "\r\n" in text else "\n"
    position = matches[0].end()
    gate = f"{newline}   if(!{_PREFIX}Allowed()) return INIT_FAILED;"
    modified = text[:position] + gate + text[position:]
    result = expiry_helper(expiry_epoch_utc, program_name, newline) + modified
    return (b"\xef\xbb\xbf" if has_bom else b"") + result.encode("utf-8")


def find_metaeditor(metaeditor_path: str | Path | None = None) -> Path:
    """Resolve runtime compiler location without saving an absolute path."""
    chosen = metaeditor_path or os.environ.get("MOSES_METAEDITOR")
    if chosen:
        compiler = Path(chosen).expanduser().resolve()
        if compiler.is_file():
            return compiler
        raise MT5BuildError("Selected MetaEditor executable was not found")
    found = shutil.which("MetaEditor64.exe") or shutil.which("MetaEditor.exe")
    if found:
        return Path(found).resolve()
    candidates: list[Path] = []
    for name in ("ProgramFiles", "ProgramFiles(x86)"):
        folder = os.environ.get(name)
        if not folder:
            continue
        root = Path(folder)
        candidates.append(root / "MetaTrader 5" / "MetaEditor64.exe")
        if root.is_dir():
            candidates.extend(p / "MetaEditor64.exe" for p in root.iterdir()
                              if p.is_dir() and "metatrader" in p.name.casefold())
    if os.name == "nt":
        import winreg
        key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for access in (winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
                           winreg.KEY_READ | winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, key_path, 0, access) as parent:
                        for index in range(winreg.QueryInfoKey(parent)[0]):
                            try:
                                child_name = winreg.EnumKey(parent, index)
                                with winreg.OpenKey(parent, child_name) as child:
                                    display = winreg.QueryValueEx(child, "DisplayName")[0]
                                    if "metatrader" not in str(display).casefold():
                                        continue
                                    location = winreg.QueryValueEx(child, "InstallLocation")[0]
                                    candidates.append(Path(location) / "MetaEditor64.exe")
                            except OSError:
                                continue
                except OSError:
                    continue
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise MT5BuildError("MetaEditor is required to regenerate the five licensed EX5 files")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decode_log(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    if b"\x00" in raw[:80]:
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


def _portable_log(text: str, paths: tuple[Path, ...]) -> str:
    # Compiler messages put absolute source paths into logs.  Persist only the
    # location relative to staging, then scrub any unrelated machine paths.
    for path in sorted(paths, key=lambda p: len(str(p)), reverse=True):
        text = re.sub(re.escape(str(path)), ".", text, flags=re.IGNORECASE)
        text = re.sub(re.escape(path.as_posix()), ".", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)\b[A-Z]:[\\/][^\r\n\"<>]*", "[external path]", text)
    text = re.sub(r"\\\\[^\r\n\"<>]*", "[external path]", text)
    return text


def build_mt5(project_root: Path | str, stage_dir: Path | str,
              expiry_epoch_utc: int, metaeditor_path: Path | str | None = None) -> list[Path]:
    """Regenerate all five EX5 files; only returned files belong in payload.

    Source copies and sanitised compile receipts live under _mt5_build.  The
    source tree is read-only and any overlapping staging location is rejected.
    Existing EX5 files from the application are never read or reused.
    """
    expiry = _valid_epoch(expiry_epoch_utc)
    project = Path(project_root).resolve()
    stage = Path(stage_dir).resolve()
    original = project / "Part1" / "program" / "MT5"
    if not original.is_dir():
        raise MT5BuildError("Application MT5 source folder was not found")
    if stage == original or stage.is_relative_to(original) or original.is_relative_to(stage):
        raise MT5BuildError("MT5 staging must be separate from the application source folder")
    compiler = find_metaeditor(metaeditor_path)
    original_files = [original / f"{name}.mq5" for name in MT5_PROGRAMS]
    original_files.extend(sorted(original.glob("*.mqh")))
    if any(not path.is_file() for path in original_files):
        raise MT5BuildError("One or more required MT5 source files are missing")
    fingerprints = {path.relative_to(project).as_posix(): _sha256(path)
                    for path in original_files}
    work = stage / "_mt5_build" / uuid.uuid4().hex
    work.mkdir(parents=True)
    for path in original_files:
        if path.suffix.casefold() == ".mqh":
            shutil.copyfile(path, work / path.name)
    outputs: list[Path] = []
    receipts: list[dict] = []
    try:
        for name in MT5_PROGRAMS:
            source = original / f"{name}.mq5"
            generated = work / source.name
            generated.write_bytes(licensed_source(source.read_bytes(), expiry, name))
            binary = generated.with_suffix(".ex5")
            log = generated.with_suffix(".log")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                result = subprocess.run(
                    [str(compiler), f"/compile:{generated.name}", "/log"],
                    cwd=work, capture_output=True, timeout=180,
                    creationflags=flags, check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                if log.is_file():
                    log.write_text(_portable_log(_decode_log(log.read_bytes()),
                                                  (work, project, compiler.parent)),
                                   encoding="utf-8")
                raise MT5BuildError(f"MetaEditor could not compile {name}") from exc
            raw_log = _decode_log(log.read_bytes()) if log.is_file() else ""
            clean_log = _portable_log(raw_log, (work, project, compiler.parent))
            log.write_text(clean_log, encoding="utf-8")
            # MetaEditor builds have used non-zero process exits on success;
            # the explicit compiler result and a fresh binary are authoritative.
            success = re.search(r"\bResult:\s*0\s+errors\b", raw_log, re.IGNORECASE)
            if not success or not binary.is_file() or binary.stat().st_size == 0:
                raise MT5BuildError(f"MetaEditor compilation failed for {name}; see its build receipt")
            folder = "EA" if name == MT5_PROGRAMS[0] else "Indicators"
            destination = stage / "MT5" / folder / binary.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(binary, destination)
            outputs.append(destination)
            receipts.append({
                "source": source.relative_to(project).as_posix(),
                "source_sha256": fingerprints[source.relative_to(project).as_posix()],
                "generated_sha256": _sha256(generated),
                "output": destination.relative_to(stage).as_posix(),
                "output_sha256": _sha256(destination),
                "compile_log": log.relative_to(stage).as_posix(),
                "compiler_exit": result.returncode,
            })
    finally:
        changed = [relative for relative, digest in fingerprints.items()
                   if _sha256(project / relative) != digest]
        manifest = {
            "expiry_epoch_utc": expiry,
            "expiry_local_wall": expiry + _BUILDER_CALENDAR_OFFSET_SECONDS,
            "runtime_clock": "actual_pc_local_calendar",
            "original_sources_unchanged": not changed,
            "changed_original_sources": changed,
            "sources": fingerprints,
            "programs": receipts,
        }
        (work / "compile_receipt.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if changed:
            raise MT5BuildError("Application MT5 sources changed during deployment generation")
    return outputs
