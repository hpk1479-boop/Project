"""MOSES quick guide: how to use part 1, 2 and 3, one page each. Run 제작.py first (page numbers)."""
from pathlib import Path
import hashlib
import json
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Frame, Spacer

from pypdf import PdfReader

from 디자인 import (CHAPTERS, CW, H, LINE, MARGIN, MUTED, NAVY, W, Card, Columns, P, callout, example, heading,
                  steps, table, tint)

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OUT = ROOT / '매뉴얼/모세_간단사용서.pdf'
QA = ROOT / '검증결과' / ('revision' + ''.join(ch for ch in ROOT.name if ch.isdigit())) / 'manual'
QA.mkdir(parents=True, exist_ok=True)
TEMP = QA / 'quick.partial.pdf'
TITLE = '모세 트레이딩 시스템 간단사용서'
UPDATED = '2026.10.07'
REVISION = ROOT.name
PAGES = json.loads((HERE / '쪽번호.json').read_text('utf-8'))
ICON = ImageReader(str(HERE / '그림/moses_icon.png'))


def ref(chapter):
    first, last = PAGES[chapter]
    return f'사용자 매뉴얼 {first}쪽' if first == last else f'사용자 매뉴얼 {first}~{last}쪽'


def tone(chapter): return CHAPTERS[chapter][1]


# ---------------------------------------------------------------- 파트1 · 라이브 감시
G = tone('live')
part1 = [
    Card([P('시작 전 준비', 'cardtitle', G),
          P('MT5에 EA(THE_STAFF_OF_MOSES)를 LIVE 모드로 실행하고, 톱니바퀴 → 라이브 감시 설정에서 텔레그램을 연결해 두세요. '
            '→ 설치·MT5 ' + ref('install') + ' / 텔레그램 ' + ref('telegram'), 'card')], bg=tint(G, .08), bar=G),
    Spacer(1, 10),
    heading('알림 받기까지 4단계', G),
    *steps([('라이브 감시 → 전략 설정을 열어요', '쓸 전략에 체크해요. 기본 스페셜과 승급한 새 전략이 여기에 있어요.'),
            ('최종 트리거와 거래시간을 확인해요', '전략 카드의 최종 알림 조건에서 올존·무지성·브레이커 중 하나를 고르고, 거래시간도 봐요.'),
            ('저장해요', '실행 중에 저장하면 라이브 엔진이 자동으로 다시 시작돼요.'),
            ('전체 시작을 눌러요', '조건이 맞으면 텔레그램으로 알림이 와요. 멈출 때는 전체 종료를 눌러요.')], G),
    Columns([[Card([P('화면 카드 상태', 'cardtitle', G),
                    P('**연결대기** 실행·연결을 기다리는 중\n**연결중** 연결됨 (조건 충족과는 별개)\n**오류** 그 모듈 로그를 확인\n'
                      'EVENT가 오류이고 "엔진 처리 지연 · N초 밀림"이 보이면 알림이 늦는 중이에요.', 'card')], bg=tint(G, .08))],
             [Card([P('WATCH: 내가 직접 거는 감시', 'cardtitle', G),
                    P('봇과의 1:1 대화에 보내요.\n"골드 15분 하단 원비에 닿으면 알려줘"\n"골드 1분 무지성 브레이커 매수 올존 알려줘"\n'
                      '답장과 WATCH 로그로 등록을 확인해요.', 'card')], bg=tint(G, .08))]]),
    Spacer(1, 10),
    *callout('warn', '알림이 안 오면', 'MT5 로그인·EA 실행 → 전략 체크·거래시간 → 텔레그램 칸이 "설정됨"인지 → 조건이 아직 안 맞았을 수도 있어요. '
             '자세히: ' + ref('live') + ', 문제 해결 ' + ref('help')),
]

