"""Equipment report as Excel and PDF: card, current diagnosis, components, history."""
import io
from datetime import datetime
from pathlib import Path

from .config import SERVER_DIR
from .rules import LEVEL_TEXT, ru

EVENT_TITLE = {"inspection": "Осмотр", "defect": "Дефект", "maintenance": "Обслуживание", "replacement": "Замена"}

# The PDF needs a TrueType font with Cyrillic. Put any .ttf into server/assets/fonts to
# override; otherwise a system font is used (Windows, then common Linux locations).
FONT_CANDIDATES = [
    *sorted((SERVER_DIR / "assets" / "fonts").glob("*.ttf")),
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/usr/share/fonts/truetype/pt-astra/PTAstraSans-Regular.ttf"),
]


class NoFont(RuntimeError):
    pass


def build_rows(card: dict, diagnosis: dict, user: dict, stats: dict | None = None, photos=()) -> dict:
    """Everything both formats show, as plain rows, so Excel and PDF cannot disagree."""
    names = {c["code"]: c["name"] for c in card["components"]}
    summary = [
        ("Агрегат", f"{card['id']} · {card['name']}"),
        ("Модель", card["model"] or "—"),
        ("Серийный номер", card["serial"] or "—"),
        ("Размещение", card["location"] or "—"),
        ("Наработка, моточасов", ru(card["hours"]) + ("" if card["has_telemetry"] else " (последнее известное значение)")),
    ]
    if card["resource"]:
        r = card["resource"]
        summary.append(("Ресурс агрегата", f"{ru(r['norm'])} ч, расчётный остаток {ru(r['remaining'])} ч ({r['source']})"))
    summary += [
        ("Телеметрия", f"подключена, время данных {diagnosis['ts']}" if card["has_telemetry"] else "не подключена"),
        ("Итог", diagnosis["status_text"]),
        ("Отчёт сформировал", f"{user['name']} ({user['role_title']})"),
        ("Дата формирования", datetime.now().strftime("%d.%m.%Y %H:%M")),
    ]

    diagnoses = [
        (LEVEL_TEXT[d["level"]], d["title"], names.get(d["component"], "агрегат в целом"), d["diagnosis"],
         "; ".join(d["evidence"]), d["recommendation"], d["basis"])
        for d in diagnosis["diagnoses"]
    ]

    components = []
    for c in card["components"]:
        r = c["resource"]
        components.append((
            c["name"], diagnosis["components"][c["code"]]["text"], ru(r["used_hours"]),
            ru(c["norm_hours"]) if c["norm_hours"] else "не задан",
            ru(r["remaining_hours"]) if r["remaining_hours"] is not None else "—",
            c["norm_source"] or "—", c["installed_on"] or "—",
        ))

    history = [
        (e["created_at"][:10], EVENT_TITLE[e["kind"]], e["component_name"] or "—",
         ru(e["equipment_hours"]) if e["equipment_hours"] is not None else "—", e["author"] or "—", e["note"] or "")
        for e in card["events"]
    ]
    return {
        "title": f"Отчёт о состоянии оборудования {card['id']}",
        "summary": summary,
        "specs": [(f"{section}: {key}", value) for section, fields in card["specs"].items()
                  for key, value in fields.items()],
        "diagnoses": diagnoses,
        "components": components,
        "history": history,
        "stats": [] if not stats else (
            [("Всего дефектов в истории", str(stats["defects"])), ("Осмотров в истории", str(stats["inspections"]))]
            + [(f"По признаку: {row['title']}", str(row["count"])) for row in stats["by_sign"]]
            + [(f"По узлу: {name}", str(n)) for name, n in stats["by_component"].items()]
            + [(f"По месяцу: {month}", str(n)) for month, n in stats["by_month"].items()]),
        "photos": list(photos),                   # (caption, path)
        "limits": ["Отчёт основан на внешнем осмотре, показаниях датчиков и расчёте по наработке. "
                   "Отсутствие замечаний не подтверждает исправность оборудования."] + (
            [] if card["has_telemetry"] else ["Телеметрия не подключена: правила по датчикам не применялись."]),
    }


DIAGNOSIS_HEAD = ("Категория", "Диагноз", "Узел", "Описание", "Причины", "Рекомендация", "Основание")
COMPONENT_HEAD = ("Узел", "Состояние", "Наработка, ч", "Норматив, ч", "Остаток, ч", "Источник норматива", "Установлен")
HISTORY_HEAD = ("Дата", "Событие", "Узел", "Наработка, ч", "Автор", "Примечание")


