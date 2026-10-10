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