# ---------------------------------------------------------------- 파트2 · 백테스트
B = tone('backtest')
part2 = [
    heading('과거 데이터로 전략 확인하기', B),
    *steps([('백테스트 메뉴를 열어요', '처음 한 번은 연결 위치 설정에서 데이터 창고를 지정하고 저장해요. 처음엔 한 종목, 한 전략, 며칠 정도로 시작하세요.'),
            ('종목과 기간을 넣어요', '날짜는 UTC이고 종료일은 포함되지 않아요. 9월 한 달이면 2026-09-01 ~ 2026-10-01이에요.'),
            ('대상을 골라요', 'SPECIAL은 백테스트의 전략 설정에서 골라 적용해요(라이브 설정과 따로예요). WATCH는 감시 명령을 적어요.'),
            ('결과 옵션을 골라요', '알림 온리는 여러 전략을 함께 돌려요. 가상 진입은 전략 1개만 돌리고, 그 전략 레시피의 진입·진입 제한·손절 값이 채워져요.'),
            ('백테스트 실행을 눌러요', '자료가 없으면 구축 계획이 나와요. 계획을 읽고 "계획 확인 후 녹화 시작"으로 승인해요.'),
            ('결과 보기를 눌러요', '알림 횟수나 가상 진입 결과를 보고 파일을 내려받아요. 백테스트 목록에서 언제든 다시 열 수 있어요.')], B),
    *table([['재생 모드', '이렇게 골라요'],
            ['BAR / 봉', '처음에 추천. 봉 단위로 재생해요.'],
            ['TIMER / 틱', '진행봉과 사건을 더 자세히 재생해요.'],
            ['이벤트', '이벤트 처리 경로예요.']], (.24, .76), B, first_bold=True),
    *callout('tip', '라이브와 함께 돌려도 돼요', '백테스트는 라이브보다 낮은 순위로 돌아서 라이브 알림을 늦추지 않아요. MOSES 창을 뒤로 보내도 느려지지 않아요. 자세히: ' + ref('backtest')),
]

# ---------------------------------------------------------------- 파트3 · AI 전략연구
V = tone('lab')
part3 = [
    P('AI 전략연구는 Gemini 연결이 필요해요 → ' + ref('ai'), 'small'),
    heading('말로 전략을 만들어 라이브까지', V),
    *steps([('전략생성을 고르고 요청을 보내요', '종목·시간봉·방향·조건 순서·최종 트리거를 적어요.'),
            ('핵심 요약을 확인하고 고쳐요', 'AI 해석이 내 요청과 같은지 봐요. 표나 대화로 고칠 수 있고, 세부는 상세보기에 있어요.'),
            ('전략 생성을 눌러요', '생성 완료 이름이 나오면 저장된 거예요. 아직 라이브에 켜진 건 아니에요.'),
            ('백테스트로 확인해요', '파트3에서 바로 요청해도 파트2와 같은 엔진으로 돌아요.'),
            ('목록에서 메인전략 승급', '초기화 옆 목록 → 새 전략 선택 → 메인전략 승급. 파트1·파트2 전략 설정에 올라가요.'),
            ('파트1에서 사용 선택 · 저장 · 전체 시작', '승급만으로는 감시가 켜지지 않아요.')], V),
    Columns([[*example('전략 요청 예시', '골드 15분 상승추세 중 눌림에서 1분 일반 매수 올존이 나오면 알림을 주는 전략을 만들어 주세요.', V)],
             [*example('백테스트 요청 예시', '지금 연구한 전략을 골드 최근 3개월, 보유 데이터만 써서 BAR 모드, 알림 온리로 백테스트해 주세요.', V)]]),
    *table([['올존 선택명', '뜻'],
            ['올존', '상위 프레임 추세 중 눌림의 마이크로 더블바텀·더블탑'],
            ['무지성 올존', '그 프레임의 마이크로 패턴만'],
            ['브레이커 올존 / 무지성 브레이커', '위 두 가지 + 앞전 저점·고점을 깬 패턴']], (.36, .64), V, first_bold=True),
    P('올존 알림은 우측 저점·고점에서 와요. 알림 뒤 실제 반전을 확인하세요. 자세히: 올존 ' + ref('oz') + ', 파트3 ' + ref('lab'), 'small'),
]

