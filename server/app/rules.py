"""Diagnostic rules: telemetry + visual findings + motor-hours -> diagnosis, reasons, recommendation.

`evaluate` is a pure function of `Context`, so every rule can be tested without the
server. Rules never read the customer's reference "Диагноз" column.

Levels follow the three result categories of the task statement:
    ok    — видимых проблем не обнаружено
    warn  — рекомендуется проверка
    alarm — обнаружены признаки возможного дефекта
"""
from dataclasses import dataclass, field

from .config import TAGS
from .inspection import SIGNS, Finding
from .telemetry import STATUS_ORDER, tag_status

LEVEL_TEXT = {
    "ok": "Видимых проблем не обнаружено",
    "warn": "Рекомендуется проверка",
    "alarm": "Обнаружены признаки возможного дефекта",
}
CONFIDENT_SCORE = 0.6           # camera findings at or above this count as "признаки возможного дефекта"


def ru(value: float, digits: int = 0) -> str:
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


@dataclass
class Context:
    values: dict[str, float]                        # current telemetry, tag -> value; empty: not connected
    hours: float                                    # equipment motor-hours
    components: dict[str, dict]                     # code -> component (with "name", "resource")
    events: list[dict] = field(default_factory=list)
    findings: dict[tuple[str, str], Finding] = field(default_factory=dict)
    inspected: set[str] = field(default_factory=set)
    maintenance_marks: tuple = ()                   # motor-hour marks of scheduled service for this unit
    unit_resource: dict | None = None               # {norm, used, remaining, status, source} of the whole unit

    def component_name(self, code: str) -> str:
        return self.components.get(code, {}).get("name", code)


@dataclass
class Evidence:
    text: str
    level: str | None = None                        # set by telemetry conditions
    finding: tuple[str, str] | None = None          # set by visual conditions


# ---------- conditions: check() returns evidence when the condition holds, None otherwise ----------

class Level:
    """Tag is at or above the given setpoint."""

    def __init__(self, tag: str, at_least: str = "warn"):
        self.tag, self.at_least = tag, at_least

    def check(self, ctx: Context) -> list[Evidence] | None:
        value = ctx.values.get(self.tag)
        if value is None:                       # no telemetry: the condition is unknown, so it does not hold
            return None
        status = tag_status(self.tag, value)
        if STATUS_ORDER[status] < STATUS_ORDER[self.at_least]:
            return None
        spec = TAGS[self.tag]
        kind = "тревоги" if status == "alarm" else "предупреждения"
        return [Evidence(f"{spec['title']}: {ru(value, spec['digits'])} {spec['unit']} — выше уставки "
                         f"{kind} {ru(spec[status], 1)}", level=status)]


class Normal:
    """Tag is below its warning setpoint."""

    def __init__(self, tag: str):
        self.tag = tag

    def check(self, ctx: Context) -> list[Evidence] | None:
        value = ctx.values.get(self.tag)
        if value is None or tag_status(self.tag, value) != "ok":   # unknown is not "normal"
            return None
        spec = TAGS[self.tag]
        return [Evidence(f"{spec['title']} в норме: {ru(value, spec['digits'])} {spec['unit']}")]


class Seen:
    """A visual sign was recorded on the component during this inspection."""

    def __init__(self, component: str, *signs: str):
        self.component, self.signs = component, signs

    def check(self, ctx: Context) -> list[Evidence] | None:
        found = [ctx.findings[(self.component, s)] for s in self.signs if (self.component, s) in ctx.findings]
        return [finding_evidence(ctx, f) for f in found] or None


class AnyOf:
    def __init__(self, *conditions):
        self.conditions = conditions

    def check(self, ctx: Context) -> list[Evidence] | None:
        evidence = [e for c in self.conditions for e in (c.check(ctx) or [])]
        return evidence or None


