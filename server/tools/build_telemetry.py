"""Merge the customer's vibration and temperature workbooks into data/telemetry.csv.

Run once (and again if the source workbooks change):
    .venv/Scripts/python tools/build_telemetry.py
"""
import csv
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[2]
CASE = ROOT / "docs" / "Кейс" / "Кейс"
OUT = Path(__file__).resolve().parents[1] / "data" / "telemetry.csv"

VIB = CASE / "Вибрация" / "БД значений (вибрация).xlsx"
TEMP = CASE / "Температура" / "БД значений (температура).xlsx"


def rows(path: Path) -> list[tuple]:
    ws = load_workbook(path, read_only=True, data_only=True).worksheets[0]
    data = [r for r in ws.iter_rows(min_row=2, values_only=True) if r[0] is not None]
    return data


def main() -> None:
    vib = {str(r[0]): r for r in rows(VIB)}
    temp = {str(r[0]): r for r in rows(TEMP)}
    if vib.keys() != temp.keys():
        raise SystemExit("Timestamps differ between the two workbooks")

    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "rpm", "vib_rms", "vib_1x", "vib_2x", "vib_hf",
                    "temp_bearing", "temp_winding", "label_vib", "label_temp"])
        for ts in vib:
            _, rpm, rms, a1, a2, hf, label_v = vib[ts][:7]
            _, rpm_t, t_bearing, t_winding, label_t = temp[ts][:5]
            if rpm != rpm_t:
                raise SystemExit(f"RPM differs at {ts}: {rpm} vs {rpm_t}")
            w.writerow([ts, rpm, round(rms, 2), round(a1, 2), round(a2, 2), round(hf, 2),
                        round(t_bearing, 1), round(t_winding, 1), label_v, label_t])
    print(f"{len(vib)} rows -> {OUT}")


if __name__ == "__main__":
    main()
