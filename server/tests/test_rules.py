"""Rules against the customer's data.

    .venv/Scripts/python -m unittest discover -s tests -v

The reference "Диагноз" columns are used here only as the expected answer.
"""
import unittest

from app.inspection import Finding, FrameTracker, expected_signs
from app.rules import Context, evaluate
from app.telemetry import Replayer, find_episodes
from app.vision import Detection

COMPONENTS = {
    code: {"name": name, "norm_hours": None, "resource": {"status": "unknown"}}
    for code, name in [("motor", "Электродвигатель"), ("terminal_box", "Клеммная коробка"),
                       ("coupling", "Муфта"), ("bearing", "Подшипниковый узел"), ("seal", "Уплотнение"),
                       ("impeller", "Рабочее колесо"), ("frame", "Рама")]
}

# reference label -> (rule ids that may explain it, level)
EXPECTED = {
    "Пред: Рост дисбаланса": ({"imbalance"}, "warn"),
    "Тревога: Выс. дисбаланс (РК)": ({"imbalance"}, "alarm"),
    "Пред: Ухудшение центровки": ({"misalignment"}, "warn"),
    "Тревога: Расцентровка": ({"misalignment"}, "alarm"),
    "Пред: Износ/Шум": ({"hf_noise", "bearing_wear"}, "warn"),
    "Тревога: Дефект подшипника": ({"hf_noise", "bearing_wear"}, "alarm"),
    "Пред: Рост темп. подш.": ({"bearing_wear", "bearing_overheat"}, "warn"),
    "Тревога: Перегрев подш. насоса": ({"bearing_wear", "bearing_overheat"}, "alarm"),
    "Пред: Рост темп. ЭД": ({"winding_overheat"}, "warn"),
    "Тревога: Перегрев обмоток ЭД": ({"winding_overheat"}, "alarm"),
}
ORDER = {"ok": 0, "warn": 1, "alarm": 2}


def context(row, findings=(), inspected=(), hours=100.0, events=()):
    return Context(
        values={k: v for k, v in row.items() if isinstance(v, float)},
        hours=hours, components=COMPONENTS, events=list(events),
        findings={(f.component, f.sign): f for f in findings}, inspected=set(inspected),
        maintenance_marks=(4000, 8000, 16000),
    )


def camera(component, sign, score=0.8):
    return Finding(component, sign, score, "camera", "ts")


class SensorRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = Replayer().rows
        cls.episodes = find_episodes(cls.rows)

    def test_every_row_matches_reference_labels(self):
        for row in self.rows:
            result = evaluate(context(row))
            fired = {d["id"]: d["level"] for d in result["diagnoses"]}
            labels = [l for l in (row["label_vib"], row["label_temp"]) if l != "Норма"]
            with self.subTest(ts=row["ts"]):
                if not labels:
                    self.assertEqual(fired, {})
                for label in labels:
                    ids, level = EXPECTED[label]
                    matched = ids & fired.keys()
                    self.assertTrue(matched, f"{label}: fired {fired}")
                    # a rule that joins two tags reports the worse of them
                    self.assertGreaterEqual(max(ORDER[fired[i]] for i in matched), ORDER[level])
                expected_ids = set().union(*(EXPECTED[l][0] for l in labels)) if labels else set()
                self.assertLessEqual(fired.keys(), expected_ids, "unexpected diagnosis")

    def test_four_episodes_found(self):
        self.assertEqual([e["start_ts"][:10] for e in self.episodes],
                         ["2026-11-15", "2026-11-20", "2026-11-25", "2026-11-29"])


class FusionRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rows = Replayer().rows
        cls.normal = rows[0]
        cls.imbalance, cls.misalignment, cls.bearing, cls.winding = (rows[e["alarm"]] for e in find_episodes(rows))

    def ids(self, result):
        return [d["id"] for d in result["diagnoses"]]

    def test_frame_corrosion_turns_imbalance_into_looseness(self):
        before = evaluate(context(self.imbalance))
        self.assertEqual(self.ids(before), ["imbalance"])
        self.assertEqual([g["component"] for g in before["guidance"]], ["frame"])

        after = evaluate(context(self.imbalance, [camera("frame", "corrosion")]))
        self.assertEqual(self.ids(after), ["looseness"])
        self.assertEqual(after["guidance"], [])
        self.assertIn("Уточняет гипотезу по датчикам: «Дисбаланс ротора»", after["diagnoses"][0]["evidence"])

    def test_seal_leak_with_bearing_deviation_is_seal_degradation(self):
        after = evaluate(context(self.bearing, [camera("seal", "leak")]))
        self.assertEqual(self.ids(after), ["seal_degradation"])
        self.assertEqual(after["status"], "alarm")

    def test_seal_leak_alone_is_a_check(self):
        result = evaluate(context(self.normal, [camera("seal", "leak")]))
        self.assertEqual(self.ids(result), ["seal_leak"])
        self.assertEqual(result["status"], "warn")

    def test_terminal_marks_with_hot_winding_is_bad_contact(self):
        after = evaluate(context(self.winding, [camera("terminal_box", "overheat")]))
        self.assertEqual(self.ids(after), ["bad_contact"])

    def test_misalignment_asks_to_look_at_coupling_until_inspected(self):
        self.assertEqual([g["component"] for g in evaluate(context(self.misalignment))["guidance"]], ["coupling"])
        self.assertEqual(evaluate(context(self.misalignment, inspected=["coupling"]))["guidance"], [])

    def test_missing_guard_fires_without_sensor_deviation(self):
        manual = Finding("coupling", "guard_missing", None, "manual", "ts")
        result = evaluate(context(self.normal, [manual]))
        self.assertEqual(self.ids(result), ["guard_missing"])
        self.assertEqual(result["status"], "alarm")

    def test_unrelated_corrosion_is_reported_on_its_own(self):
        weak = evaluate(context(self.normal, [camera("motor", "corrosion", 0.4)]))
        self.assertEqual((self.ids(weak), weak["status"]), (["visual:motor:corrosion"], "warn"))
        strong = evaluate(context(self.normal, [camera("motor", "corrosion", 0.9)]))
        self.assertEqual(strong["status"], "alarm")

    def test_component_text_distinguishes_not_inspected_from_clear(self):
        result = evaluate(context(self.normal, inspected=["seal"]))
        self.assertEqual(result["components"]["seal"]["text"], "Видимых проблем не обнаружено")
        self.assertEqual(result["components"]["frame"]["text"], "Не осмотрен")


class MaintenanceAndResource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.normal = Replayer().rows[0]

    def test_service_mark(self):
        ids = lambda **kw: [d["id"] for d in evaluate(context(self.normal, **kw))["diagnoses"]]
        self.assertEqual(ids(hours=3999), [])
        self.assertEqual(ids(hours=4000), ["maintenance_due"])
        done = [{"kind": "maintenance", "equipment_hours": 4010}]
        self.assertEqual(ids(hours=7999, events=done), [])
        self.assertEqual(ids(hours=8000, events=done), ["maintenance_due"])

    def test_resource_running_out(self):
        components = dict(COMPONENTS)
        components["seal"] = {"name": "Уплотнение", "norm_hours": 8000, "norm_source": "принято командой",
                              "resource": {"status": "warn", "used_hours": 7300, "remaining_hours": 700}}
        ctx = context(self.normal)
        ctx.components = components
        result = evaluate(ctx)
        self.assertEqual([d["id"] for d in result["diagnoses"]], ["resource:seal"])
        self.assertEqual(result["components"]["seal"]["level"], "warn")


class NoTelemetry(unittest.TestCase):
    def test_unknown_values_fire_no_sensor_rule_and_are_not_normal(self):
        leak = camera("seal", "leak")
        ctx = Context(values={}, hours=100.0, components=COMPONENTS, findings={("seal", "leak"): leak})
        result = evaluate(ctx)
        # neither "seal degradation" (needs a deviation) nor "leak, sensors normal" (needs a reading)
        self.assertEqual([d["id"] for d in result["diagnoses"]], ["visual:seal:leak"])
        self.assertFalse(result["telemetry"])

    def test_unit_resource_from_passport(self):
        ctx = Context(values={}, hours=10650.0, components=COMPONENTS,
                      unit_resource={"norm": 11000, "used": 10650, "remaining": 350, "status": "warn",
                                     "source": "паспорт агрегата"})
        self.assertEqual([d["id"] for d in evaluate(ctx)["diagnoses"]], ["resource:unit"])


class SignPlaces(unittest.TestCase):
    def test_signs_expected_on_a_component(self):
        self.assertEqual(expected_signs("terminal_box"), {"corrosion", "overheat"})
        self.assertEqual(expected_signs("seal"), {"corrosion", "leak"})
        self.assertEqual(expected_signs("bearing"), {"corrosion", "leak", "overheat"})
        self.assertEqual(expected_signs("dts"), {"corrosion"})


class Tracker(unittest.TestCase):
    def test_sign_needs_three_frames(self):
        tracker = FrameTracker()
        hit = [Detection("leak", "Течь", 0.5, (0, 0, 1, 1))]
        self.assertEqual(tracker.update(hit), {})
        self.assertEqual(tracker.update([]), {})
        self.assertEqual(tracker.update(hit), {})
        self.assertEqual(tracker.update(hit), {"leak": 0.5})

    def test_low_confidence_is_still_recorded(self):
        tracker = FrameTracker()
        for _ in range(3):
            confirmed = tracker.update([Detection("leak", "Течь", 0.1, (0, 0, 1, 1))])
        self.assertEqual(confirmed, {"leak": 0.1})

    def test_unknown_labels_are_ignored(self):
        tracker = FrameTracker()
        for _ in range(5):
            confirmed = tracker.update([Detection("graffiti", "?", 0.9, (0, 0, 1, 1))])
        self.assertEqual(confirmed, {})


if __name__ == "__main__":
    unittest.main()