def finding_evidence(ctx: Context, f: Finding) -> Evidence:
    where = f"{SIGNS[f.sign]} на узле «{ctx.component_name(f.component)}»"
    if f.source == "manual":
        text = f"Отмечено инженером: {where}" + (f" ({f.note})" if f.note else "")
    else:
        text = f"Камера: {where}, уверенность {round((f.score or 0) * 100)}%"
    return Evidence(text, finding=(f.component, f.sign))


# ---------- rules ----------

@dataclass
class Rule:
    id: str
    title: str
    component: str | None
    when: list                                   # all must hold
    diagnosis: str
    recommendation: str | dict[str, str]         # one text, or a text per level
    level: str = "from_tags"                     # "warn" | "alarm" | "from_tags" (worst telemetry level)
    also: list = field(default_factory=list)     # optional conditions, only add evidence
    look_at: list[tuple[str, str]] = field(default_factory=list)   # (component, why) to check with the camera
    supersedes: list[str] = field(default_factory=list)            # hypotheses this rule refines
    basis: str = ""                              # where the rule comes from


CHAT = "правило заказчика"
DB_DOC = "уставки из описания данных заказчика"

RULES = [
    Rule(
        id="imbalance", title="Дисбаланс ротора", component="impeller",
        when=[Level("vib_1x")],
        diagnosis="Растёт вибрация на оборотной частоте: вероятен дисбаланс рабочего колеса "
                  "(налипание, эрозия). Такую же картину даёт ослабление крепления к фундаменту.",
        recommendation={
            "warn": "Осмотреть раму и анкерные болты, чтобы исключить ослабление крепления. "
                    "Запланировать проверку балансировки рабочего колеса.",
            "alarm": "Высокий уровень вибрации: перевести агрегат в резерв. Осмотреть раму и анкерные болты, "
                     "затем проверить рабочее колесо и балансировку.",
        },
        look_at=[("frame", "исключить ослабление крепления и коррозию рамы")],
        basis=DB_DOC,
    ),
    Rule(
        id="looseness", title="Ослабление крепления к фундаменту", component="frame",
        when=[Level("vib_1x"), Seen("frame", "corrosion", "loose_fastener")],
        diagnosis="Вибрация на оборотной частоте вызвана плохой посадкой агрегата на фундамент, "
                  "а не дисбалансом ротора.",
        recommendation="Протянуть анкерные болты моментом из документации на агрегат, устранить коррозию рамы. "
                       "После затяжки проверить соосность насоса и двигателя.",
        supersedes=["imbalance"],
        basis=CHAT,
    ),
    Rule(
        id="misalignment", title="Расцентровка валов", component="coupling",
        when=[Level("vib_2x")],
        also=[Normal("temp_bearing")],
        diagnosis="Растёт вибрация на удвоенной оборотной частоте: нарушена соосность насоса и двигателя.",
        recommendation={
            "warn": "Запланировать проверку и коррекцию соосности насоса и двигателя лазерным методом.",
            "alarm": "Выполнить проверку и коррекцию соосности насоса и двигателя лазерным методом "
                     "до продолжения длительной работы.",
        },
        look_at=[("coupling", "состояние муфты и защитного кожуха")],
        basis=CHAT,
    ),
    Rule(
        id="hf_noise", title="Высокочастотный шум", component="bearing",
        when=[Level("vib_hf"), Normal("temp_bearing")],
        diagnosis="Растёт высокочастотная вибрация при нормальной температуре подшипника: начало износа "
                  "подшипника либо кавитация. По имеющимся датчикам их не различить — нет давления на входе.",
        recommendation="Проверить условия всасывания и состояние подшипникового узла. "
                       "Контролировать температуру подшипника.",
        look_at=[("seal", "нет ли течи в зоне вала"), ("bearing", "внешнее состояние подшипникового узла")],
        basis=DB_DOC,
    ),
    Rule(
        id="bearing_wear", title="Износ подшипника или проблема смазки", component="bearing",
        when=[Level("vib_hf"), Level("temp_bearing")],
        diagnosis="Одновременно растут высокочастотная вибрация и температура подшипникового узла: "
                  "проблема со смазкой или механический износ подшипника.",
        recommendation={
            "warn": "Проверить систему смазки, при необходимости добавить или заменить смазку. "
                    "Запланировать замену подшипника.",
            "alarm": "Риск заклинивания: перевести агрегат в резерв. Проверить смазку, заменить подшипник.",
        },
        look_at=[("seal", "нет ли течи в зоне вала"), ("bearing", "внешнее состояние подшипникового узла")],
        basis=CHAT,
    ),
    Rule(
        id="bearing_overheat", title="Перегрев подшипникового узла", component="bearing",
        when=[Level("temp_bearing"), Normal("vib_hf")],
        diagnosis="Температура подшипникового узла растёт без роста высокочастотной вибрации: "
                  "вероятна нехватка или деградация смазки.",
        recommendation={
            "warn": "Проверить уровень и состояние смазки подшипникового узла.",
            "alarm": "Риск заклинивания: перевести агрегат в резерв и проверить смазку.",
        },
        look_at=[("seal", "нет ли течи в зоне вала"), ("bearing", "внешнее состояние подшипникового узла")],
        basis=DB_DOC,
    ),
    Rule(
        id="seal_degradation", title="Деградация уплотнения вала", component="seal", level="alarm",
        when=[Seen("seal", "leak"), AnyOf(Level("vib_hf"), Level("temp_bearing"))],
        diagnosis="Утечка через уплотнение вала сопровождается отклонениями подшипникового узла: "
                  "высокий риск попадания перекачиваемой жидкости в подшипник и его заклинивания.",
        recommendation="Немедленно перевести насос в резерв. Заменить уплотнение, проверить уровень "
                       "и состояние масла в подшипниковом узле.",
        supersedes=["hf_noise", "bearing_wear", "bearing_overheat"],
        basis=CHAT,
    ),
    Rule(
        id="seal_leak", title="Утечка через уплотнение вала", component="seal", level="warn",
        when=[Seen("seal", "leak"), Normal("vib_hf"), Normal("temp_bearing")],
        diagnosis="Видна утечка в зоне уплотнения вала. Подшипниковый узел по датчикам в норме.",
        recommendation="Проверить уплотнение при ближайшем останове. До этого контролировать температуру "
                       "и вибрацию подшипника.",
        basis=CHAT,
    ),
    Rule(
        id="winding_overheat", title="Перегрев обмоток двигателя", component="motor",
        when=[Level("temp_winding")],
        diagnosis="Растёт температура обмоток статора. Возможные причины: перегрузка, плохое охлаждение, "
                  "плохой контакт в силовой цепи.",
        recommendation={
            "warn": "Проверить нагрузку двигателя и охлаждение: рёбра корпуса, крыльчатку вентилятора. "
                    "Осмотреть клеммную коробку.",
            "alarm": "Риск пробоя изоляции: снизить нагрузку или остановить агрегат. "
                     "Проверить охлаждение и клеммную коробку.",
        },
        look_at=[("terminal_box", "нет ли следов перегрева и окисления контактов"),
                 ("motor", "чистота рёбер охлаждения, целостность корпуса")],
        basis=DB_DOC,
    ),
    Rule(
        id="bad_contact", title="Плохой контакт в клеммной коробке", component="terminal_box", level="alarm",
        when=[Seen("terminal_box", "overheat"), Level("temp_winding")],
        diagnosis="Следы перегрева в клеммной коробке при росте температуры обмоток: высокое переходное "
                  "сопротивление в месте контакта. Риск отгорания фазы или возгорания.",
        recommendation="Немедленно обесточить агрегат. Протянуть клеммы, проверить изоляцию жил и обмоток.",
        supersedes=["winding_overheat"],
        basis=CHAT,
    ),
    Rule(
        id="terminal_marks", title="Следы перегрева в клеммной коробке", component="terminal_box", level="alarm",
        when=[Seen("terminal_box", "overheat"), Normal("temp_winding")],
        diagnosis="В клеммной коробке видны следы перегрева или окисления. Температура обмоток сейчас в норме.",
        recommendation="Обесточить агрегат и осмотреть контактные соединения, протянуть клеммы.",
        basis=CHAT,
    ),
    Rule(
        id="guard_missing", title="Нет защитного кожуха муфты", component="coupling", level="alarm",
        when=[Seen("coupling", "guard_missing")],
        diagnosis="Нарушение промышленной безопасности: защитный кожух муфты отсутствует или смещён. "
                  "Прямой риск травмы при вращении вала.",
        recommendation="Эксплуатация запрещена до установки и закрепления кожуха.",
        basis=CHAT,
    ),
]


