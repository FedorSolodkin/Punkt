"""Формирование PDF отчёта (ТЗ §2.3, §5): ReportLab + встроенный
кириллический шрифт DejaVu Sans (свободная лицензия, поставляется в
reports/fonts)."""
import io
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image as RLImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONTS_DIR = Path(__file__).resolve().parent / 'fonts'

_fonts_registered = False


class PhotoUnavailable(Exception):
    """Поднимается, если файл фото для отчёта отсутствует на диске (ТЗ §2.3: 503)."""


def _ensure_fonts():
    global _fonts_registered
    if _fonts_registered:
        return
    pdfmetrics.registerFont(TTFont('DejaVuSans', str(FONTS_DIR / 'DejaVuSans.ttf')))
    pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', str(FONTS_DIR / 'DejaVuSans-Bold.ttf')))
    _fonts_registered = True


def _photo_cell(path, placeholder_style):
    if path is None:
        return Paragraph('—', placeholder_style)
    p = Path(path)
    if not p.exists():
        raise PhotoUnavailable(str(path))
    img = RLImage(str(p), width=24 * mm, height=18 * mm)
    img.hAlign = 'CENTER'
    return img


def build_pdf(*, site_name, generated_at, filters_text, counts, rows) -> bytes:
    """rows: список dict с ключами number, zone, description, assignee,
    due_date, status_display, overdue (bool), before_path, after_path.
    """
    _ensure_fonts()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
        title='PUNKT — реестр замечаний',
    )

    base = getSampleStyleSheet()
    normal = ParagraphStyle('normal_ru', parent=base['Normal'], fontName='DejaVuSans', fontSize=9, leading=12)
    title_style = ParagraphStyle('title_ru', parent=base['Title'], fontName='DejaVuSans-Bold', fontSize=16)
    header_style = ParagraphStyle('header_ru', parent=normal, fontName='DejaVuSans-Bold', fontSize=8)
    small_style = ParagraphStyle('small_ru', parent=normal, fontSize=7.5, leading=9)

    story = [
        Paragraph('PUNKT — реестр замечаний', title_style),
        Paragraph(f'Объект: {site_name}', normal),
        Paragraph(f'Сформирован: {generated_at}', normal),
        Paragraph(f'Фильтры: {filters_text or "без фильтров"}', normal),
        Paragraph(
            f'Всего: {counts["total"]} · Открыто: {counts["open"]} · '
            f'На проверке: {counts["ready"]} · Закрыто: {counts["closed"]} · '
            f'Просрочено: {counts["overdue"]}',
            normal,
        ),
        Spacer(1, 5 * mm),
    ]

    header = ['№', 'Зона', 'Описание', 'Исполнитель', 'Срок', 'Статус', 'До', 'После']
    table_data = [[Paragraph(h, header_style) for h in header]]

    for row in rows:
        status_text = row['status_display'] + (' · просрочено' if row['overdue'] else '')
        table_data.append([
            Paragraph(str(row['number']), small_style),
            Paragraph(row['zone'], small_style),
            Paragraph(row['description'], small_style),
            Paragraph(row['assignee'], small_style),
            Paragraph(row['due_date'], small_style),
            Paragraph(status_text, small_style),
            _photo_cell(row.get('before_path'), small_style),
            _photo_cell(row.get('after_path'), small_style),
        ])

    col_widths = [10 * mm, 22 * mm, 62 * mm, 28 * mm, 18 * mm, 28 * mm, 26 * mm, 26 * mm]
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#999999')),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E5E9EE')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, -1), 'DejaVuSans'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(table)

    if not rows:
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph('Нет записей, соответствующих фильтру. Итог: 0.', normal))

    doc.build(story)
    return buffer.getvalue()