def to_xlsx(rows: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    book = Workbook()

    def fill(sheet, head, body, widths):
        if head:
            sheet.append(head)
            for cell in sheet[1]:
                cell.font = Font(bold=True)
        for row in body:
            sheet.append([str(v) for v in row])
        for line in sheet.iter_rows():
            for cell in line:
                # Text typed by people (notes) must never be read as a formula.
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        for index, width in enumerate(widths):
            sheet.column_dimensions[chr(ord("A") + index)].width = width

    summary = book.active
    summary.title = "Сводка"
    fill(summary, None, [(rows["title"], "")] + rows["summary"] + [("", "")] + rows["specs"]
         + [("", "")] + [("Ограничения", text) for text in rows["limits"]], (30, 80))
    summary["A1"].font = Font(bold=True, size=13)
    fill(book.create_sheet("Диагностика"), DIAGNOSIS_HEAD, rows["diagnoses"], (26, 30, 24, 50, 50, 50, 24))
    fill(book.create_sheet("Узлы"), COMPONENT_HEAD, rows["components"], (30, 34, 14, 14, 14, 22, 14))
    fill(book.create_sheet("История"), HISTORY_HEAD, rows["history"], (12, 16, 26, 14, 20, 80))
    fill(book.create_sheet("Статистика дефектов"), ("Показатель", "Количество"), rows["stats"], (50, 14))
    if rows["photos"]:
        from openpyxl.drawing.image import Image as SheetImage

        sheet = book.create_sheet("Фото дефектов")
        sheet.column_dimensions["A"].width = 60
        line = 1
        for caption, path in rows["photos"]:
            sheet.cell(row=line, column=1, value=str(caption)).font = Font(bold=True)
            picture = SheetImage(str(path))
            scale = 360 / picture.width
            picture.width, picture.height = 360, int(picture.height * scale)
            sheet.add_image(picture, f"A{line + 1}")
            line += int(picture.height / 20) + 3          # rows are about 20 px high

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def to_pdf(rows: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font_path = next((p for p in FONT_CANDIDATES if p.exists()), None)
    if font_path is None:
        raise NoFont("Нет шрифта с кириллицей: положите .ttf в server/assets/fonts")
    if "EqvSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("EqvSans", str(font_path)))

    body = ParagraphStyle("body", fontName="EqvSans", fontSize=8.5, leading=11)
    head = ParagraphStyle("head", parent=body, fontSize=11, leading=14, spaceBefore=8, spaceAfter=4)
    title = ParagraphStyle("title", parent=body, fontSize=14, leading=18, spaceAfter=6)

    def escape(value) -> str:
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def table(header, data, widths):
        cells = [[Paragraph(escape(v), body) for v in row] for row in ([header] if header else []) + list(data)]
        style = [("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]
        if header:
            style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6e8ea")))
        return Table(cells, colWidths=[w * mm for w in widths], repeatRows=1 if header else 0, style=TableStyle(style))

    story = [Paragraph(escape(rows["title"]), title), table(None, rows["summary"] + rows["specs"], (60, 207))]
    story.append(Paragraph("Диагностика", head))
    if rows["diagnoses"]:
        story.append(table(DIAGNOSIS_HEAD, rows["diagnoses"], (30, 34, 28, 50, 55, 50, 20)))
    else:
        story.append(Paragraph("Отклонений по датчикам и визуальных признаков не зафиксировано.", body))
    story += [Paragraph("Узлы и ресурс", head), table(COMPONENT_HEAD, rows["components"], (50, 60, 24, 24, 24, 50, 35))]
    story.append(Paragraph("История", head))
    story.append(table(HISTORY_HEAD, rows["history"], (22, 26, 40, 22, 32, 125)) if rows["history"]
                 else Paragraph("Записей нет.", body))
    if rows["stats"]:
        story += [Paragraph("Статистика дефектов", head), table(("Показатель", "Количество"), rows["stats"], (120, 40))]
    if rows["photos"]:
        from reportlab.platypus import Image as FlowImage

        story.append(Paragraph("Фотографии дефектов", head))
        cells = []
        for caption, path in rows["photos"]:
            picture = FlowImage(str(path))
            scale = 82 * mm / picture.imageWidth
            picture.drawWidth, picture.drawHeight = 82 * mm, picture.imageHeight * scale
            cells.append([picture, Paragraph(escape(caption), body)])
        grid = [cells[i:i + 3] + [""] * (3 - len(cells[i:i + 3])) for i in range(0, len(cells), 3)]
        story.append(Table(grid, colWidths=[88 * mm] * 3, style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")])))
    story.append(Spacer(1, 6))
    story += [Paragraph(escape(text), body) for text in rows["limits"]]

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=15 * mm, rightMargin=15 * mm,
                      topMargin=12 * mm, bottomMargin=12 * mm, title=rows["title"]).build(story)
    return buffer.getvalue()