def _worst(levels) -> str:
    return max(levels, key=STATUS_ORDER.get, default="ok")


def _apply_rule(rule: Rule, ctx: Context) -> dict | None:
    evidence: list[Evidence] = []
    for condition in rule.when:
        found = condition.check(ctx)
        if found is None:
            return None
        evidence += found
    for condition in rule.also:
        evidence += condition.check(ctx) or []

    level = rule.level
    if level == "from_tags":
        level = _worst(e.level for e in evidence if e.level)
    recommendation = rule.recommendation if isinstance(rule.recommendation, str) else rule.recommendation[level]
    return {
        "id": rule.id,
        "level": level,
        "title": rule.title,
        "component": rule.component,
        "diagnosis": rule.diagnosis,
        "evidence": [e.text for e in evidence],
        "recommendation": recommendation,
        "basis": rule.basis,
        "_findings": {e.finding for e in evidence if e.finding},
        "_rule": rule,
    }


def _maintenance(ctx: Context) -> dict | None:
    crossed = [m for m in ctx.maintenance_marks if ctx.hours >= m]
    if not crossed:
        return None
    mark = crossed[-1]
    if any(e["kind"] == "maintenance" and (e.get("equipment_hours") or 0) >= mark for e in ctx.events):
        return None
    return {
        "id": "maintenance_due", "level": "warn", "title": "Достигнут межремонтный интервал", "component": None,
        "diagnosis": f"Наработка агрегата прошла регламентную отметку {ru(mark)} моточасов.",
        "evidence": [f"Наработка: {ru(ctx.hours)} моточасов",
                     f"Записи о ТО после отметки {ru(mark)} ч в истории нет"],
        "recommendation": "Провести ТО-2: замена масла в подшипниковых узлах, проверка центровки, "
                          "протяжка фланцевых соединений. После выполнения зарегистрировать обслуживание.",
        "basis": CHAT,
    }


