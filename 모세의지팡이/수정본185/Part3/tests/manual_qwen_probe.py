"""Optional local Ollama probe; no strategy generation, file writes, or Telegram."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lab.ai.agent import Agent
from lab.ai.provider import from_settings
from lab.server import ai_settings

CASES = (
    '15분 상승추세와 상승 FVG가 같이 성립하면 1분 브레이커 올존 전략',
    '1분 EMA50/200 골크 후 10분 안에 5분 상승 FVG가 생기면 무지성 브레이커 올존 알림',
    '1분 EMA50이 EMA200 위에 있으면 1분 올존 전략',
    '1분 EMA50이 EMA200을 상향 교차하면 1분 올존 전략',
    '5분 상승 FVG가 있으면 1분 올존 전략',
    '5분 새 상승 FVG가 생기면 1분 올존 전략',
)

selected = [CASES[int(x)] for x in sys.argv[1:]] if len(sys.argv) > 1 else CASES
for sentence in selected:
    started = time.monotonic()
    try:
        agent = Agent(from_settings(ai_settings()))
        answer = agent.send(sentence)
        row = {'input': sentence, 'elapsed_sec': round(time.monotonic() - started, 2), **answer,
               'raw_model': [m['content'] for m in agent.messages if m['role'] == 'assistant' and m['content']]}
    except Exception as exc:
        row = {'input': sentence, 'error': str(exc)}
    print(json.dumps(row, ensure_ascii=True), flush=True)
