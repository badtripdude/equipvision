"""Trainee mode: what to check on each component and how, and a score for the inspection.

The checklist is general inspection practice for pump units, in line with the customer's list of
visual defects and diagnostic rules; it does not replace the manufacturer's manual, which is
available in the passport section.

Scoring compares the trainee with what the system itself registered during the same walk-around:
  completeness — on how many components the camera was actually held and an answer given;
  attention    — how many of the signs the camera recorded the trainee reported too.
The camera is not an infallible reference: the result is feedback for the trainee and the
mentor, not a certification.
"""
from .inspection import CAMERA_SIGNS, INSPECTED_FRAMES, SIGNS, Session

GENERIC = {
    "what": ["Целостность корпуса и крепежа", "Следы коррозии, подтёки, посторонние предметы"],
    "how": "Осмотрите узел со всех доступных сторон, держите камеру неподвижно 2–3 секунды.",
    "normal": "Поверхность сухая и чистая, крепёж на месте, повреждений нет.",
}

CHECKLIST = {
    "motor": {
        "what": ["Рёбра охлаждения: не погнуты, не забиты грязью", "Изменение цвета краски у подшипниковых щитов",
                 "Наличие и целостность провода заземления", "Изоляция подводящего кабеля"],
        "how": "Обойдите двигатель, снимите корпус сбоку и со стороны вентилятора.",
        "normal": "Краска заводского цвета, рёбра чистые, заземление подключено, кабель без трещин и оплавления.",
    },
    "terminal_box": {
        "what": ["Потемнение, оплавление, нагар на корпусе и крышке", "Окисление контактов, следы перегрева изоляции",
                 "Герметичность кабельного ввода"],
        "how": "Снимайте коробку вблизи. Крышку открывает только персонал с допуском и при снятом напряжении.",
        "normal": "Корпус чистый, без потемнений и запаха гари, кабельный ввод затянут.",
    },
    "coupling": {
        "what": ["Защитный кожух на месте и закреплён", "Нет следов масла и резиновой крошки под муфтой",
                 "Нет следов перегрева на кожухе"],
        "how": "Снимайте зону между двигателем и насосом. Не приближайте руки и телефон к вращающимся частям.",
        "normal": "Кожух установлен без смещения, весь крепёж на месте.",
    },
    "bearing": {
        "what": ["Подтёки масла или смазки из-под крышек", "Изменение цвета краски от нагрева",
                 "Уровень и цвет масла в смотровом стекле, если оно есть"],
        "how": "Снимайте подшипниковый узел сбоку и снизу, где собираются подтёки.",
        "normal": "Узел сухой, краска без побежалости, масло прозрачное и не ниже отметки.",
    },
    "seal": {
        "what": ["Капли, влажное пятно или блеск жидкости в зоне вала", "Налёт или кристаллы продукта вокруг уплотнения",
                 "Ржавые подтёки ниже уплотнения"],
        "how": "Снимайте место выхода вала из корпуса насоса и поверхность под ним.",
        "normal": "Зона вала сухая. Допустимую утечку для конкретного уплотнения смотрите в инструкции по эксплуатации.",
    },
    "impeller": {
        "what": ["Течи по разъёму корпуса и фланцам", "Коррозия и отслоение краски на корпусе", "Трещины корпуса"],
        "how": "Проточная часть закрыта: осматривается корпус насоса, его разъём и фланцы.",
        "normal": "Корпус сухой, без трещин, фланцевые соединения без подтёков.",
    },
    "stator": {
        "what": ["Течи по фланцам статора и корпуса насоса", "Коррозия и вмятины на корпусе", "Состояние стяжных шпилек"],
        "how": "Пройдите вдоль насоса от приёмной камеры до напорного фланца.",
        "normal": "Корпус сухой, шпильки затянуты, фланцы без подтёков.",
    },
    "joints": {
        "what": ["Подтёки смазки в зоне шарнирных узлов", "Следы перегрева на корпусе в зоне шарниров"],
        "how": "Шарниры закрыты корпусом: осматривается корпус в зоне приёмной камеры и люк.",
        "normal": "Корпус сухой, люк закрыт, пробки на месте.",
    },
    "gearbox": {
        "what": ["Подтёки масла по разъёму корпуса и у валов", "Изменение цвета краски от нагрева", "Пробки и сапун на месте"],
        "how": "Снимайте редуктор снизу и со стороны выходного вала.",
        "normal": "Корпус сухой и чистый, краска без побежалости.",
    },
    "frame": {
        "what": ["Коррозия рамы и опор", "Анкерные болты: все на месте, гайки не выступают и не ослаблены",
                 "Трещины фундамента, лужи под агрегатом"],
        "how": "Снимайте каждую опору и болты вблизи, под углом сверху.",
        "normal": "Рама окрашена, болты затянуты, под агрегатом сухо.",
    },
    "gauge": {
        "what": ["Стекло целое, стрелка не дрожит и не лежит на упоре", "Течи по резьбе и импульсной линии",
                 "Показание в рабочей зоне шкалы"],
        "how": "Снимайте прибор прямо, без бликов на стекле.",
        "normal": "Прибор целый, соединения сухие.",
    },
    "dts": {
        "what": ["Корпус датчика и кабельный ввод целы", "Кабель не перетёрт и закреплён"],
        "how": "Снимайте датчик и место его установки на статоре.",
        "normal": "Датчик закреплён, кабель без повреждений.",
    },
}