def _resource(ctx: Context) -> list[dict]:
    out = []
    unit = ctx.unit_resource
    if unit and unit["status"] in ("warn", "alarm"):
        spent = unit["status"] == "alarm"
        out.append({
            "id": "resource:unit", "level": unit["status"], "component": None,
            "title": "Ресурс агрегата выработан" if spent else "Ресурс агрегата на исходе",
            "diagnosis": "Расчёт по наработке и паспортному ресурсу, не оценка фактического состояния.",
            "evidence": [
                f"Наработка {ru(unit['used'])} из {ru(unit['norm'])} ч",
                (f"Ресурс превышен на {ru(-unit['remaining'])} ч" if spent
                 else f"Расчётный остаток {ru(unit['remaining'])} ч"),
                f"Источник норматива: {unit['source']}",
            ],
            "recommendation": ("Решить вопрос о списании или продлении ресурса по результатам обследования."
                               if spent else "Запланировать обследование и решение о продлении ресурса."),
            "basis": "расчёт ресурса",
        })
    for code, component in ctx.components.items():
        res = component.get("resource") or {}
        if res.get("status") not in ("warn", "alarm"):
            continue
        spent = res["status"] == "alarm"
        out.append({
            "id": f"resource:{code}", "level": res["status"], "component": code,
            "title": "Нормативный ресурс выработан" if spent else "Ресурс узла на исходе",
            "diagnosis": f"Узел «{component['name']}»: расчёт по наработке, не оценка фактического состояния.",
            "evidence": [
                f"Наработка узла {ru(res['used_hours'])} из {ru(component['norm_hours'])} ч",
                (f"Норматив превышен на {ru(-res['remaining_hours'])} ч" if spent
                 else f"Расчётный остаток {ru(res['remaining_hours'])} ч"),
                f"Норматив: {component.get('norm_source') or 'источник не указан'}",
            ],
            "recommendation": ("Заменить узел или обосновать продление по результатам осмотра." if spent
                               else "Запланировать замену узла, заказать запасные части."),
            "basis": "расчёт ресурса",
        })
    return out


