"""Sign-in, roles and reports through the HTTP API, on a throwaway database and the stub detector."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ["EQUIPVISION_DETECTOR"] = "stub"      # no GPU model needed for these tests

from fastapi.testclient import TestClient  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402

from app import config, db, photos  # noqa: E402
from app.main import app  # noqa: E402

SEED = {u["role"]: u for u in json.loads((config.DATA_DIR / "users.seed.json").read_text(encoding="utf-8"))["users"]}


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls.tmp.name) / "test.sqlite3"
        photos.PHOTO_DIR = Path(cls.tmp.name) / "photos"
        cls.server = TestClient(app)
        cls.server.__enter__()                    # runs startup: creates and seeds the database

    @classmethod
    def tearDownClass(cls):
        cls.server.__exit__(None, None, None)
        cls.tmp.cleanup()

    def client(self, role: str | None = None) -> TestClient:
        client = TestClient(app)
        if role:
            account = SEED[role]
            res = client.post("/api/login", json={"username": account["username"], "password": account["password"]})
            self.assertEqual(res.status_code, 200, res.text)
        return client

    def test_api_is_closed_without_sign_in(self):
        anonymous = self.client()
        for path in ("/api/equipment", "/api/equipment/P-101", "/api/telemetry/current", "/api/users",
                     "/api/equipment/P-101/report.pdf"):
            self.assertEqual(anonymous.get(path).status_code, 401, path)
        self.assertEqual(anonymous.post("/api/identify", content=b"x").status_code, 401)
        self.assertEqual(anonymous.get("/").status_code, 200)          # the client itself is public

    def test_wrong_password_and_unknown_user(self):
        anonymous = self.client()
        self.assertEqual(anonymous.post("/api/login", json={"username": "engineer", "password": "nope"}).status_code, 401)
        self.assertEqual(anonymous.post("/api/login", json={"username": "ghost", "password": "nope"}).status_code, 401)

    def test_password_is_not_stored_in_clear(self):
        with db.connect() as conn:
            stored = conn.execute("SELECT password_hash FROM user WHERE username = 'engineer'").fetchone()[0]
        self.assertNotIn(SEED["engineer"]["password"], stored)
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))

    def test_logout_ends_the_session(self):
        client = self.client("engineer")
        self.assertEqual(client.get("/api/me").json()["role"], "engineer")
        client.post("/api/logout")
        self.assertEqual(client.get("/api/equipment").status_code, 401)

    def test_engineer_inspects_but_cannot_register_work(self):
        engineer = self.client("engineer")
        self.assertEqual(engineer.post("/api/equipment/PCP-1/events", json={"kind": "inspection"}).status_code, 201)
        self.assertEqual(engineer.post("/api/equipment/PCP-1/events", json={"kind": "maintenance"}).status_code, 403)
        self.assertEqual(engineer.post("/api/equipment/PCP-1/events", json={"kind": "replacement"}).status_code, 403)
        self.assertEqual(engineer.get("/api/users").status_code, 403)

    def test_master_registers_work_and_author_comes_from_the_session(self):
        master = self.client("master")
        res = master.post("/api/equipment/PCP-2/events", json={"kind": "maintenance", "note": "ТО", "author": "чужое имя"})
        self.assertEqual(res.status_code, 201)
        event = master.get("/api/equipment/PCP-2").json()["events"][0]
        self.assertEqual(event["author"], SEED["master"]["name"])
        self.assertEqual(master.get("/api/users").status_code, 403)

    def test_admin_manages_users(self):
        admin = self.client("admin")
        new = {"username": "newcomer", "name": "Новый инженер", "role": "engineer", "password": "long-enough-1"}
        self.assertEqual(admin.post("/api/users", json=new).status_code, 201)
        self.assertEqual(admin.post("/api/users", json=new).status_code, 400)                 # duplicate
        self.assertEqual(admin.post("/api/users", json={**new, "username": "x2", "password": "short"}).status_code, 422)
        self.assertIn("newcomer", [u["username"] for u in admin.get("/api/users").json()])
        fresh = TestClient(app)
        self.assertEqual(fresh.post("/api/login", json={"username": "newcomer", "password": "long-enough-1"}).status_code, 200)

    def test_reports(self):
        engineer = self.client("engineer")
        xlsx = engineer.get("/api/equipment/P-101/report.xlsx")
        self.assertEqual(xlsx.status_code, 200)
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(xlsx.content))
        self.assertEqual(book.sheetnames, ["Сводка", "Диагностика", "Узлы", "История", "Статистика дефектов"])
        self.assertIn("P-101", book["Сводка"]["A1"].value)
        self.assertEqual(book["Узлы"].max_row, 1 + 8)                                           # header + components
        summary = " ".join(str(cell.value) for row in book["Сводка"].iter_rows() for cell in row)
        self.assertIn(SEED["engineer"]["name"], summary)                                        # who made the report

        pdf = engineer.get("/api/equipment/PCP-4/report.pdf")
        self.assertEqual(pdf.status_code, 200, pdf.text[:200])
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertIn("attachment", pdf.headers["content-disposition"])

    def test_passport_data_and_documents(self):
        engineer = self.client("engineer")
        specs = engineer.get("/api/equipment/PCP-3").json()["specs"]
        self.assertEqual(specs["Общие сведения (паспорт агрегата)"]["Серийный номер"], "21031004-3")
        self.assertEqual(specs["Технические характеристики (паспорт агрегата)"]["Масса"], "430 кг")

        listing = engineer.get("/api/equipment/PCP-3/documents").json()
        self.assertIsNone(listing["note"])
        passport = listing["documents"][0]
        self.assertEqual((passport["group"], passport["pages"]), ("Паспорта", 7))
        self.assertIn("21031004-3", passport["title"])
        self.assertGreaterEqual(len(listing["documents"]), 12)

        page = engineer.get("/api/equipment/PCP-3/documents/0/pages/4")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.content[:3], b"\xff\xd8\xff")                                       # a JPEG
        self.assertEqual(engineer.get("/api/equipment/PCP-3/documents/0/pages/8").status_code, 404)
        self.assertEqual(engineer.get("/api/equipment/PCP-3/documents/99/pages/1").status_code, 404)
        self.assertEqual(engineer.get("/api/equipment/PCP-3/documents/0/pages/1?rotate=45").status_code, 404)
        self.assertEqual(self.client().get("/api/equipment/PCP-3/documents").status_code, 401)

        demo = engineer.get("/api/equipment/P-101/documents").json()
        self.assertIn("Паспорта на этот агрегат", demo["note"])                                     # said openly

    def rusty_frame(self) -> bytes:
        """A frame the stub detector calls corrosion."""
        image = Image.new("RGB", (640, 480), (110, 120, 130))
        ImageDraw.Draw(image).ellipse((380, 200, 520, 290), fill=(181, 84, 28))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=70)
        return buffer.getvalue()

    def look_at(self, client, equipment_id: str, component: str, frames: int = 6) -> dict:
        with client.websocket_connect("/ws/vision") as ws:
            ws.send_text(json.dumps({"type": "focus", "equipment_id": equipment_id, "component": component}))
            for _ in range(frames):
                ws.send_bytes(self.rusty_frame())
                answer = ws.receive_json()
        return answer

    def test_defect_photo_goes_to_history_statistics_and_report(self):
        engineer = self.client("engineer")
        self.look_at(engineer, "PCP-2", "frame")
        finding = engineer.get("/api/equipment/PCP-2/diagnosis").json()["findings"][0]
        self.assertEqual((finding["component"], finding["sign"]), ("frame", "corrosion"))
        shot = engineer.get(f"/api/photos/{finding['photo']}")
        self.assertEqual((shot.status_code, shot.content[:3]), (200, bytes([0xFF, 0xD8, 0xFF])))      # a JPEG
        self.assertEqual(engineer.get("/api/photos/..%2F..%2Fequipvision.sqlite3").status_code, 404)
        self.assertEqual(self.client().get(f"/api/photos/{finding['photo']}").status_code, 401)

        before = engineer.get("/api/equipment/PCP-2/stats").json()
        pdf = engineer.get("/api/equipment/PCP-2/report.pdf")              # an unsaved finding is in the report too
        self.assertEqual(pdf.status_code, 200, pdf.text[:200])
        self.assertGreater(len(pdf.content), 30_000)                        # the photo is embedded

        engineer.post("/api/equipment/PCP-2/inspection/complete")
        event = next(e for e in engineer.get("/api/equipment/PCP-2").json()["events"] if e["kind"] == "defect")
        self.assertEqual((event["sign"], event["photo"]), ("corrosion", finding["photo"]))
        after = engineer.get("/api/equipment/PCP-2/stats").json()
        self.assertEqual(after["defects"], before["defects"] + 1)
        self.assertIn("corrosion", [row["sign"] for row in after["by_sign"]])
        self.assertIn("Рама и анкерные болты", after["by_component"])

        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(engineer.get("/api/equipment/PCP-2/report.xlsx").content))
        self.assertIn("Фото дефектов", book.sheetnames)
        self.assertEqual(len(book["Фото дефектов"]._images), 1)

        fleet = engineer.get("/api/stats").json()
        self.assertEqual(len(fleet["units"]), 5)
        self.assertGreaterEqual(fleet["defects"], after["defects"])

    def test_photo_of_an_unsaved_finding_is_removed_on_reset(self):
        engineer = self.client("engineer")
        self.look_at(engineer, "PCP-1", "frame")
        name = engineer.get("/api/equipment/PCP-1/diagnosis").json()["findings"][0]["photo"]
        engineer.post("/api/equipment/PCP-1/inspection/reset")
        self.assertEqual(engineer.get(f"/api/photos/{name}").status_code, 404)

    def test_engineer_removes_a_wrong_finding_and_the_camera_does_not_put_it_back(self):
        engineer = self.client("engineer")
        engineer.post("/api/equipment/PCP-3/inspection/reset")
        self.look_at(engineer, "PCP-3", "frame")
        finding = engineer.get("/api/equipment/PCP-3/diagnosis").json()["findings"][0]
        ref = {"component": finding["component"], "sign": finding["sign"]}
        self.assertEqual(self.client().post("/api/equipment/PCP-3/findings/remove", json=ref).status_code, 401)

        after = engineer.post("/api/equipment/PCP-3/findings/remove", json=ref)
        self.assertEqual((after.status_code, after.json()["findings"]), (200, []))
        self.assertFalse(any(d["id"].startswith("visual:") for d in after.json()["diagnoses"]))
        self.assertEqual(engineer.get(f"/api/photos/{finding['photo']}").status_code, 404)
        self.assertEqual(engineer.post("/api/equipment/PCP-3/findings/remove", json=ref).status_code, 404)

        self.look_at(engineer, "PCP-3", "frame")                          # the camera still sees the same thing
        self.assertEqual(engineer.get("/api/equipment/PCP-3/diagnosis").json()["findings"], [])
        engineer.post("/api/equipment/PCP-3/findings", json={**ref, "note": "всё-таки есть"})      # by hand it comes back
        self.assertEqual(len(engineer.get("/api/equipment/PCP-3/diagnosis").json()["findings"]), 1)
        engineer.post("/api/equipment/PCP-3/inspection/reset")

    def test_round_tracks_inspected_and_skipped_units(self):
        engineer = self.client("engineer")
        active = engineer.post("/api/round/start").json()["active"]
        self.assertEqual((active["total"], active["pending"], active["next"]), (5, 5, active["stops"][0]["equipment_id"]))
        self.assertEqual(engineer.post("/api/round/start").json()["active"]["id"], active["id"])      # no second round

        self.assertEqual(engineer.post("/api/round/skip", json={"equipment_id": "PCP-4", "reason": "x"}).status_code, 422)
        engineer.post("/api/round/skip", json={"equipment_id": "PCP-4", "reason": "нет допуска в помещение"})
        saved = engineer.post("/api/equipment/P-101/inspection/complete").json()
        state = engineer.get("/api/round").json()["active"]
        by_id = {s["equipment_id"]: s for s in state["stops"]}
        self.assertEqual((by_id["P-101"]["status"], by_id["PCP-4"]["status"], by_id["PCP-4"]["note"]),
                         ("inspected", "skipped", "нет допуска в помещение"))
        self.assertEqual((state["inspected"], state["skipped"], state["pending"]), (1, 1, 3))
        self.assertEqual(saved["next"], state["next"])

        done = engineer.post("/api/round/finish").json()
        self.assertIsNone(done["active"])
        self.assertEqual((done["last"]["inspected"], done["last"]["pending"]), (1, 3))               # 3 never visited
        self.assertEqual(engineer.post("/api/round/skip", json={"equipment_id": "P-101", "reason": "после обхода"}).status_code, 404)

    def test_trainee_mode_scores_the_inspection(self):
        engineer = self.client("engineer")
        steps = engineer.get("/api/equipment/PCP-3/training").json()
        self.assertEqual(len(steps), 8)
        self.assertTrue(all(step["what"] and step["how"] and step["normal"] for step in steps))

        engineer.post("/api/equipment/PCP-3/inspection/reset")
        nothing = engineer.post("/api/equipment/PCP-3/training", json={"answers": {}}).json()
        self.assertEqual((nothing["score"], nothing["inspected"]), (0, 0))

        self.look_at(engineer, "PCP-3", "frame", frames=7)                  # the camera records corrosion there
        missed = engineer.post("/api/equipment/PCP-3/training", json={"answers": {"frame": []}}).json()
        step = next(s for s in missed["steps"] if s["component"] == "frame")
        self.assertEqual((step["missed"], step["ok"], missed["attention"]), (["corrosion"], False, 0.0))
        found = engineer.post("/api/equipment/PCP-3/training", json={"answers": {"frame": ["corrosion"]}}).json()
        self.assertEqual((found["attention"], found["inspected"], found["total"]), (1.0, 1, 8))
        self.assertGreater(found["score"], missed["score"])
        engineer.post("/api/equipment/PCP-3/inspection/reset")

        self.assertEqual({r["username"] for r in engineer.get("/api/training").json()}, {"engineer"})
        self.assertGreaterEqual(len(self.client("master").get("/api/training").json()), 3)           # the mentor sees all

    def test_voice_endpoint(self):
        from app import voice
        engineer = self.client("engineer")
        self.assertEqual(self.client().post("/api/voice", content=bytes(32000)).status_code, 401)
        if not voice.Recogniser().available:
            self.assertEqual(engineer.post("/api/voice", content=bytes(32000)).status_code, 503)
            return
        self.assertEqual(engineer.post("/api/voice", content=b"short").status_code, 400)
        silence = engineer.post("/api/voice", content=bytes(32000)).json()               # one second of silence
        self.assertEqual((silence["text"], silence["match"], silence["candidates"]), ("", None, []))

    def test_note_starting_with_equals_is_not_a_formula(self):
        engineer = self.client("engineer")
        engineer.post("/api/equipment/PCP-3/events", json={"kind": "inspection", "note": "=1+1"})
        from openpyxl import load_workbook
        sheet = load_workbook(io.BytesIO(engineer.get("/api/equipment/PCP-3/report.xlsx").content))["История"]
        cell = sheet.cell(row=2, column=6)
        self.assertEqual((cell.value, cell.data_type), ("=1+1", "s"))

    def test_camera_socket_needs_a_session(self):
        from starlette.websockets import WebSocketDisconnect
        with self.assertRaises(WebSocketDisconnect) as closed:
            with self.client().websocket_connect("/ws/vision") as ws:
                ws.receive_json()
        self.assertEqual(closed.exception.code, 4401)
        with self.client("engineer").websocket_connect("/ws/vision") as ws:
            ws.send_bytes(b"not a jpeg")
            self.assertIn("error", ws.receive_json())


if __name__ == "__main__":
    unittest.main()
