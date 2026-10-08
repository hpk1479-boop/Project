"""Shared look of the MOSES manuals (user manual and quick guide): colors, fonts and page parts.

Only drawing helpers live here; the text is in 제작.py and 간단사용서_제작.py.
"""
from html import escape
from pathlib import Path
import os
import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, Paragraph, Spacer, Table, TableStyle

FONT_ROOT = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
pdfmetrics.registerFont(TTFont('MG', str(FONT_ROOT / 'malgun.ttf')))
pdfmetrics.registerFont(TTFont('MGB', str(FONT_ROOT / 'malgunbd.ttf')))
pdfmetrics.registerFontFamily('MG', normal='MG', bold='MGB', italic='MG', boldItalic='MGB')

W, H = A4
MARGIN = 48
CW = W - 2 * MARGIN

INK = colors.HexColor('#1F2D3A')
MUTED = colors.HexColor('#5F6E7B')
LINE = colors.HexColor('#E3E8EE')
SOFT = colors.HexColor('#F5F7FA')
NAVY = colors.HexColor('#14324B')
AMBER = colors.HexColor('#E8930C')
RED = colors.HexColor('#D64545')
GREEN = colors.HexColor('#2F9E44')

# One color per chapter: header chip, numbers, table heads and footer.
CHAPTERS = {
    'start': ('시작하기', colors.HexColor('#2D7FF9')),
    'install': ('설치 · MT5', colors.HexColor('#6C5CE7')),
    'telegram': ('텔레그램 알림', colors.HexColor('#1D93D2')),
    'ai': ('AI 설정', colors.HexColor('#0F9D8C')),
    'oz': ('올존 이해하기', colors.HexColor('#E8590C')),
    'live': ('파트1 · 라이브 감시', colors.HexColor('#2F9E44')),
    'backtest': ('파트2 · 백테스트', colors.HexColor('#1C7ED6')),
    'lab': ('파트3 · AI 전략연구', colors.HexColor('#AE3EC9')),
    'settings': ('설정 화면', colors.HexColor('#C2255C')),
    'help': ('도움말', colors.HexColor('#495057')),
}


def tint(color, amount):
    """Mix a color with white; amount 0 = white, 1 = the color."""
    return colors.Color(1 - (1 - color.red) * amount, 1 - (1 - color.green) * amount, 1 - (1 - color.blue) * amount)


def style(name, size, leading, color=INK, bold=False, **extra):
    return ParagraphStyle(name, fontName='MGB' if bold else 'MG', fontSize=size, leading=leading,
                          textColor=color, wordWrap='CJK', **extra)


STYLES = {
    'body': style('body', 10.4, 17.2, spaceAfter=6),
    'lead': style('lead', 11, 17.5, MUTED),
    'small': style('small', 9, 14.5, MUTED, spaceAfter=4),
    'card': style('card', 9.9, 16),
    'cardtitle': style('cardtitle', 10.6, 16.5, bold=True),
    'cell': style('cell', 9.5, 15),
    'head': style('head', 9.5, 15, bold=True),
    'h': style('h', 12.5, 19, bold=True, spaceBefore=4, spaceAfter=5),
    'code': style('code', 9.2, 14.5, colors.HexColor('#23394D')),
    'caption': style('caption', 8.6, 13, MUTED),
}


def markup(text):
    """Escape text; **bold** and line breaks are the only markup."""
    value = escape(str(text))
    value = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', value)
    return value.replace('\n', '<br/>')


def P(text, kind='body', color=None):
    chosen = STYLES[kind] if color is None else ParagraphStyle(kind + 'c', parent=STYLES[kind], textColor=color)
    return Paragraph(markup(text), chosen)


class Card(Flowable):
    """Rounded box with optional badge (number or symbol) and stacked paragraphs."""

    def __init__(self, parts, *, bg=SOFT, border=None, badge=None, badge_color=None, width=None, pad=12, gap=3,
                 bar=None, radius=9):
        super().__init__()
        self.parts = [P(x) if isinstance(x, str) else x for x in parts]
        self.bg, self.border, self.badge, self.badge_color = bg, border, badge, badge_color
        self.width_hint, self.pad, self.gap, self.bar, self.radius = width, pad, gap, bar, radius

    def wrap(self, available_width, available_height):
        self.width = self.width_hint or available_width
        self.offset = 30 if self.badge is not None else 0
        inner = self.width - 2 * self.pad - self.offset - (4 if self.bar else 0)
        self.heights = [part.wrap(inner, available_height)[1] for part in self.parts]
        self.height = sum(self.heights) + self.gap * (len(self.parts) - 1) + 2 * self.pad
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(self.bg)
        if self.border is not None:
            c.setStrokeColor(self.border); c.setLineWidth(.8)
        c.roundRect(0, 0, self.width, self.height, self.radius, fill=1, stroke=1 if self.border is not None else 0)
        left = self.pad
        if self.bar:
            c.setFillColor(self.bar); c.roundRect(0, 0, 4, self.height, 2, fill=1, stroke=0); left += 4
        if self.badge is not None:
            color = self.badge_color or NAVY
            cy = self.height - self.pad - 10
            c.setFillColor(color); c.circle(left + 11, cy, 11, fill=1, stroke=0)
            c.setFillColor(colors.white); c.setFont('MGB', 10.5 if len(str(self.badge)) < 2 else 9)
            c.drawCentredString(left + 11, cy - 3.7, str(self.badge))
            left += self.offset
        y = self.height - self.pad
        for part, height in zip(self.parts, self.heights):
            y -= height
            part.drawOn(c, left, y)
            y -= self.gap


