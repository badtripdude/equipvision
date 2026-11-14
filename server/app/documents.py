"""Passports and the rest of the technical documentation of a unit, shown inside the app.

The originals are scanned PDFs in the customer's documentation folder. Pages are rendered to
JPEG on the server (pypdfium2, no external tools) and cached on disk, so the documents open in
any phone browser, offline, without a PDF viewer. Nothing is retyped: what the engineer sees is
the customer's own page.
"""
import hashlib
import io
import threading
from pathlib import Path

from .config import DATA_DIR, ROOT_DIR

DOCS_DIR = ROOT_DIR / "docs" / "Кейс" / "Кейс" / "Документация" / "Паспорта Насосов"
CACHE_DIR = DATA_DIR / "doc-cache"
PAGE_WIDTH = 1400

# group, title, file — documents common to the four single-screw units (project 21031004)
SCREW_COMMON = [
    ("Эксплуатация", "Инструкция по эксплуатации и обслуживанию агрегата", "Инструкция по ЭиО.pdf"),
    ("Эксплуатация", "Перечень смазочных материалов", "Перечень смазочных мат-ов.pdf"),
    ("Эксплуатация", "Руководство по эксплуатации электродвигателя", "РЭ ЭД.pdf"),
    ("Эксплуатация", "Руководство: мотор-редуктор", "Мотор-редукт. РП.pdf"),
    ("Проект", "Сборочный и установочный чертежи, схема, блокировки", "СБ, уст. чертеж и др..pdf"),
    ("Проект", "Опросный лист, позиция 0201", "ОЛ 0201.pdf"),
    ("Проект", "Опросный лист, позиция 0601", "ОЛ 0601.pdf"),
    ("Проект", "Присоединительная арматура", "Присоед. арматура Горэлтех.pdf"),
    ("Проект", "Перечень документов проекта", "Перечень док-ов.pdf"),
    ("Разрешительные", "Декларации и сертификаты на агрегат", "Декл серт..pdf"),
    ("Разрешительные", "Сертификаты на электродвигатель", "Серты ЭД.pdf"),
    ("Разрешительные", "Свидетельства на датчик температуры", "Свид СИ, СС Элемер.pdf"),
]

NO_PASSPORT_NOTE = ("Паспорта на этот агрегат в материалах заказчика нет: карточка демонстрационная, "
                    "характеристики взяты из каталога производителя.")

_lock = threading.Lock()
_page_counts: dict[str, int] = {}


def _files(equipment: dict) -> tuple[str | None, list[tuple[str, str, str]]]:
    """(note, [(group, title, file)]) for a unit."""
    serial = equipment.get("serial") or ""
    if serial.startswith("21031004-"):
        n = serial.rsplit("-", 1)[1]
        own = [("Паспорта", f"Паспорт агрегата, зав. № {serial}", f"1) Насос №{n} ПС.pdf"),
               ("Паспорта", "Паспорта двигателя, редуктора и датчика температуры", f"2) Редукт, ЭД, ТПП №{n} ПС.pdf")]
        return None, own + SCREW_COMMON
    return NO_PASSPORT_NOTE, [("Каталог", "Каталог насосов HF: характеристики и рабочие кривые", "HF до 132 м3-час.pdf")]


def _pages(name: str) -> int:
    if name not in _page_counts:
        import pypdfium2 as pdfium

        with _lock:                                   # pdfium is not thread-safe
            _page_counts[name] = len(pdfium.PdfDocument(str(DOCS_DIR / name)))
    return _page_counts[name]


def list_for(equipment: dict) -> dict:
    note, files = _files(equipment)
    documents = [{"index": i, "group": group, "title": title, "pages": _pages(name)}
                 for i, (group, title, name) in enumerate(files) if (DOCS_DIR / name).exists()]
    return {"note": note, "documents": documents}


def render_page(equipment: dict, index: int, page: int, rotate: int = 0) -> bytes:
    """JPEG of one page (1-based). The scans are not consistently oriented, hence `rotate`."""
    _, files = _files(equipment)
    if not 0 <= index < len(files):
        raise LookupError("нет такого документа")
    name = files[index][2]
    if not 1 <= page <= _pages(name) or rotate not in (0, 90, 180, 270):
        raise LookupError("нет такой страницы")

    key = hashlib.md5(f"{name}|{page}|{rotate}|{PAGE_WIDTH}".encode()).hexdigest()
    cached = CACHE_DIR / f"{key}.jpg"
    if cached.exists():
        return cached.read_bytes()

    import pypdfium2 as pdfium

    with _lock:
        pdf_page = pdfium.PdfDocument(str(DOCS_DIR / name))[page - 1]
        image = pdf_page.render(scale=PAGE_WIDTH / pdf_page.get_width()).to_pil().convert("RGB")
    if rotate:
        image = image.rotate(-rotate, expand=True)    # clockwise, as the button says
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=80)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(buffer.getvalue())
    return buffer.getvalue()
