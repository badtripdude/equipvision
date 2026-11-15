"""SQLite storage: equipment cards, components, inspection and maintenance history."""
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from . import auth
from .config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS equipment (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    manufacturer TEXT,
    model TEXT,
    serial TEXT UNIQUE,
    location TEXT,
    commissioned_on TEXT,
    hours_base REAL NOT NULL DEFAULT 0,   -- motor-hours before the telemetry window starts
    has_telemetry INTEGER NOT NULL DEFAULT 0,
    resource_hours REAL,                   -- unit resource from the passport, NULL if unknown
    resource_source TEXT,
    maintenance_marks TEXT NOT NULL DEFAULT '[]',   -- JSON list of motor-hour marks for scheduled service
    specs TEXT                             -- JSON with nameplate characteristics
);
CREATE TABLE IF NOT EXISTS component (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id TEXT NOT NULL REFERENCES equipment(id),
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    norm_hours REAL,                       -- NULL: no hour-based norm, condition-based only
    norm_source TEXT,
    installed_on TEXT,
    hours_at_install REAL NOT NULL DEFAULT 0,
    UNIQUE (equipment_id, code)
);
CREATE TABLE IF NOT EXISTS event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id TEXT NOT NULL REFERENCES equipment(id),
    component_id INTEGER REFERENCES component(id),
    kind TEXT NOT NULL CHECK (kind IN ('inspection', 'defect', 'maintenance', 'replacement')),
    note TEXT,
    author TEXT,
    equipment_hours REAL,
    created_at TEXT NOT NULL,
    sign TEXT,                             -- visual sign of a defect event: corrosion / leak / overheat / ...
    photo TEXT                             -- file name of the photo taken when the defect was recorded
);
CREATE TABLE IF NOT EXISTS round (         -- a walk-around of the whole route
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    started_by TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS round_stop (
    round_id INTEGER NOT NULL REFERENCES round(id),
    equipment_id TEXT NOT NULL REFERENCES equipment(id),
    position INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'inspected', 'skipped')),
    note TEXT,
    at TEXT,
    PRIMARY KEY (round_id, equipment_id)
);
CREATE TABLE IF NOT EXISTS training (      -- results of the trainee mode
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id TEXT NOT NULL REFERENCES equipment(id),
    username TEXT NOT NULL,
    name TEXT NOT NULL,
    score INTEGER NOT NULL,
    details TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user (
    username TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('engineer', 'master', 'admin')),
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES user(username),
    expires_at REAL NOT NULL
);
"""

TEAM = "принято командой"
PASSPORT = "паспорт агрегата"


def _unit(**fields) -> dict:
    return {"manufacturer": None, "location": None, "commissioned_on": None, "has_telemetry": 0,
            "resource_hours": None, "resource_source": None, "maintenance_marks": "[]", **fields}


# Demo centrifugal unit: the one the customer's telemetry belongs to. Characteristics follow
# the HF 30A row of the catalogue in docs (7.5 kW, 2900 rpm). The serial number, dates and all
# hour norms are demo values chosen by the team: the customer's documents give none for it.
CENTRIFUGAL = _unit(
    id="P-101", name="Насосный агрегат центробежный", model="HF 30A", serial="HF30A-2026-0417",
    location="Насосная станция, позиция P-101", commissioned_on="2025-09-01", hours_base=7950,
    has_telemetry=1, maintenance_marks="[4000, 8000, 16000]",
    specs=json.dumps({
        "Характеристики (каталог HF, строка HF 30A)": {
            "Мощность двигателя": "7,5 кВт", "Частота вращения": "2900 об/мин",
            "Подача": "до 132 м³/ч", "Напор": "до 23 м",
        },
    }, ensure_ascii=False),
)
CENTRIFUGAL_COMPONENTS = [
    # code, name, norm_hours, norm_source, installed_on, hours_at_install
    ("motor", "Электродвигатель", 40000, TEAM, "2025-09-01", 0),
    ("terminal_box", "Клеммная коробка", None, None, "2025-09-01", 0),
    ("coupling", "Муфта и защитный кожух", 16000, TEAM, "2025-09-01", 0),
    ("bearing", "Подшипниковый узел насоса", 16000, TEAM, "2025-09-01", 0),
    ("seal", "Торцевое уплотнение", 8000, TEAM, "2025-10-20", 850),
    ("impeller", "Рабочее колесо", 24000, TEAM, "2025-09-01", 0),
    ("frame", "Рама и анкерные болты", None, None, "2025-09-01", 0),
    ("gauge", "Манометр на нагнетании", None, None, "2025-09-01", 0),
]
# Demo history, so the card is not empty and the 4000 h service is already on record.
CENTRIFUGAL_EVENTS = [
    # component code, kind, note, author, equipment_hours, created_at
    ("seal", "replacement", "Замена торцевого уплотнения", "Мастер участка", 850, "2025-10-20T09:30:00+00:00"),
    (None, "maintenance", "ТО-2: замена масла, проверка центровки, протяжка фланцев", "Мастер участка", 4010,
     "2026-03-20T08:00:00+00:00"),
    (None, "inspection", "Плановый осмотр, замечаний нет", "Инженер", 7020, "2026-09-28T10:15:00+00:00"),
]

# Four single-screw units from the customer's real passports (project 21031004).
# From the documents: model, serial numbers, manufacturer, date of manufacture, resource and
# service life (passport), flow/head/motor (data sheet OL 0201 and the specification).
# NOT in the documents: motor-hours, so hours_base is demo data; component norms are not given
# either, so components are assessed by condition only. No telemetry is connected to these units.
# Motor serial numbers are read from the motor passports; only unit 1 was verified by eye, the scans
# of the others are bound in a different order, so for them the card points to the passport itself.
MOTOR_SERIALS = {1: "230201068"}


def screw_specs(n: int) -> str:
    """Everything stated in the unit passport (pages 3-4), section by section, plus what the motor
    passport and the data sheet add. Values are copied as printed in the documents."""
    motor = {
        "Тип": "BRA160M4 FБУ1, трёхфазный асинхронный", "Изготовитель": "АО «Ярославский электромашиностроительный завод» (ELDIN)",
        "Мощность": "11 кВт", "Напряжение, частота": "380 В, 50 Гц, соединение Y", "Номинальный ток": "23 А",
        "Частота вращения": "1460 об/мин", "КПД": "87,8 %", "cos φ": "0,84", "Режим работы": "S1",
        "Монтажное исполнение": "IM 2001", "Степень защиты": "IP 55", "Взрывозащита": "1Ex d IIB T4 Gb",
        "Класс изоляции": "F", "Защита обмоток": "PTC-терморезисторы",
        "Допустимая виброскорость": "2,2 мм/с (категория A по ГОСТ IEC 60034-14)", "Уровень звука": "67 дБА",
    }
    if n in MOTOR_SERIALS:
        motor["Заводской номер"] = MOTOR_SERIALS[n]
        motor["Дата изготовления"] = "01.03.2023"
    else:
        motor["Заводской номер"] = "см. паспорт двигателя в документах"
    return json.dumps({
        "Общие сведения (паспорт агрегата)": {
            "Оборудование": "агрегат насосный одновинтовой эксцентриковый",
            "Модель": "AMS PCP60S12/3 01 Ex B1 HNBR DTS", "Серийный номер": f"21031004-{n}",
            "Изготовитель": "АМС (Россия), ООО «Альтернативные Механические Системы»",
            "Технические условия": "ТУ 3631-001-23430200-2015",
            "Назначение": "перекачивание обводнённого нефтепродукта",
            "Климатическое исполнение": "У1 по ГОСТ 15150-69", "Дата выпуска": "06.2023",
            "Гарантия": "12 месяцев с момента ввода в эксплуатацию",
            "Ресурс до списания": "не менее 11 000 ч", "Срок службы": "20 лет",
        },
        "Технические характеристики (паспорт агрегата)": {
            "Подача": "5–20 м³/ч", "Давление": "до 10 бар", "Допускаемый кавитационный запас NPSHr": "2 м",
            "Температура продукта": "+5…+45 °C", "Макс. температура при пропарке (насос выключен)": "+90 °C",
            "Макс. плотность": "1600 кг/м³", "Концентрация твёрдой фазы": "до 5 %",
            "Габариты (Д × Ш × В)": "2742 × 360 × 605 мм", "Масса": "430 кг",
            "Реверсивная работа": "да", "Защитный кожух": "да",
        },
        "Материалы и присоединения (паспорт агрегата)": {
            "Ротор": "коррозионностойкая сталь 40Х13, закалка 50 HRC", "Статор": "эластомер абразивостойкий HNBR",
            "Уплотнение": "одинарное торцевое, карбид вольфрама / карбид кремния",
            "Корпус": "сталь 20 с защитным покрытием",
            "Вход и выход продукта": "фланец 100-16-01-1-B-ст.20-IV-db-110, ГОСТ 33259-15",
        },
        "Электродвигатель (паспорта агрегата и двигателя)": motor,
        "Редуктор (паспорт агрегата, перечень смазки)": {
            "Тип": "WXRF87-AM160-5.30-M1-φ300, подшипники NSK", "Частота вращения на выходе": "260 об/мин",
            "Передаточное отношение": "5,3", "Выходной вал": "Ф40 × 80 мм",
            "Масло": "синтетическое VG220, 3,7 л, −45…+40 °C",
        },
        "Защиты (паспорт агрегата, перечень блокировок)": {
            "Датчик сухого хода DTS": "термопреобразователь ПТ 0304 Ex-ВТ, Pt100, −50…160 °C, 0Ex ia IIB T4 Ga",
            "Реле термисторной защиты": "РТЗ-1М AC230В УХЛ4",
        },
        "Комплектность (паспорт агрегата)": {
            "Ответные фланцы": "2 шт., с крепежом и прокладками СНП-Д",
            "Анкер клиновой М12×100": "4 шт.",
            "ЗИП на ПНР и 1 год": "статор 60S1240DTS, торцевое уплотнение 60S1290, уплотнительное кольцо 60S1226 — по 1 шт.",
            "Документация": "по перечню документов проекта № 21031004",
        },
        "Проектные условия (опросный лист 0201)": {
            "Среда": "нефтяной шлам сточных вод", "Подача номинальная": "18 м³/ч (5–20)", "Напор": "60 м",
            "Режим работы": "периодический", "Срок службы по опросному листу": "10 лет",
        },
    }, ensure_ascii=False)


SCREW_UNITS = [
    _unit(id=f"PCP-{n}", name="Агрегат насосный одновинтовой эксцентриковый", manufacturer="AMS",
          model="AMS PCP60S12/3 01 Ex B1 HNBR DTS", serial=f"21031004-{n}",
          location="Очистные сооружения, откачка нефтешлама", hours_base=hours,
          resource_hours=11000, resource_source=PASSPORT, specs=screw_specs(n))
    for n, hours in ((1, 3200), (2, 5400), (3, 9800), (4, 10650))
]
SCREW_COMPONENTS = [
    ("motor", "Электродвигатель", None, None, None, 0),
    ("terminal_box", "Клеммная коробка", None, None, None, 0),
    ("gearbox", "Редуктор", None, None, None, 0),
    ("seal", "Торцевое уплотнение", None, None, None, 0),
    ("stator", "Статор и ротор", None, None, None, 0),
    ("joints", "Шарнирные узлы", None, None, None, 0),
    ("dts", "Датчик сухого хода", None, None, None, 0),
    ("frame", "Рама и анкерные болты", None, None, None, 0),
]


# Demo defect history, so the statistics are not empty on a fresh database. Not from the customer.
SEED_DEFECTS = [
    # equipment, component code, sign, note, equipment_hours, created_at
    ("P-101", "frame", "corrosion", "Коррозия опоры рамы", 5210, "2026-05-12T09:10:00+00:00"),
    ("P-101", "seal", "leak", "Капельная течь уплотнения", 6480, "2026-07-03T11:40:00+00:00"),
    ("P-101", "frame", "corrosion", "Коррозия у анкерного болта", 7020, "2026-09-28T10:20:00+00:00"),
    ("PCP-1", "stator", "corrosion", "Коррозия корпуса у напорного фланца", 2100, "2026-06-18T08:30:00+00:00"),
    ("PCP-2", "gearbox", "leak", "Подтёк масла по разъёму редуктора", 4300, "2026-08-07T14:05:00+00:00"),
    ("PCP-3", "seal", "leak", "Течь торцевого уплотнения", 8900, "2026-08-21T09:50:00+00:00"),
    ("PCP-3", "terminal_box", "overheat", "Потемнение корпуса клеммной коробки", 9400, "2026-10-02T13:15:00+00:00"),
    ("PCP-4", "frame", "corrosion", "Коррозия рамы", 9900, "2026-09-11T10:00:00+00:00"),
    ("PCP-4", "seal", "leak", "Течь торцевого уплотнения", 10300, "2026-10-19T15:30:00+00:00"),
]


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(event)")}
        for column in ("sign", "photo"):              # databases created before these columns existed
            if column not in columns:
                conn.execute(f"ALTER TABLE event ADD COLUMN {column} TEXT")
        if not conn.execute("SELECT COUNT(*) FROM user").fetchone()[0]:
            seed_users = json.loads((DATA_DIR / "users.seed.json").read_text(encoding="utf-8"))["users"]
            conn.executemany(
                "INSERT INTO user (username, name, role, password_hash) VALUES (?, ?, ?, ?)",
                [(u["username"], u["name"], u["role"], auth.hash_password(u["password"])) for u in seed_users],
            )
        if conn.execute("SELECT COUNT(*) FROM equipment").fetchone()[0]:
            # The characteristics are reference data kept in the code: bring an existing database up to date.
            conn.executemany("UPDATE equipment SET specs = :specs WHERE id = :id", [CENTRIFUGAL, *SCREW_UNITS])
            return
        seed = [(CENTRIFUGAL, CENTRIFUGAL_COMPONENTS, CENTRIFUGAL_EVENTS)]
        seed += [(unit, SCREW_COMPONENTS, []) for unit in SCREW_UNITS]
        for unit, components, events in seed:
            conn.execute(
                "INSERT INTO equipment (id, name, manufacturer, model, serial, location, commissioned_on, hours_base, "
                "has_telemetry, resource_hours, resource_source, maintenance_marks, specs) "
                "VALUES (:id, :name, :manufacturer, :model, :serial, :location, :commissioned_on, :hours_base, "
                ":has_telemetry, :resource_hours, :resource_source, :maintenance_marks, :specs)",
                unit,
            )
            conn.executemany(
                "INSERT INTO component (equipment_id, code, name, norm_hours, norm_source, installed_on, "
                "hours_at_install) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(unit["id"], *c) for c in components],
            )
            conn.executemany(
                "INSERT INTO event (equipment_id, component_id, kind, note, author, equipment_hours, created_at) "
                "VALUES (?, (SELECT id FROM component WHERE equipment_id = ? AND code = ?), ?, ?, ?, ?, ?)",
                [(unit["id"], unit["id"], *e) for e in events],
            )
        conn.executemany(
            "INSERT INTO event (equipment_id, component_id, kind, sign, note, author, equipment_hours, created_at) "
            "VALUES (?, (SELECT id FROM component WHERE equipment_id = ? AND code = ?), 'defect', ?, ?, ?, ?, ?)",
            [(unit, unit, code, sign, f"{note} (демонстрационная запись)", "Инженер", hours, at)
             for unit, code, sign, note, hours, at in SEED_DEFECTS],
        )


def list_equipment() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, name, manufacturer, model, serial, location, has_telemetry FROM equipment ORDER BY id")]


def get_equipment(equipment_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
        if row is None:
            return None
        components = conn.execute(
            "SELECT * FROM component WHERE equipment_id = ? ORDER BY id", (equipment_id,)
        ).fetchall()
        events = conn.execute(
            "SELECT e.*, c.name AS component_name FROM event e LEFT JOIN component c ON c.id = e.component_id "
            "WHERE e.equipment_id = ? ORDER BY e.id DESC LIMIT 50",
            (equipment_id,),
        ).fetchall()
        return {**dict(row), "components": [dict(c) for c in components], "events": [dict(e) for e in events]}


def add_event(equipment_id: str, component_id: int | None, kind: str, note: str | None,
              author: str | None, equipment_hours: float, sign: str | None = None, photo: str | None = None) -> dict:
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        if component_id is not None:
            owner = conn.execute("SELECT equipment_id FROM component WHERE id = ?", (component_id,)).fetchone()
            if owner is None or owner["equipment_id"] != equipment_id:
                raise ValueError("component does not belong to this equipment")
        cur = conn.execute(
            "INSERT INTO event (equipment_id, component_id, kind, note, author, equipment_hours, created_at, sign, photo) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (equipment_id, component_id, kind, note, author, equipment_hours, created_at, sign, photo),
        )
        if kind == "replacement" and component_id is not None:
            # A replaced component starts its service life again from the current motor-hours.
            conn.execute(
                "UPDATE component SET hours_at_install = ?, installed_on = ? WHERE id = ?",
                (equipment_hours, created_at[:10], component_id),
            )
        return {"id": cur.lastrowid, "created_at": created_at}


# ---------- users and sessions ----------

def _public(row) -> dict:
    return {"username": row["username"], "name": row["name"], "role": row["role"],
            "role_title": auth.ROLES[row["role"]]}


def login(username: str, password: str) -> tuple[str, dict] | None:
    """(session token, user) for a correct password, None otherwise."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM user WHERE username = ?", (username,)).fetchone()
        # Hash even for an unknown user, so the answer time does not reveal which names exist.
        stored = row["password_hash"] if row else auth.hash_password("unknown-user")
        if not auth.verify_password(password, stored) or row is None:
            return None
        token = auth.new_token()
        conn.execute("DELETE FROM session WHERE expires_at < ?", (time.time(),))
        conn.execute("INSERT INTO session (token_hash, username, expires_at) VALUES (?, ?, ?)",
                     (auth.token_hash(token), username, time.time() + auth.SESSION_SECONDS))
        return token, _public(row)


