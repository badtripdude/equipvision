import os
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = SERVER_DIR.parent
DATA_DIR = SERVER_DIR / "data"
CERT_DIR = SERVER_DIR / "certs"
CLIENT_DIR = ROOT_DIR / "client"

TELEMETRY_CSV = DATA_DIR / "telemetry.csv"
# EQUIPVISION_DB (run.py --db) points a second server at its own database, e.g. for a trial run.
DB_PATH = Path(os.environ.get("EQUIPVISION_DB") or DATA_DIR / "equipvision.sqlite3")

# Telemetry tags of the demo centrifugal unit.
# warn/alarm come from the customer's "Описание БД" documents, except vib_hf alarm:
# the document only gives 2.5 g, the 5.0 g alarm level is inferred from the data
# (warning rows top out at 4.99 g, alarm rows start at 5.09 g).
TAGS = {
    "rpm":          {"digits": 0, "title": "Обороты",             "unit": "об/мин"},
    "vib_rms":      {"digits": 2, "title": "Вибрация, СКЗ",       "unit": "мм/с"},
    "vib_1x":       {"digits": 2, "title": "Вибрация 1x",         "unit": "мм/с", "warn": 2.8,  "alarm": 4.5},
    "vib_2x":       {"digits": 2, "title": "Вибрация 2x",         "unit": "мм/с", "warn": 1.8,  "alarm": 3.0},
    "vib_hf":       {"digits": 2, "title": "ВЧ шум",              "unit": "g",    "warn": 2.5,  "alarm": 5.0},
    "temp_bearing": {"digits": 1, "title": "Подшипник насоса",    "unit": "°C",   "warn": 75.0, "alarm": 90.0},
    "temp_winding": {"digits": 1, "title": "Обмотки двигателя",   "unit": "°C",   "warn": 90.0, "alarm": 110.0},
}

# Each telemetry row is one hour of operation.
HOURS_PER_ROW = 1.0
# Replay speed: telemetry rows per wall-clock second.
DEFAULT_SPEED = 0.5
