"""Remove only regenerable deployment files after the build worker exits.

The caller must hold the deployment lock and wait for the worker to exit before
calling this module from a Python installation outside ``.buildenv``.
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import time
from typing import Callable


_TARGET_NAMES = (".work", "prerequisites", ".buildenv")
_MAX_ATTEMPTS = 4
_RETRY_DELAY = 0.35
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class UnsafeCleanupPath(ValueError):
    """A path is not an ordinary directory inside the deployment folder."""


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _ordinary_directory(path: Path) -> None:
    info = path.lstat()
    if _is_reparse(info):
        raise UnsafeCleanupPath(f"연결된 경로는 정리하지 않습니다: {path.name}")
    if not stat.S_ISDIR(info.st_mode):
        raise UnsafeCleanupPath(f"폴더가 아닌 경로는 정리하지 않습니다: {path.name}")


def _check_ancestors(path: Path) -> None:
    for part in (path, *path.parents):
        if _is_reparse(part.lstat()):
            raise UnsafeCleanupPath(f"연결된 상위 경로는 정리하지 않습니다: {part.name}")


def _deployment_directory(root: Path) -> Path:
    # Check the supplied path before resolve() can conceal a junction/symlink.
    absolute = root.absolute()
    _check_ancestors(absolute)
    _ordinary_directory(absolute)
    canonical_root = absolute.resolve(strict=True)
    install = canonical_root / "통합설치"
    _ordinary_directory(install)
    if install.resolve(strict=True) != install or install.parent != canonical_root:
        raise UnsafeCleanupPath("통합설치 폴더의 실제 위치가 일치하지 않습니다.")
    return install


def _validate_target(install: Path, target: Path) -> None:
    _ordinary_directory(install)
    if target.name not in _TARGET_NAMES or target.parent != install:
        raise UnsafeCleanupPath("허용된 정리 폴더가 아닙니다.")
    _ordinary_directory(target)
    if target.resolve(strict=True) != install / target.name:
        raise UnsafeCleanupPath("정리 폴더의 실제 위치가 일치하지 않습니다.")
    # Do not follow linked children. Refuse the entire target if any are found.
    pending = [target]
    while pending:
        folder = pending.pop()
        _ordinary_directory(folder)
        with os.scandir(folder) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if _is_reparse(info):
                    raise UnsafeCleanupPath(
                        f"연결된 하위 경로가 있어 정리하지 않습니다: {target.name}"
                    )
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))


def _within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def _retryable(error: OSError) -> bool:
    return error.errno in {
        errno.EACCES, errno.EPERM, errno.EBUSY, errno.ENOTEMPTY
    } or getattr(error, "winerror", None) in {5, 32, 33, 145}


def _remove_target(install: Path, target: Path) -> int:
    def remove_readonly(function, filename, exc_info):
        error = exc_info[1]
        path = Path(filename).absolute()
        if not isinstance(error, OSError) or not _retryable(error):
            raise error
        if not _within(path, target):
            raise UnsafeCleanupPath("읽기 전용 파일이 정리 폴더 밖에 있습니다.")
        # Check each component again before changing attributes or removing it.
        for part in (path, *path.parents):
            info = part.lstat()
            if _is_reparse(info):
                raise UnsafeCleanupPath("연결된 경로는 삭제하지 않습니다.")
            if part == install:
                break
        os.chmod(path, path.lstat().st_mode | stat.S_IWRITE)
        function(filename)

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        _validate_target(install, target)
        try:
            shutil.rmtree(target, onerror=remove_readonly)
            return attempt
        except OSError as error:
            if not target.exists():
                return attempt
            if attempt == _MAX_ATTEMPTS or not _retryable(error):
                raise
            time.sleep(_RETRY_DELAY * attempt)
    raise AssertionError("unreachable")


def _error_text(error: Exception) -> str:
    # Fixed-size result avoids growing a second cache or exposing large traces.
    return str(error).replace("\n", " ").replace("\r", " ")[:400]


def _save_result(install: Path, result: dict, emit: Callable[[str], None]) -> None:
    folder = install / ".buildtmp"
    try:
        _ordinary_directory(install)
        if not folder.exists() and not folder.is_symlink():
            folder.mkdir()
        _ordinary_directory(folder)
        if folder.resolve(strict=True) != install / ".buildtmp":
            raise UnsafeCleanupPath("정리 결과 폴더의 실제 위치가 일치하지 않습니다.")
        report = folder / "cleanup_result.json"
        if report.exists() or report.is_symlink():
            info = report.lstat()
            if _is_reparse(info) or not stat.S_ISREG(info.st_mode):
                raise UnsafeCleanupPath("연결된 정리 결과 파일은 쓰지 않습니다.")
        content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        report.write_text(content, encoding="utf-8")
    except (OSError, UnsafeCleanupPath) as error:
        emit(f"임시 파일 정리 기록 저장 실패: {_error_text(error)}")


def cleanup_build_files(root: str | os.PathLike[str], emit=print) -> dict:
    """Delete the exact three build caches and return a bounded status record.

    Missing folders count as successful cleanup. Unsafe or locked folders are
    preserved and reported while independent safe folders are still cleaned.
    This function never removes ``.keys``, releases, originals, or evidence.
    """
    result = {"schema": 1, "success": True, "targets": []}
    try:
        install = _deployment_directory(Path(root))
    except (OSError, UnsafeCleanupPath) as error:
        result["success"] = False
        for name in _TARGET_NAMES:
            result["targets"].append({
                "path": f"통합설치/{name}", "status": "blocked",
                "attempts": 0, "error": _error_text(error),
            })
        emit(f"임시 파일 정리 실패: {_error_text(error)}")
        return result

    executable = Path(sys.executable).absolute().resolve(strict=False)
    for name in _TARGET_NAMES:
        target = install / name
        row = {"path": f"통합설치/{name}", "status": "missing", "attempts": 0}
        try:
            # lstat() detects broken symlinks, unlike exists().
            try:
                target.lstat()
            except FileNotFoundError:
                result["targets"].append(row)
                continue
            _validate_target(install, target)
            if name == ".buildenv" and _within(executable, target):
                raise UnsafeCleanupPath("현재 실행 중인 Python 환경은 삭제하지 않습니다.")
            row["attempts"] = _remove_target(install, target)
            row["status"] = "removed"
            emit(f"임시 파일 정리 완료: 통합설치/{name}")
        except (OSError, UnsafeCleanupPath) as error:
            row["status"] = "blocked" if isinstance(error, UnsafeCleanupPath) else "failed"
            row["error"] = _error_text(error)
            result["success"] = False
            emit(f"임시 파일 정리 실패: 통합설치/{name} — {row['error']}")
        result["targets"].append(row)
    _save_result(install, result, emit)
    if result["success"]:
        emit("임시 파일 정리 완료")
    return result
