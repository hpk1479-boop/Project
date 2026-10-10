"""Opt-in future fine-tuning corpus: only user-confirmed screen applications."""
from __future__ import annotations

import datetime as dt
import json

from .. import catalog


def record_confirmed(user_text: str, first_interpretation: dict | None,
                     confirmed_intent: dict, recipe_summary: dict) -> None:
    path = catalog.ROOT / 'projects' / 'ai_confirmed.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {'confirmed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
           'user_text': user_text,
           'ai_first_interpretation': first_interpretation,
           'user_applied_intent': confirmed_intent,
           'confirmed_recipe_summary': recipe_summary,
           'status': 'USER_APPLIED_TO_EDITOR'}
    # Model drafts are never written here; only the trusted /ai/apply route calls this.
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
