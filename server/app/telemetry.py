"""Telemetry emulator: replays the customer's hourly rows as if they were live.

The rest of the app only talks to `Replayer`, so a real OPC UA / Modbus source can
replace it later behind the same methods.
"""
import csv
import time
from dataclasses import dataclass, field

from .config import DEFAULT_SPEED, HOURS_PER_ROW, TAGS, TELEMETRY_CSV

NORMAL = "Норма"
STATUS_ORDER = {"ok": 0, "warn": 1, "alarm": 2}


def tag_status(tag: str, value: float) -> str | None:
    """ok / warn / alarm for tags that have setpoints, None for the rest."""
    spec = TAGS[tag]
    if "warn" not in spec:
        return None
    if value > spec["alarm"]:
        return "alarm"
    if value > spec["warn"]:
        return "warn"
    return "ok"


def load_rows() -> list[dict]:
    with TELEMETRY_CSV.open(encoding="utf-8") as f:
        rows = []
        for raw in csv.DictReader(f):
            row = {"ts": raw["ts"], "label_vib": raw["label_vib"], "label_temp": raw["label_temp"]}
            for tag in TAGS:
                row[tag] = float(raw[tag])
            rows.append(row)
    return rows


def find_episodes(rows: list[dict], max_gap: int = 3) -> list[dict]:
    """Group consecutive rows the customer labelled as abnormal.

    Uses the reference labels only to offer "jump to episode" buttons for the demo;
    diagnostics must never read these labels.
    """
    episodes: list[dict] = []
    current = None
    for i, row in enumerate(rows):
        labels = {row["label_vib"], row["label_temp"]} - {NORMAL}
        if labels:
            if current and i - current["end"] <= max_gap + 1:
                current["end"] = i
                current["labels"].update(labels)
            else:
                current = {"start": i, "end": i, "labels": set(labels)}
                episodes.append(current)
            if current.get("alarm") is None and any(l.startswith("Тревога") for l in labels):
                current["alarm"] = i
    return [
        {
            "start": e["start"],
            "end": e["end"],
            "alarm": e.get("alarm", e["start"]),
            "start_ts": rows[e["start"]]["ts"],
            "end_ts": rows[e["end"]]["ts"],
            "labels": sorted(e["labels"]),
        }
        for e in episodes
    ]


@dataclass
class Replayer:
    rows: list[dict] = field(default_factory=load_rows)
    speed: float = DEFAULT_SPEED
    paused: bool = False
    _base_index: float = 0.0
    _base_time: float = field(default_factory=time.monotonic)

    def _position(self) -> float:
        if self.paused:
            return self._base_index
        pos = self._base_index + (time.monotonic() - self._base_time) * self.speed
        return min(pos, len(self.rows) - 1)

    @property
    def index(self) -> int:
        return int(self._position())

    def seek(self, index: int) -> None:
        self._base_index = float(max(0, min(index, len(self.rows) - 1)))
        self._base_time = time.monotonic()

    def set_speed(self, speed: float) -> None:
        self.seek_float(self._position())
        self.speed = max(0.0, speed)

    def set_paused(self, paused: bool) -> None:
        self.seek_float(self._position())
        self.paused = paused

    def seek_float(self, position: float) -> None:
        self._base_index = position
        self._base_time = time.monotonic()

    def running_hours(self, upto: int | None = None) -> float:
        """Motor-hours accumulated since the start of the data: only rows with the unit running."""
        upto = self.index if upto is None else upto
        return sum(HOURS_PER_ROW for r in self.rows[: upto + 1] if r["rpm"] > 0)

    def snapshot(self, index: int | None = None) -> dict:
        index = self.index if index is None else index
        row = self.rows[index]
        values = {}
        worst = "ok"
        for tag, spec in TAGS.items():
            status = tag_status(tag, row[tag])
            values[tag] = {"value": row[tag], "status": status, "title": spec["title"], "unit": spec["unit"],
                           "warn": spec.get("warn"), "alarm": spec.get("alarm")}
            if status and STATUS_ORDER[status] > STATUS_ORDER[worst]:
                worst = status
        return {
            "index": index,
            "total": len(self.rows),
            "ts": row["ts"],
            "running": row["rpm"] > 0,
            "status": worst,
            "values": values,
            "speed": self.speed,
            "paused": self.paused,
        }

    def history(self, count: int) -> list[dict]:
        end = self.index + 1
        start = max(0, end - count)
        return [{"ts": r["ts"], **{tag: r[tag] for tag in TAGS}} for r in self.rows[start:end]]
