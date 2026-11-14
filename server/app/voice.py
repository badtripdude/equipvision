"""Choosing equipment by voice, offline.

The browser records a short phrase and sends raw 16-bit mono audio; Vosk (Apache-2.0, runs on the
server CPU, no network) turns it into words. Recognition is limited to a small vocabulary — unit
types and numbers — which makes it far more reliable in a noisy shop than open dictation.
The engineer names the unit the way it is called on site: "насос сто один", "винтовой номер три".
"""
import json
import re
import threading

from .config import SERVER_DIR

MODEL_DIR = SERVER_DIR / "models" / "vosk-ru"
RATE = 16000

UNITS = {"ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
         "семь": 7, "восемь": 8, "девять": 9}
TEENS = {"десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
         "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19}
TENS = {"двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70,
        "восемьдесят": 80, "девяносто": 90}
HUNDREDS = {"сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500, "шестьсот": 600,
            "семьсот": 700, "восемьсот": 800, "девятьсот": 900}
ORDINALS = {"первый": 1, "второй": 2, "третий": 3, "четвёртый": 4, "четвертый": 4, "пятый": 5, "шестой": 6,
            "седьмой": 7, "восьмой": 8, "девятый": 9, "десятый": 10}
# Words that say what kind of unit is meant -> a fragment of the unit name in the database.
KINDS = {"центробежный": "центробежн", "винтовой": "винтов", "одновинтовой": "винтов"}
FILLERS = ["насос", "агрегат", "номер", "позиция"]
# Words the recogniser listens for; the two left out are not in the model's dictionary.
VOCABULARY = [w for w in (*FILLERS, *KINDS, *UNITS, *TEENS, *TENS, *HUNDREDS, *ORDINALS)
              if w not in ("одновинтовой", "четвертый")]
MIN_CONFIDENCE = 0.7


def spoken_number(words: list[str]) -> int | None:
    """"сто один" -> 101, "один ноль один" -> 101, "третий" -> 3. None when no number was said."""
    for word in words:
        if word in ORDINALS:
            return ORDINALS[word]
    numerals = [w for w in words if w in UNITS or w in TEENS or w in TENS or w in HUNDREDS]
    if not numerals:
        return None
    if len(numerals) > 1 and all(w in UNITS for w in numerals):      # said digit by digit
        return int("".join(str(UNITS[w]) for w in numerals))
    total = 0
    for word in numerals:
        total += UNITS.get(word, 0) + TEENS.get(word, 0) + TENS.get(word, 0) + HUNDREDS.get(word, 0)
    return total


def match_text(text: str, equipment: list[dict]) -> tuple[dict | None, list[dict]]:
    """(the unit that was named, or None; candidates to choose from when the phrase fits several)."""
    words = text.lower().replace("ё", "е").split()
    words = [w.replace("четвертый", "четвёртый") for w in words]
    number = spoken_number(words)
    kinds = [fragment for word, fragment in KINDS.items() if word in words]

    found = equipment
    if kinds:
        found = [u for u in found if any(k in u["name"].lower() for k in kinds)]
    if number is not None:
        # The number in the unit's designation: P-101 -> 101, PCP-3 -> 3.
        found = [u for u in found if [int(n) for n in re.findall(r"\d+", u["id"])][-1:] == [number]]
    if number is None and not kinds:
        return None, []
    if len(found) == 1:
        return found[0], []
    return None, found


class Recogniser:
    """Vosk model, loaded on first use: it takes a second and ~300 MB of memory."""

    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return (MODEL_DIR / "am").exists()

    def words(self, pcm: bytes, rate: int = RATE) -> list[tuple[str, float]]:
        """Recognised words with the engine's confidence in each."""
        from vosk import KaldiRecognizer, Model, SetLogLevel

        with self._lock:
            if self._model is None:
                SetLogLevel(-1)
                self._model = Model(str(MODEL_DIR))
            recogniser = KaldiRecognizer(self._model, rate, json.dumps([" ".join(VOCABULARY), "[unk]"], ensure_ascii=False))
            recogniser.SetWords(True)
            recogniser.AcceptWaveform(pcm)
            result = json.loads(recogniser.FinalResult()).get("result", [])
        return [(item["word"], float(item["conf"])) for item in result if item["word"] != "[unk]"]


def sure(words: list[tuple[str, float]]) -> bool:
    """Whether the phrase is clear enough to open the unit without asking.

    With a small vocabulary any noise is heard as some word, so a lone word is never trusted:
    the phrase must have a number and a word saying it is about a unit, each heard clearly.
    """
    said = [w for w, _ in words]
    about_unit = any(w in FILLERS or w in KINDS for w in said)
    return (about_unit and spoken_number(said) is not None
            and min((conf for _, conf in words), default=0.0) >= MIN_CONFIDENCE)