def steps_for(card: dict) -> list[dict]:
    return [{"component": c["code"], "name": c["name"], **CHECKLIST.get(c["code"], GENERIC)} for c in card["components"]]


def score(card: dict, session: Session, answers: dict[str, list[str]]) -> dict:
    """answers: component code -> signs the trainee reported (an empty list means "nothing found")."""
    steps, looked_n, found_n, expected_n = [], 0, 0, 0
    for component in card["components"]:
        code = component["code"]
        looked = session.frames.get(code, 0) >= INSPECTED_FRAMES
        answered = code in answers
        reported = {s for s in answers.get(code, []) if s in SIGNS}
        camera = {sign for (c, sign), f in session.findings.items() if c == code and f.source == "camera"}
        found, missed = camera & reported, camera - reported
        unconfirmed = {s for s in reported - camera if s in CAMERA_SIGNS}      # the camera could have seen it, but did not
        done = looked and answered
        looked_n += done
        found_n += len(found)
        expected_n += len(camera)

        if not looked:
            comment = "Камера не была наведена на узел достаточно долго: узел считается неосмотренным."
        elif not answered:
            comment = "Узел осмотрен камерой, но результат не отмечен."
        elif missed:
            comment = "Пропущено: " + ", ".join(SIGNS[s] for s in sorted(missed)) + ". Сверьтесь с подсказкой, что искать на этом узле."
        elif unconfirmed:
            comment = ("Отмечено, но камера этого не зафиксировала: " + ", ".join(SIGNS[s] for s in sorted(unconfirmed))
                       + ". Покажите наставнику.")
        else:
            comment = "Верно." if camera else "Замечаний нет — совпадает с результатом камеры."
        steps.append({"component": code, "name": component["name"], "looked": looked, "answered": answered,
                      "camera": sorted(camera), "reported": sorted(reported), "missed": sorted(missed),
                      "ok": done and not missed, "comment": comment})

    total = len(steps)
    completeness = looked_n / total if total else 0.0
    attention = found_n / expected_n if expected_n else None      # nothing to find: attention cannot be judged
    value = round(100 * (completeness if attention is None else 0.5 * completeness + 0.5 * attention))
    grade = ("Осмотр выполнен хорошо" if value >= 85 else "Есть пропуски — разберите замечания" if value >= 60
             else "Осмотр нужно повторить")
    return {"score": value, "grade": grade, "completeness": round(completeness, 2),
            "attention": None if attention is None else round(attention, 2),
            "inspected": looked_n, "total": total, "found": found_n, "expected": expected_n, "steps": steps}
