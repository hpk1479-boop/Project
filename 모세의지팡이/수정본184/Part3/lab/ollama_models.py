"""Read installed local Ollama names for the settings UI; never load a model."""
from __future__ import annotations

import json
import urllib.request

from .ai.provider import BASE_URL, model_name


def list_models(timeout=3):
    try:
        request = urllib.request.Request(BASE_URL + '/api/tags', method='GET',
                                         headers={'Accept': 'application/json'})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read(1_000_001).decode('utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get('models'), list):
            raise ValueError('Invalid model list')
        names = set()
        for row in data['models']:
            if not isinstance(row, dict):
                raise ValueError('Invalid model entry')
            value = row.get('name') or row.get('model')
            if value:
                names.add(model_name(value))
        return {'available': True, 'models': sorted(names, key=str.casefold), 'error': None}
    except (OSError, ValueError, TypeError):
        return {'available': False, 'models': [],
                'error': 'Ollama 모델 목록을 불러오지 못했습니다. 모델명을 직접 입력하세요.'}
