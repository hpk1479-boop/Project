"""Project-owned AI settings shared by independent WATCH and research clients."""
from __future__ import annotations

import json
from pathlib import Path


SETTINGS_PATH=Path('settings/ai_settings.json')
LEGACY_SETTINGS_PATH=Path('Part3/projects/ai_settings.json')


def read_settings(root):
    """Read incomplete settings; a corrupt current file never falls back silently."""
    root=Path(root).resolve()
    current=root/SETTINGS_PATH
    legacy=root/LEGACY_SETTINGS_PATH
    path=current if current.exists() else legacy
    if not path.resolve().is_relative_to(root):
        raise ValueError('공통 AI 설정 파일은 프로젝트 내부에 있어야 합니다.')
    if not path.exists():
        raw={}
    else:
        try:
            raw=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,UnicodeError,ValueError):
            raise ValueError('AI 설정 파일을 읽지 못했습니다. 설정 화면에서 확인하세요.') from None
        if not isinstance(raw,dict):
            raise ValueError('AI 설정 파일은 JSON 객체여야 합니다.')
    from .provider import configured_settings
    result=configured_settings(raw)
    result.setdefault('watch_enabled',True)
    return result


def masked_settings(data):
    """Never send the stored API key back to a browser or WATCH client."""
    from .security import redact
    result=redact(dict(data))
    # Only availability is public; the credential stays server-side. Keep the
    # existing UI contract while generic redaction covers future providers too.
    result['gemini_api_key_configured']=bool(data.get('gemini_api_key'))
    return result
