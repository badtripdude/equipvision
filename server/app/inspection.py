"""State of the current walk-around: what the camera or the engineer found on which component.

Kept in memory per equipment until the inspection is saved to history or reset.
"""
from collections import deque
from dataclasses import dataclass, field

# Visual signs. The first three are what the vision model is expected to detect;
# the rest can only be recorded by the engineer by hand.
SIGNS = {
    "corrosion": "коррозия",
    "leak": "течь",
    "overheat": "следы перегрева",
    "loose_fastener": "ослабленный крепёж",
    "guard_missing": "нет защитного кожуха",
}
CAMERA_SIGNS = ("corrosion", "leak", "overheat")

# Components where a sign can physically appear. Corrosion can be anywhere. A sign outside its
# places (a "leak" on a terminal box) is far more often a mistake of the model than a defect, so
# the camera does not record it on its own: it is shown as unusual and left to the engineer,
# who can still record it by hand.
SIGN_PLACES = {
    "leak": {"seal", "bearing", "gearbox", "impeller", "stator", "joints", "gauge", "frame"},
    "overheat": {"motor", "terminal_box", "bearing", "gearbox", "coupling"},
}


def expected_signs(component: str) -> set[str]:
    return {sign for sign in CAMERA_SIGNS if component in SIGN_PLACES.get(sign, {component})}

WINDOW = 8              # frames remembered per component
CONFIRM = 3             # a sign must appear in this many of the last WINDOW frames
INSPECTED_FRAMES = 5    # frames needed before "nothing visible" means anything


@dataclass
class Finding:
    component: str
    sign: str
    score: float | None      # None for manual findings
    source: str              # "camera" | "manual"
    ts: str                  # telemetry time when recorded
    note: str | None = None
    photo: str | None = None # file name of the frame the sign was recorded on


class FrameTracker:
    """Confirms a sign only when it persists over several frames, to drop one-frame flicker."""

    def __init__(self) -> None:
        self.frames: deque[dict[str, float]] = deque(maxlen=WINDOW)

    def reset(self) -> None:
        self.frames.clear()

    def update(self, detections) -> dict[str, float]:
        best: dict[str, float] = {}
        for d in detections:
            # The detector already applies its own thresholds. The score is a calibrated confidence and
            # can be low for a real finding, so it must not be used to drop detections a second time.
            if d.label in CAMERA_SIGNS:
                best[d.label] = max(best.get(d.label, 0.0), d.score)
        self.frames.append(best)
        confirmed = {}
        for sign in {s for frame in self.frames for s in frame}:
            scores = [frame[sign] for frame in self.frames if sign in frame]
            if len(scores) >= CONFIRM:
                confirmed[sign] = max(scores)
        return confirmed


@dataclass
class Session:
    findings: dict[tuple[str, str], Finding] = field(default_factory=dict)
    frames: dict[str, int] = field(default_factory=dict)

    def inspected(self) -> set[str]:
        seen = {code for code, n in self.frames.items() if n >= INSPECTED_FRAMES}
        return seen | {component for component, _ in self.findings}


_sessions: dict[str, Session] = {}


def session(equipment_id: str) -> Session:
    return _sessions.setdefault(equipment_id, Session())


def reset(equipment_id: str) -> None:
    _sessions.pop(equipment_id, None)


def observe(equipment_id: str, component: str, confirmed: dict[str, float], ts: str) -> list[str]:
    """Count an analysed frame for the component and store confirmed signs. Returns newly recorded signs."""
    s = session(equipment_id)
    s.frames[component] = s.frames.get(component, 0) + 1
    new = []
    for sign, score in confirmed.items():
        key = (component, sign)
        known = s.findings.get(key)
        if known is None:
            s.findings[key] = Finding(component, sign, score, "camera", ts)
            new.append(sign)
        elif known.source == "camera" and score > (known.score or 0):
            known.score = score
    return new


def add_manual(equipment_id: str, component: str, sign: str, ts: str, note: str | None,
               photo: str | None = None) -> Finding:
    finding = Finding(component, sign, None, "manual", ts, note, photo)
    session(equipment_id).findings[(component, sign)] = finding
    return finding