c = canvas.Canvas(str(TEMP), pagesize=(W, H), pageCompression=1)
c.setTitle(TITLE); c.setAuthor('MOSES'); c.setSubject('파트1 라이브 감시, 파트2 백테스트, 파트3 AI 전략연구 간단 사용법')


def header(part, number, total):
    name, color = CHAPTERS[part]
    c.setFillColor(NAVY); c.rect(0, H - 118, W, 118, fill=1, stroke=0)
    c.setFillColor(colors.Color(.3, .6, 1, alpha=.10)); c.circle(W - 40, H - 30, 120, fill=1, stroke=0)
    c.setFillAlpha(1)   # the decoration's transparency must not fade the icon
    c.drawImage(ICON, W - MARGIN - 62, H - 96, width=62, height=62, mask='auto', preserveAspectRatio=True)
    c.setFillColor(colors.HexColor('#BFD3E6')); c.setFont('MGB', 9.5); c.drawString(MARGIN, H - 42, '모세 간단사용서')
    c.setFillColor(color); c.roundRect(MARGIN, H - 84, 30, 30, 8, fill=1, stroke=0)
    c.setFillColor(colors.white); c.setFont('MGB', 16); c.drawCentredString(MARGIN + 15, H - 75, str(number))
    c.setFont('MGB', 22); c.drawString(MARGIN + 42, H - 78, name)
    c.setStrokeColor(LINE); c.line(MARGIN, 44, W - MARGIN, 44)
    c.setFillColor(MUTED); c.setFont('MG', 7.8)
    c.drawString(MARGIN, 30, f'{TITLE}  ·  {REVISION} · {UPDATED}  ·  자세한 설명은 모세 사용자 매뉴얼')
    c.setFillColor(color); c.roundRect(W - MARGIN - 34, 25, 34, 15, 7.5, fill=1, stroke=0)
    c.setFillColor(colors.white); c.setFont('MGB', 8); c.drawCentredString(W - MARGIN - 17, 29.3, f'{number} / {total}')


layout = []
sections = (('live', part1), ('backtest', part2), ('lab', part3))
for number, (part, body) in enumerate(sections, 1):
    header(part, number, len(sections))
    story = list(body)
    frame = Frame(MARGIN, 56, CW, H - 118 - 74, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    frame.addFromList(story, c)
    if story:
        raise RuntimeError(f'Quick guide page {number} overflow; remaining={len(story)}')
    layout.append({'page': number, 'part': CHAPTERS[part][0], 'overflow': False})
    c.bookmarkPage(f'q{number}'); c.addOutlineEntry(CHAPTERS[part][0], f'q{number}', level=0); c.showPage()
c.save()

reader = PdfReader(str(TEMP)); text = '\n'.join(page.extract_text() or '' for page in reader.pages)
assert len(reader.pages) == len(sections)
for term in ('모세 간단사용서', '파트1', '파트2', '파트3', '전체 시작', 'WATCH', '알림 온리', 'UTC', '메인전략 승급',
             '핵심 요약', '무지성 브레이커', '사용자 매뉴얼', '엔진 처리 지연'):
    assert term in text, term
assert '�' not in text and str(ROOT) not in text
os.replace(TEMP, OUT)
(QA / 'quick_checks.json').write_text(json.dumps({
    'document': OUT.relative_to(ROOT).as_posix(), 'title': TITLE, 'pages': len(sections), 'bytes': OUT.stat().st_size,
    'sha256': hashlib.sha256(OUT.read_bytes()).hexdigest(), 'overflow_checks': layout, 'text_checks': 'passed',
    'manual_page_references': PAGES}, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'pdf': OUT.relative_to(ROOT).as_posix(), 'pages': len(sections),
                  'size_KiB': round(OUT.stat().st_size / 1024, 1)}, ensure_ascii=False))
