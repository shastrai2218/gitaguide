"""Which language register the user wrote in, so the answer can mirror it.

Tester feedback, 9 Sep 2026: *"Reply answer in hinglish."* Testers write to
GitaGuide the way they speak - Devanagari Hindi, Roman-script Hinglish, or
English - and an English-only reply to a Hinglish message reads like a form
letter.

Why this is a deterministic function and not an LLM call
-------------------------------------------------------
Three reasons, in order of weight:

1. **It must not cost a NIM request.** A turn already spends two, against a
   ~40/min free tier, and the intent call alone has been measured at 18.1 s
   under upstream 503s (kg_measurements.md section 13). A third call would add
   latency to the stage testers already call slow.
2. **It must be testable.** Register is checked by golden cases here; an LLM
   judgement could not be pinned.
3. **It is a cheap discrimination.** Script is decisive for Hindi, and
   Roman-script Indic is well separated from English by function words -
   pronouns, postpositions and copulas that English simply does not use.

It is deliberately **not** a pipeline stage: the pipeline order is fixed
(emotion -> intent -> gate -> memory -> retrieval -> generation) and adding a
stage to it needs more justification than a prompt hint does. This runs inside
prompt construction and is recorded on the generation result for measurement.

Scope, stated
-------------
Three buckets only. **Romanised Gujarati is bucketed as Hinglish**, because
that is what tester B wrote ("emne answers vague lagya") and because a reply in
Roman-script Hindi is far closer to it than an English one. A Gujarati-specific
register would need a Gujarati generation prompt and Gujarati golden cases;
that is future work, and until then the bucket is honest rather than silent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

#: Devanagari block. Presence of these letters is decisive for Hindi.
DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
#: Letters in any script, for the ratio below.
LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
#: Word tokens, lowercased by the caller.
WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

#: Fraction of letters that must be Devanagari to call a message Hindi.
#:
#: Not "any Devanagari char": an English question that quotes one Sanskrit word
#: ("what does धर्म mean?") is an English message about Hindi, and answering it
#: in Hindi would be wrong. A fifth of the letters means the person is writing
#: in the script, not quoting it.
DEVANAGARI_RATIO_FOR_HINDI = 0.20

#: Distinct Roman-script Indic markers needed to call a message Hinglish.
MARKERS_FOR_HINGLISH = 2
#: A short message gets by on one marker - "kya karu?" is two words and
#: unmistakable, and demanding two would misread it as English.
SHORT_MESSAGE_WORDS = 5

#: Roman-script Hindi/Gujarati function words and very common content words.
#:
#: Curation rules, because a false positive is worse than a false negative here
#: (answering an English speaker in Hinglish is a bigger error than the
#: reverse):
#:
#: * nothing shorter than three letters - "ka", "ke", "ki", "ko", "se", "ne",
#:   "ma", "ho" are real Hindi but collide with typos and abbreviations;
#: * nothing that is also an ordinary English word. Excluded for that reason
#:   and listed here so the next person does not "helpfully" add them back:
#:   **main** (mine/chief), **par** (golf/par), **mate**, **the**, **tha** is
#:   fine but **that** is not, **kam** (cam), **sab** is fine but **cab** is
#:   not, **din** (din = noise), **log** (logarithm), **aap** is fine.
#:   "din" and "log" are excluded despite being frequent Hindi, because
#:   "the din of the office" and "log the issue" are plausible English.
#: Grouped one semantic family per line, so a future edit can see what it is
#: adding to. A single flat literal would be unreadable and an edit to it
#: unreviewable.
MARKERS: frozenset[str] = frozenset([
    # pronouns and possessives
    "mujhe", "muje", "mujhko", "mujhse", "mera", "meri", "mere", "apna", "apne", "apni",
    "hamara", "hamari", "tera", "teri", "tumhara", "aapka", "aapki",
    "unka", "uska", "uske", "iska", "iske", "hum", "tum", "aapko", "humein", "hamein",
    "kuch", "kuchh", "sabhi", "koi", "woh", "yeh", "unko", "inko",
    # question words
    "kya", "kyu", "kyun", "kyon", "kaise", "kaisa", "kaisi", "kahan", "kaun",
    "kitna", "kitni", "kitne",
    # negation and emphasis
    "nahi", "nahin", "bilkul", "zaroor", "jarur",
    # copulas and auxiliaries
    "hai", "hain", "tha", "thi", "thay", "hoga", "hogi", "honge", "hona", "hote", "hoti",
    # "to do", in the tenses people actually write
    "karna", "karni", "karta", "karti", "karte", "karoon", "karun", "karu", "karo",
    "kiya", "kiye", "karke", "karwata", "karwati", "karwana", "kardo", "kardiya",
    "karunga", "karungi",
    # continuous aspect and quantity
    "raha", "rahi", "rahe", "rahta", "rahti", "bahut", "thoda", "thodi", "zyada", "jyada",
    # conjunctions and time
    "lekin", "magar", "aur", "phir", "abhi", "aaj", "kal", "roz", "kabhi", "jabhi",
    # modality
    "chahiye", "chahta", "chahti", "chahte", "sakta", "sakti", "sakte", "sakoon",
    # understanding and knowing
    "matlab", "samajh", "samjha", "samjhao", "samajhta", "pata", "malum", "maloom",
    # inner life - the vocabulary testers actually reach for
    "dimaag", "dimag", "mann", "dil", "jeene", "jeena", "jindagi", "zindagi",
    "khatam", "pareshan",
    # judgement
    "acha", "accha", "aacha", "theek", "thik", "bura", "kharab", "galat", "sahi",
    # everyday nouns
    "kaam", "naukri", "paisa", "ghar", "baat", "baatein", "saath", "waqt", "samay",
    "dost", "bhai", "yaar", "behen", "didi", "papa", "mummy",
    # common verbs
    "laag", "lagta", "lagti", "lagte", "lagna", "bol", "bolta", "bolna",
    "suno", "dekho", "dekhta", "dekhna", "nikal", "chhod", "chhodna", "chodna",
    "milna", "milta",
    # position
    "upar", "niche", "andar", "bahar", "wapas",
    # Romanised Gujarati (bucketed as Hinglish - see the module docstring)
    "chhe", "che", "nathi", "kem", "karvu", "krvani", "kidhi", "lagya", "emne",
    "mane", "tame", "tamne", "ame", "amne", "jaay", "thay", "hoy", "saru",
    "joie", "joiye", "kharu",
])


class Register(str, Enum):
    """The three registers GitaGuide answers in."""

    HINDI = "hindi"
    HINGLISH = "hinglish"
    ENGLISH = "english"


@dataclass(frozen=True)
class RegisterResult:
    """The verdict plus the evidence, so a wrong call can be explained."""

    register: Register
    devanagari_ratio: float
    markers: tuple[str, ...]
    words: int

    def summary(self) -> str:
        return (
            f"{self.register.value} (devanagari={self.devanagari_ratio:.2f}, "
            f"markers={len(self.markers)}, words={self.words})"
        )


def detect_register(text: str) -> RegisterResult:
    """Classify ``text`` as Hindi, Hinglish or English.

    >>> detect_register("मुझे नौकरी छोड़नी चाहिए?").register
    <Register.HINDI: 'hindi'>
    >>> detect_register("mujhe samajh nahi aa raha kya karu").register
    <Register.HINGLISH: 'hinglish'>
    >>> detect_register("My manager takes credit for my work.").register
    <Register.ENGLISH: 'english'>
    """
    letters = LETTER_RE.findall(text)
    devanagari = DEVANAGARI_RE.findall(text)
    ratio = len(devanagari) / len(letters) if letters else 0.0

    words = [word.lower() for word in WORD_RE.findall(text)]
    found = tuple(sorted({word for word in words if word in MARKERS}))

    if ratio >= DEVANAGARI_RATIO_FOR_HINDI:
        register = Register.HINDI
    elif len(found) >= MARKERS_FOR_HINGLISH or (
        found and len(words) <= SHORT_MESSAGE_WORDS
    ):
        register = Register.HINGLISH
    else:
        register = Register.ENGLISH

    return RegisterResult(
        register=register, devanagari_ratio=round(ratio, 4), markers=found, words=len(words)
    )


#: How the answer should be written, per register. Injected into the prompt.
#:
#: The Hinglish line is the one that needed care. A model told only "reply in
#: Hinglish" tends to produce transliterated Sanskrit-flavoured prose
#: ("he sakha, tvam karma-yoga ka anusaran karo"), which is not how anyone
#: writes - it reads like a translation of scripture rather than a message from
#: a person. So the instruction names the register by its actual grammar
#: (Roman-script everyday Hindi) and explicitly keeps ordinary English words in
#: place, which is what real Hinglish does.
REGISTER_INSTRUCTIONS: dict[Register, str] = {
    Register.ENGLISH: (
        "Reply in English - plain, warm, everyday English. Verse ids stay exactly "
        "as given in ALLOWED CITATION IDS."
    ),
    Register.HINGLISH: (
        "Reply in Hinglish: everyday spoken Hindi written in Roman script, keeping "
        "ordinary English words where a person would naturally use them (work, "
        "manager, team, decision, promotion). Write the way people actually text - "
        "\"aapko lagta hai ki\", not \"bhavatah cintanam\". Do NOT transliterate "
        "Sanskrit, do not use Devanagari, and do not write formal or scriptural "
        "Hindi. Verse ids stay in Latin script, exactly as given in ALLOWED "
        "CITATION IDS."
    ),
    Register.HINDI: (
        "Reply in Hindi, in Devanagari script, in plain conversational Hindi - not "
        "literary or scriptural Hindi. Keep ordinary English technical words where "
        "a Hindi speaker would use them. Verse ids stay in Latin script, exactly as "
        "given in ALLOWED CITATION IDS."
    ),
}


def register_instruction(register: Register) -> str:
    return REGISTER_INSTRUCTIONS[register]