def _unexplained_findings(ctx: Context, used: set) -> list[dict]:
    """Visual signs no specific rule picked up still deserve a line of their own."""
    out = []
    for key, f in ctx.findings.items():
        if key in used:
            continue
        confident = f.source == "manual" or (f.score or 0) >= CONFIDENT_SCORE
        out.append({
            "id": f"visual:{f.component}:{f.sign}", "level": "alarm" if confident else "warn",
            "component": f.component,
            "title": f"{SIGNS[f.sign].capitalize()}: {ctx.component_name(f.component)}",
            "diagnosis": "Визуальный признак, который правила не связывают с показаниями датчиков.",
            "evidence": [finding_evidence(ctx, f).text],
            "recommendation": "Осмотреть узел вблизи и оценить степень повреждения.",
            "basis": "визуальный осмотр",
        })
    return out


def evaluate(ctx: Context) -> dict:
    fired = [d for d in (_apply_rule(rule, ctx) for rule in RULES) if d]

    # A rule confirmed by the camera replaces the sensor-only hypothesis it refines.
    by_id = {d["id"]: d for d in fired}
    superseded = set()
    for d in fired:
        for old in d["_rule"].supersedes:
            if old in by_id:
                superseded.add(old)
                d["evidence"].append(f"Уточняет гипотезу по датчикам: «{by_id[old]['title']}»")
    active = [d for d in fired if d["id"] not in superseded]

    used = set().union(*(d["_findings"] for d in active)) if active else set()
    guidance, asked = [], set()
    for d in active:
        for component, why in d["_rule"].look_at:
            if component not in ctx.inspected and component not in asked:
                asked.add(component)
                guidance.append({"component": component, "name": ctx.component_name(component),
                                 "why": why, "because": d["title"]})
    for d in active:
        del d["_findings"], d["_rule"]

    extra = _maintenance(ctx)
    diagnoses = active + _unexplained_findings(ctx, used) + _resource(ctx) + ([extra] if extra else [])
    diagnoses.sort(key=lambda d: -STATUS_ORDER[d["level"]])

    components = {}
    for code, component in ctx.components.items():
        level = _worst(d["level"] for d in diagnoses if d["component"] == code)
        inspected = code in ctx.inspected
        if level == "ok":
            text = LEVEL_TEXT["ok"] if inspected else "Не осмотрен"
        else:
            text = LEVEL_TEXT[level]
        components[code] = {"level": level, "inspected": inspected, "text": text}

    status = _worst(d["level"] for d in diagnoses)
    return {
        "status": status,
        "status_text": (LEVEL_TEXT[status] if diagnoses or ctx.inspected
                        else "Осмотр не начат, отклонений по датчикам нет" if ctx.values
                        else "Осмотр не начат"),
        "telemetry": bool(ctx.values),
        "diagnoses": diagnoses,
        "guidance": guidance,
        "components": components,
    }