def session_user(token: str | None) -> dict | None:
    if not token:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT u.* FROM session s JOIN user u ON u.username = s.username "
            "WHERE s.token_hash = ? AND s.expires_at > ?", (auth.token_hash(token), time.time())).fetchone()
        return _public(row) if row else None


def logout(token: str | None) -> None:
    if token:
        with connect() as conn:
            conn.execute("DELETE FROM session WHERE token_hash = ?", (auth.token_hash(token),))


def list_users() -> list[dict]:
    with connect() as conn:
        return [_public(r) for r in conn.execute("SELECT * FROM user ORDER BY username")]


def add_user(username: str, name: str, role: str, password: str) -> dict:
    with connect() as conn:
        if conn.execute("SELECT 1 FROM user WHERE username = ?", (username,)).fetchone():
            raise ValueError("Пользователь с таким логином уже есть")
        conn.execute("INSERT INTO user (username, name, role, password_hash) VALUES (?, ?, ?, ?)",
                     (username, name, role, auth.hash_password(password)))
    return {"username": username, "name": name, "role": role, "role_title": auth.ROLES[role]}


def update_user(username: str, name: str, role: str, password: str | None, actor: str) -> dict:
    """Change the name, the role and, if given, the password. The last administrator keeps the role."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM user WHERE username = ?", (username,)).fetchone()
        if row is None:
            raise LookupError("Пользователь не найден")
        if row["role"] == "admin" and role != "admin":
            if username == actor:
                raise ValueError("Нельзя снять роль администратора с самого себя")
            if conn.execute("SELECT COUNT(*) FROM user WHERE role = 'admin'").fetchone()[0] <= 1:
                raise ValueError("В системе должен остаться хотя бы один администратор")
        conn.execute("UPDATE user SET name = ?, role = ? WHERE username = ?", (name, role, username))
        if password:
            conn.execute("UPDATE user SET password_hash = ? WHERE username = ?", (auth.hash_password(password), username))
            if username != actor:                          # a new password signs the person out everywhere
                conn.execute("DELETE FROM session WHERE username = ?", (username,))
    return {"username": username, "name": name, "role": role, "role_title": auth.ROLES[role]}


def delete_user(username: str, actor: str) -> None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM user WHERE username = ?", (username,)).fetchone()
        if row is None:
            raise LookupError("Пользователь не найден")
        if username == actor:
            raise ValueError("Нельзя удалить собственную учётную запись")
        if row["role"] == "admin" and conn.execute("SELECT COUNT(*) FROM user WHERE role = 'admin'").fetchone()[0] <= 1:
            raise ValueError("В системе должен остаться хотя бы один администратор")
        conn.execute("DELETE FROM session WHERE username = ?", (username,))
        conn.execute("DELETE FROM user WHERE username = ?", (username,))


# ---------- corrections of the history ----------

def update_event(event_id: int, note: str | None) -> dict:
    with connect() as conn:
        if conn.execute("SELECT 1 FROM event WHERE id = ?", (event_id,)).fetchone() is None:
            raise LookupError("Запись не найдена")
        conn.execute("UPDATE event SET note = ? WHERE id = ?", (note, event_id))
    return {"id": event_id, "note": note}


def delete_event(event_id: int) -> dict:
    """Removes the record and returns it, so the caller can delete its photo."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM event WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise LookupError("Запись не найдена")
        conn.execute("DELETE FROM event WHERE id = ?", (event_id,))
    return dict(row)


