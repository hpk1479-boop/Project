"""Only dedicated Generic output/cache/approval roots can be written."""
from pathlib import Path
from .contracts import ROOT, GenericError


def plain_path(value):
    path = Path(value).expanduser()
    # Reject before stat/resolve: even a denied path must not be explored.
    if any(part.casefold() == 'program' for part in path.parts):
        raise GenericError('E_CAPABILITY_DENIED','operational application path forbidden')
    if not path.is_absolute(): path = ROOT / path
    path = path.absolute()
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
            raise GenericError('E_CAPABILITY_DENIED', 'symlink/junction: ' + str(item))
    return path.resolve()


def project_reference(value):
    """Persist internal locations relative to ROOT; keep external resources intact."""
    path = plain_path(value)
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def internal_path(value):
    """Read portable references and old internal references without rewriting evidence.

    Only known Generic-owned data roots may be relocated. Never apply to terminal
    executable/data-root settings or arbitrary output paths.
    """
    path = Path(value).expanduser()
    # Reject before stat/resolve: even a denied path must not be explored.
    if any(part.casefold() == 'program' for part in path.parts):
        raise GenericError('E_CAPABILITY_DENIED','operational application path forbidden')
    if path.is_absolute() and not path.is_relative_to(ROOT):
        roots = {'generic_cache','generic_runs','generic_baseline','generic_approvals'}
        matches = [i for i,p in enumerate(path.parts) if p in roots]
        if matches: path = ROOT.joinpath(*path.parts[matches[-1]:])
    return plain_path(path)


def output_path(value, area='generic_runs', *, fresh=True):
    path = plain_path(value)
    base = plain_path(ROOT / area)
    if area not in ('generic_runs','generic_cache','generic_approvals','generic_baseline') or not path.is_relative_to(base) or path == base:
        raise GenericError('E_CAPABILITY_DENIED', 'output root: ' + str(path))
    if fresh and path.exists(): raise GenericError('E_RESULT_INTEGRITY', 'output exists')
    return path