def step(number, title, text, color):
    return Card([P(title, 'cardtitle'), P(text, 'card')], badge=number, badge_color=color, bg=colors.white,
                border=LINE, pad=10)


def steps(items, color, start=1):
    out = []
    for i, (title, text) in enumerate(items, start):
        out += [step(i, title, text, color), Spacer(1, 6)]
    return out


CALLOUTS = {
    'tip': ('i', colors.HexColor('#0F9D8C')),
    'note': ('i', colors.HexColor('#2D7FF9')),
    'warn': ('!', AMBER),
    'stop': ('!', RED),
    'ok': ('v', GREEN),
}


def callout(kind, title, text=None):
    symbol, color = CALLOUTS[kind]
    parts = [P(title, 'cardtitle', color)]
    if text:
        parts.append(P(text, 'card'))
    return [Card(parts, badge=symbol, badge_color=color, bg=tint(color, .09), pad=11), Spacer(1, 8)]


def example(title, text, color):
    """A quoted example (request text, message shape)."""
    return [Card([P(title, 'caption'), P(text, 'code')], bg=tint(color, .07), bar=color, pad=10, gap=2), Spacer(1, 8)]


def heading(text, color=None):
    return P(text, 'h', color)


def table(rows, fractions, color, first_bold=False):
    head = [P(x, 'head') for x in rows[0]]
    body = [[P(x, 'head' if first_bold and i == 0 else 'cell') for i, x in enumerate(row)] for row in rows[1:]]
    t = Table([head] + body, colWidths=[CW * x for x in fractions], hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), tint(color, .14)),
        ('LINEBELOW', (0, 0), (-1, 0), 1.1, color),
        ('LINEBELOW', (0, 1), (-1, -1), .5, LINE),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    return [t, Spacer(1, 9)]


class Columns(Flowable):
    """Flowables side by side (each column is a list stacked vertically)."""

    def __init__(self, columns, gap=12):
        super().__init__()
        self.columns, self.gap = columns, gap

    def wrap(self, available_width, available_height):
        self.width = available_width
        self.col_width = (available_width - self.gap * (len(self.columns) - 1)) / len(self.columns)
        self.sizes = [[item.wrap(self.col_width, available_height)[1] for item in column] for column in self.columns]
        # Cards side by side share one height, so a row reads as one block.
        tops = [sizes[0] for column, sizes in zip(self.columns, self.sizes) if isinstance(column[0], Card)]
        if len(tops) == len(self.columns):
            for column, sizes in zip(self.columns, self.sizes):
                column[0].height = sizes[0] = max(tops)
        self.height = max(sum(sizes) for sizes in self.sizes)
        return self.width, self.height

    def draw(self):
        for index, (column, sizes) in enumerate(zip(self.columns, self.sizes)):
            x = index * (self.col_width + self.gap); y = self.height
            for item, height in zip(column, sizes):
                y -= height
                item.drawOn(self.canv, x, y)


class Figure(Flowable):
    """A screen picture with rounded corners and a caption."""

    def __init__(self, path, width, caption=None, center=True):
        super().__init__()
        self.image = ImageReader(str(path))
        iw, ih = self.image.getSize()
        self.w = width; self.h = width * ih / iw
        self.caption = P(caption, 'caption') if caption else None
        self.center = center

    def wrap(self, available_width, available_height):
        self.width = available_width
        self.cap_h = self.caption.wrap(self.w, available_height)[1] + 4 if self.caption else 0
        self.height = self.h + self.cap_h
        return self.width, self.height

    def draw(self):
        c = self.canv
        x = (self.width - self.w) / 2 if self.center else 0
        y = self.cap_h
        c.saveState()
        path = c.beginPath(); path.roundRect(x, y, self.w, self.h, 8)
        c.clipPath(path, stroke=0, fill=0)
        c.drawImage(self.image, x, y, width=self.w, height=self.h, mask='auto')
        c.restoreState()
        c.setStrokeColor(LINE); c.setLineWidth(.8); c.roundRect(x, y, self.w, self.h, 8, fill=0, stroke=1)
        if self.caption:
            self.caption.drawOn(c, x, 0)


def link(label, url, color):
    return Paragraph('<link href="' + escape(url, quote=True) + '" color="' + color.hexval().replace('0x', '#') + '">'
                     + escape(label) + '</link>', STYLES['small'])