# ---------- defect statistics ----------

def stats(equipment_id: str | None = None) -> dict:
    """Counts of recorded defects: by sign, by component, by month, by unit."""
    where, args = ("WHERE e.equipment_id = ?", (equipment_id,)) if equipment_id else ("", ())
    with connect() as conn:
        rows = conn.execute(
            "SELECT e.equipment_id, e.kind, e.sign, e.created_at, c.name AS component_name FROM event e "
            f"LEFT JOIN component c ON c.id = e.component_id {where}", args).fetchall()
        units = [dict(r) for r in conn.execute("SELECT id, name FROM equipment ORDER BY id")
                 if not equipment_id or r["id"] == equipment_id]

    def count(key) -> dict:
        out: dict = {}
        for r in rows:
            if r["kind"] == "defect":
                out[key(r)] = out.get(key(r), 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    for unit in units:
        own = [r for r in rows if r["equipment_id"] == unit["id"]]
        unit["defects"] = sum(r["kind"] == "defect" for r in own)
        unit["inspections"] = sum(r["kind"] == "inspection" for r in own)
        unit["last_inspection"] = max((r["created_at"][:10] for r in own if r["kind"] == "inspection"), default=None)
    by_month = count(lambda r: r["created_at"][:7])
    return {
        "defects": sum(r["kind"] == "defect" for r in rows),
        "inspections": sum(r["kind"] == "inspection" for r in rows),
        "by_sign": count(lambda r: r["sign"] or "other"),
        "by_component": count(lambda r: r["component_name"] or "агрегат в целом"),
        "by_month": dict(sorted(by_month.items())[-6:]),
        "units": units,
    }


# ---------- walk-around route ----------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _round(conn, row) -> dict | None:
    if row is None:
        return None
    stops = [dict(r) for r in conn.execute(
        "SELECT s.equipment_id, s.position, s.status, s.note, s.at, q.name, q.model, q.serial, q.location "
        "FROM round_stop s JOIN equipment q ON q.id = s.equipment_id WHERE s.round_id = ? ORDER BY s.position",
        (row["id"],))]
    counts = {status: sum(s["status"] == status for s in stops) for status in ("inspected", "skipped", "pending")}
    return {**dict(row), "stops": stops, **counts, "total": len(stops),
            "next": next((s["equipment_id"] for s in stops if s["status"] == "pending"), None)}


def round_state() -> dict:
    """The round in progress (if any) and the last finished one."""
    with connect() as conn:
        active = conn.execute("SELECT * FROM round WHERE finished_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
        last = conn.execute("SELECT * FROM round WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        return {"active": _round(conn, active), "last": _round(conn, last)}


def round_start(author: str) -> dict:
    with connect() as conn:
        if conn.execute("SELECT 1 FROM round WHERE finished_at IS NULL").fetchone() is None:
            cur = conn.execute("INSERT INTO round (started_at, started_by) VALUES (?, ?)", (_now(), author))
            units = conn.execute("SELECT id FROM equipment ORDER BY location, id").fetchall()
            conn.executemany("INSERT INTO round_stop (round_id, equipment_id, position) VALUES (?, ?, ?)",
                             [(cur.lastrowid, unit["id"], n) for n, unit in enumerate(units, 1)])
    return round_state()


def round_mark(equipment_id: str, status: str, note: str | None = None) -> bool:
    """Set the status of a stop of the round in progress. False when there is no such stop."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE round_stop SET status = ?, note = ?, at = ? WHERE equipment_id = ? "
            "AND round_id = (SELECT id FROM round WHERE finished_at IS NULL ORDER BY id DESC LIMIT 1)",
            (status, note, _now(), equipment_id))
        return cur.rowcount > 0


def round_finish() -> dict:
    with connect() as conn:
        conn.execute("UPDATE round SET finished_at = ? WHERE finished_at IS NULL", (_now(),))
    return round_state()


# ---------- trainee results ----------

def add_training(equipment_id: str, user: dict, result: dict) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO training (equipment_id, username, name, score, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (equipment_id, user["username"], user["name"], result["score"], json.dumps(result, ensure_ascii=False), _now()))


def list_training(username: str | None = None, limit: int = 20) -> list[dict]:
    where, args = ("WHERE username = ?", (username,)) if username else ("", ())
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT equipment_id, username, name, score, created_at FROM training {where} ORDER BY id DESC LIMIT ?",
            (*args, limit))]
