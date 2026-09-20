"""Prescription understanding:  DOCUMENT -> text -> candidates -> (clinician) -> record.

    file -> extract_text()      pypdf text layer, else RapidOCR (PaddleOCR models, ONNX)
         -> normalize_text()
         -> parse_medications() deterministic regex/grammar, NO LLM, NO invented fields
         -> validate            confidence + needs_review flags
         -> stage               prescription_items(source='ocr', status pending|needs_review)

An OCR result is NEVER an active prescription. Only ``PatientIntelligenceAI.
confirm_medication`` (doctor) promotes a candidate, and the original text is kept.
Logs contain counts only, never prescription text.
"""

from __future__ import annotations

import difflib
import io
import re
import threading
from dataclasses import dataclass, field
from typing import Optional

UNREADABLE = "Unable to reliably read this prescription."
ALLOWED_EXT = {"pdf", "jpg", "jpeg", "png"}
MAX_BYTES = 12 * 1024 * 1024


def _plog(msg: str) -> None:
    print(f"[PRESCRIPTION_AI] {msg}")


# ------------------------------------------------------------------
# Vocabulary
# ------------------------------------------------------------------

FORMS = {
    "tab": "tablet", "tabs": "tablet", "tablet": "tablet", "tablets": "tablet", "t": "tablet",
    "cap": "capsule", "caps": "capsule", "capsule": "capsule", "capsules": "capsule",
    "syp": "syrup", "syr": "syrup", "syrup": "syrup", "susp": "suspension", "suspension": "suspension",
    "inj": "injection", "injection": "injection", "drops": "drops", "drop": "drops",
    "oint": "ointment", "ointment": "ointment", "cream": "cream", "gel": "gel", "lotion": "lotion",
    "neb": "nebulizer solution", "respules": "nebulizer solution", "respule": "nebulizer solution",
    "inhaler": "inhaler", "inh": "inhaler", "rotacap": "inhalation capsule", "sachet": "sachet",
    "powder": "powder", "spray": "spray", "supp": "suppository",
}

ROUTES = [  # ordered; first match wins. EXPLICIT text only — never inferred from the drug.
    (r"\bnebuli[sz]\w*|\bneb\b|\bnebs\b|\brespules?\b", "nebulization"),
    (r"\binhal\w*|\binhaler\b|\brotacap\b|\bpuffs?\b", "inhalation"),
    (r"\bintravenous\b|\bi\.?v\.?\b", "intravenous"),
    (r"\bintramuscular\b|\bi\.?m\.?\b", "intramuscular"),
    (r"\bsubcutaneous\b|\bs\.?c\.?\b|\bs/c\b", "subcutaneous"),
    (r"\bsublingual\b|\bs\.?l\.?\b", "sublingual"),
    (r"\beye drops?\b|\bophthalmic\b", "ophthalmic"),
    (r"\bear drops?\b|\botic\b", "otic"),
    (r"\btopical\b|\bapply\b|\blocal application\b|\bl/a\b", "topical"),
    (r"\bp\.?o\.?\b|\boral(?:ly)?\b|\bby mouth\b", "oral"),
]

FREQ_WORDS = [
    (r"\b(?:once|1 time)\s*(?:a |per )?(?:day|daily)\b|\bod\b|\bo\.d\.?\b|\bqd\b|\bevery day\b", "once daily"),
    (r"\btwice\s*(?:a |per )?(?:day|daily)\b|\b2 times\s*(?:a )?day\b|\bbd\b|\bb\.d\.?\b|\bbid\b|\bb\.i\.d\.?\b", "twice daily"),
    (r"\b(?:thrice|three times)\s*(?:a |per )?(?:day|daily)\b|\b3 times\s*(?:a )?day\b|\btds\b|\bt\.d\.s\.?\b|\btid\b|\bt\.i\.d\.?\b", "three times daily"),
    (r"\b(?:four times)\s*(?:a |per )?(?:day|daily)\b|\bqid\b|\bq\.i\.d\.?\b|\bqds\b", "four times daily"),
    (r"\bat (?:bed ?time|night)\b|\bhs\b|\bh\.s\.?\b|\bbedtime\b|\bnocte\b", "at bedtime"),
    (r"\bsos\b|\bs\.o\.s\.?\b|\bprn\b|\bp\.r\.n\.?\b|\bas needed\b|\bwhen required\b", "as needed"),
    (r"\bevery\s*(\d{1,2})\s*(?:hours?|hrs?|h)\b|\bq\s*(\d{1,2})\s*h\b", None),   # dynamic
    (r"\bweekly\b|\bonce a week\b", "once weekly"),
    (r"\bdaily\b", "once daily"),          # bare "daily" LAST so "twice daily" always wins first
]
_PATTERN_RX = re.compile(r"(?<![\d.])([01])\s*[-–]\s*([01])\s*[-–]\s*([01])(?:\s*[-–]\s*([01]))?(?![\d.])")
_PATTERN_MAP = {"100": "once daily (morning)", "010": "once daily (afternoon)", "001": "once daily (night)",
                "101": "twice daily", "110": "twice daily", "011": "twice daily", "111": "three times daily"}

STRENGTH_RX = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?|\.\d+)\s*(mg|mcg|µg|μg|ug|gm|g|ml|iu|units?|%)"
    r"(?:\s*/\s*(\d+(?:\.\d+)?)?\s*(ml|mg|g|puff|dose))?(?![a-z])", re.I)
DURATION_RX = re.compile(
    r"(?:\bx\s*|\bfor\s+|×\s*|\bcontinue\s+(?:for\s+)?)(\d{1,3})\s*(days?|d|weeks?|wks?|w|months?|mos?)\b"
    r"|\b(\d{1,3})\s*(days?|weeks?|months?)\b", re.I)
INSTR_RX = re.compile(
    r"\b(after (?:food|meals?|breakfast|lunch|dinner)|before (?:food|meals?|breakfast|lunch|dinner)|"
    r"empty stomach|with (?:water|milk|food|warm water)|a\.?c\.?|p\.?c\.?|at night|in the morning|"
    r"do not (?:crush|chew)|shake well|gargle|rinse (?:mouth|your mouth))\b", re.I)

HEADER_RX = re.compile(
    r"^(?:dr\.?|doctor|reg(?:istration)?\.?\s*no|mci|nmc|date|dt\.?|name|patient|age|sex|gender|address|phone|ph\.?|mob|"
    r"hospital|clinic|diagnosis|dx|c/o|complaints?|history|allerg|weight|wt|bp|pulse|temp|spo2|follow\s*-?up|review|"
    r"signature|sign\.?|advice|investigations?|note|instructions|opd|uhid|id\b)", re.I)

# Compact formulary for typo-correction ONLY (never to invent a medicine).
KNOWN_DRUGS = """paracetamol acetaminophen ibuprofen diclofenac aceclofenac naproxen amoxicillin amoxycillin clavulanic augmentin
azithromycin clarithromycin ciprofloxacin levofloxacin ofloxacin cefixime cefuroxime ceftriaxone cefpodoxime doxycycline
metronidazole ornidazole tinidazole omeprazole pantoprazole esomeprazole rabeprazole ranitidine famotidine domperidone
ondansetron levocetirizine cetirizine montelukast fexofenadine loratadine chlorpheniramine salbutamol levosalbutamol
ipratropium budesonide budecort formoterol fluticasone salmeterol theophylline deriphyllin prednisolone methylprednisolone
dexamethasone hydrocortisone metformin glimepiride gliclazide glipizide sitagliptin vildagliptin insulin amlodipine
telmisartan losartan atenolol metoprolol enalapril ramipril hydrochlorothiazide furosemide spironolactone atorvastatin
rosuvastatin clopidogrel aspirin ecosprin warfarin levothyroxine thyroxine folic ferrous iron calcium cholecalciferol
vitamin cyanocobalamin zinc ors ambroxol bromhexine dextromethorphan guaifenesin cough syrup benadryl ascoril
phenylephrine pseudoephedrine oxymetazoline xylometazoline atropine glycopyrrolate lactulose bisacodyl loperamide
albendazole ivermectin fluconazole clotrimazole terbinafine acyclovir oseltamivir tramadol gabapentin pregabalin
amitriptyline escitalopram sertraline alprazolam clonazepam diazepam levetiracetam phenytoin carbamazepine valproate
rifampicin isoniazid pyrazinamide ethambutol hydroxychloroquine chloroquine artemether lumefantrine""".split()

_DRUG_INDEX = sorted(set(KNOWN_DRUGS))


# ------------------------------------------------------------------
# Text extraction
# ------------------------------------------------------------------

@dataclass
class ExtractedText:
    text: str = ""
    method: str = "none"            # text_layer | ocr | plain
    confidence: float = 0.0         # 0..1 mean OCR score (1.0 for text layer / plain)
    line_confidences: list = field(default_factory=list)
    error: str = ""
    pages: int = 0


_ocr_lock = threading.Lock()
_ocr_engine = None


def _get_ocr():
    global _ocr_engine
    with _ocr_lock:
        if _ocr_engine is None:
            from rapidocr_onnxruntime import RapidOCR    # PaddleOCR detection/recognition models on ONNX
            _ocr_engine = RapidOCR()
    return _ocr_engine


def _ocr_image(img) -> tuple[list, list]:
    """Return (lines, confidences) reconstructed top-to-bottom."""
    import numpy as np
    arr = np.array(img.convert("RGB"))
    result, _ = _get_ocr()(arr)
    if not result:
        return [], []
    items = []
    for box, txt, score in result:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        items.append({"y": sum(ys) / 4, "h": max(ys) - min(ys), "x": min(xs), "t": str(txt), "s": float(score)})
    items.sort(key=lambda i: (i["y"], i["x"]))
    lines: list = []
    for it in items:                       # group fragments on the same visual line
        if lines and abs(it["y"] - lines[-1]["y"]) <= max(8, 0.6 * lines[-1]["h"]):
            lines[-1]["parts"].append(it)
            lines[-1]["y"] = (lines[-1]["y"] + it["y"]) / 2
        else:
            lines.append({"y": it["y"], "h": it["h"], "parts": [it]})
    out, conf = [], []
    for ln in lines:
        parts = sorted(ln["parts"], key=lambda p: p["x"])
        out.append(" ".join(p["t"] for p in parts))
        conf.append(sum(p["s"] for p in parts) / len(parts))
    return out, conf


def extract_text(data: bytes, filename: str = "", plain_text: Optional[str] = None) -> ExtractedText:
    if plain_text is not None:
        return ExtractedText(plain_text, "plain", 1.0, [1.0] * len(plain_text.splitlines()), pages=1)
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if ext not in ALLOWED_EXT:
        return ExtractedText(error="unsupported_type")
    if not data or len(data) > MAX_BYTES:
        return ExtractedText(error="bad_size")
    _plog("OCR started")
    try:
        if ext == "pdf":
            return _extract_pdf(data)
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.load()
        lines, conf = _ocr_image(img)
        if not lines:
            return ExtractedText(error="no_text", method="ocr", pages=1)
        return ExtractedText("\n".join(lines), "ocr", sum(conf) / len(conf), conf, pages=1)
    except ImportError:
        return ExtractedText(error="ocr_unavailable")
    except Exception as e:                       # noqa: BLE001
        return ExtractedText(error=f"extract_failed:{type(e).__name__}")


def _extract_pdf(data: bytes) -> ExtractedText:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    txt = "\n".join((p.extract_text() or "") for p in reader.pages).strip()
    if len(re.sub(r"\s+", "", txt)) >= 40:
        lines = txt.splitlines()
        return ExtractedText(txt, "text_layer", 1.0, [1.0] * len(lines), pages=len(reader.pages))
    # scanned PDF -> render each page then OCR
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(data)
    all_lines, all_conf = [], []
    for i in range(min(len(pdf), 4)):
        img = pdf[i].render(scale=2.2).to_pil()
        l, c = _ocr_image(img)
        all_lines += l
        all_conf += c
    if not all_lines:
        return ExtractedText(error="no_text", method="ocr", pages=len(reader.pages))
    return ExtractedText("\n".join(all_lines), "ocr", sum(all_conf) / len(all_conf), all_conf, pages=len(reader.pages))


# ------------------------------------------------------------------
# Normalisation + parsing
# ------------------------------------------------------------------

_OCR_FIXES = [
    (re.compile(r"(?<=\d)\s*[oO](?=\s*(?:mg|mcg|ml|g)\b)"), "0"),      # 5O mg -> 50 mg
    (re.compile(r"\bTab[.,:]?(?=[A-Z])"), "Tab. "),
    (re.compile(r"(?<=\d),(?=\d)"), "."),                              # 0,5 mg -> 0.5 mg
    (re.compile(r"([A-Za-z])(\d+(?:\.\d+)?\s*(?:mg|mcg|μg|ug|g|ml|iu|%)(?![A-Za-z]))", re.I), r"\1 \2"),   # Pantoprazole20mg
    (re.compile(r"([A-Za-z\)])(\.\d+\s*(?:mg|mcg|g|ml))", re.I), r"\1 \2"),                              # (Eliquis).5mg
    (re.compile(r"\b(take|use|apply|inhale|give)(\d)", re.I), r"\1 \2"),                                  # Take1 tablet
    (re.compile(r"\bm\s+eals\b", re.I), "meals"),                                                         # OCR split "m eals"
    (re.compile(r"\s{2,}"), " "),
]


def normalize_text(text: str) -> str:
    t = (text or "").replace(" ", " ").replace("\t", " ")
    t = t.replace("℞", "Rx ").replace("µ", "μ")
    t = t.translate(str.maketrans("（）：，", "():,"))                       # full-width punctuation from OCR
    t = re.sub(r"\(\s*(?:Cty|Oty|Qty|Oly)\s*", "(Qty ", t, flags=re.I)     # common OCR slips for "(Qty"
    for rx, rep in _OCR_FIXES:
        t = rx.sub(rep, t)
    return "\n".join(l.strip() for l in t.splitlines() if l.strip())


def _norm_strength(m: re.Match) -> str:
    num, unit = m.group(1), m.group(2).lower()
    if num.startswith("."):
        num = "0" + num                                   # ".5 mg" -> "0.5 mg"
    unit = {"gm": "g", "µg": "mcg", "μg": "mcg", "ug": "mcg", "unit": "units", "iu": "IU"}.get(unit, unit)
    s = f"{num} {unit}"
    if m.group(4):
        s += f"/{m.group(3) or ''}{m.group(4).lower()}"
    return s


def _detect_frequency(line: str) -> tuple[Optional[str], Optional[re.Match]]:
    m = _PATTERN_RX.search(line)
    if m:
        key = "".join(g for g in m.groups() if g is not None)
        if len(key) == 3 and key in _PATTERN_MAP:
            return _PATTERN_MAP[key], m
        if key == "1111":
            return "four times daily", m
    for rx, label in FREQ_WORDS:
        mm = re.search(rx, line, re.I)
        if mm:
            if label is None:
                hrs = mm.group(1) or mm.group(2)
                return f"every {int(hrs)} hours", mm
            return label, mm
    return None, None


def _detect_route(line: str, form: Optional[str]) -> Optional[str]:
    for rx, label in ROUTES:
        if re.search(rx, line, re.I):
            return label
    if form == "nebulizer solution" and re.search(r"\bneb", line, re.I):
        return "nebulization"
    return None


def _duration(line: str) -> tuple[Optional[str], Optional[re.Match]]:
    m = DURATION_RX.search(line)
    if not m:
        return None, None
    n = m.group(1) or m.group(3)
    unit = (m.group(2) or m.group(4) or "").lower()
    unit = "days" if unit.startswith("d") else "weeks" if unit.startswith("w") else "months"
    n_i = int(n)
    if n_i == 1:
        unit = unit[:-1]
    return f"{n_i} {unit}", m


def _closest_drug(word: str) -> tuple[Optional[str], float, list]:
    w = word.lower()
    if w in _DRUG_INDEX:
        return w, 1.0, []
    close = difflib.get_close_matches(w, _DRUG_INDEX, n=3, cutoff=0.8)
    if not close:
        return None, 0.0, []
    scores = [difflib.SequenceMatcher(None, w, c).ratio() for c in close]
    return close[0], scores[0], [c for c, s in zip(close, scores) if s >= scores[0] - 0.03]


_NUM_PREFIX = re.compile(r"^\s*(?:\(?\d{1,2}[.)]|[-•*»>]|rx[:.]?)\s*", re.I)
_FORM_PREFIX = re.compile(r"^(?:(tab|tabs|tablet|tablets|cap|caps|capsule|capsules|syp|syr|syrup|susp|suspension|inj|injection|"
                          r"oint|ointment|cream|gel|lotion|neb|inhaler|inh|drops?|sachet|powder|spray|supp|t)\b\.?)\s+", re.I)
_STOP_NAME = re.compile(r"\b(od|bd|bid|tds|tid|qid|hs|sos|prn|daily|twice|thrice|once|after|before|for|x|with|at|every|in|"
                        r"nebuli[sz]\w*|neb|inhal\w*|apply|orally|oral|po|iv|im|sc|sl|days?|weeks?|morning|night)\b", re.I)


SIG_RX = re.compile(r"^\s*(?:sig\s*[:.]?\s*)?(take|use|apply|inhale|instill|inject|swallow|chew|give|put)\b", re.I)
_META_RX = re.compile(r"\b(qty|quantity|refills?|fill date|discard after|rx\s*[:#]?\s*\d|pharmacy|ph\.?\s*\(?\d)", re.I)


def is_sig_line(line: str) -> bool:
    """Directions ("Take 1 tablet by mouth twice a day"), as printed on pharmacy labels."""
    return bool(SIG_RX.match(_NUM_PREFIX.sub("", line)))


def parse_sig(line: str) -> dict:
    freq, _ = _detect_frequency(line)
    dur, _ = _duration(line)
    instr = sorted({m.group(0).lower() for m in INSTR_RX.finditer(line)})
    return {"frequency": freq, "route": _detect_route(line, None), "duration": dur,
            "instructions": ", ".join(instr) if instr else None}


def looks_like_med_line(line: str) -> bool:
    if is_sig_line(line):
        return False
    if HEADER_RX.match(_NUM_PREFIX.sub("", line)):
        return False
    has_form = bool(_FORM_PREFIX.match(_NUM_PREFIX.sub("", line)))
    has_strength = bool(STRENGTH_RX.search(line))
    has_freq = _detect_frequency(line)[0] is not None
    has_drug = any(w.lower() in _DRUG_INDEX for w in re.findall(r"[A-Za-z]{4,}", line))
    first = re.match(r"[A-Za-z]{4,}", _NUM_PREFIX.sub("", line).strip())
    named_dose = bool(has_strength and first and not _META_RX.match(line.strip()))     # "Apixaban (Eliquis) .5 mg (Qty 30)"
    return (has_form and (has_strength or has_freq or has_drug)) or (has_strength and has_freq)         or (has_drug and (has_strength or has_freq)) or named_dose


def parse_line(raw: str, line_conf: float = 1.0) -> Optional[dict]:
    line = _NUM_PREFIX.sub("", raw).strip()
    if not line or not looks_like_med_line(raw):
        return None

    form = None
    fm = _FORM_PREFIX.match(line)
    rest = line
    if fm:
        form = FORMS.get(fm.group(1).lower())
        rest = line[fm.end():]

    sm = STRENGTH_RX.search(rest)
    strength = _norm_strength(sm) if sm else None
    freq, _ = _detect_frequency(rest)
    dur, _ = _duration(rest)
    route = _detect_route(rest, form)
    instr = sorted({m.group(0).lower() for m in INSTR_RX.finditer(rest)})
    if freq == "as needed" and "sos" in rest.lower():
        instr.append("as needed (SOS)")

    # generic in brackets: Brand (generic)
    generic = None
    gm = re.search(r"\(([A-Za-z][A-Za-z +\-]{3,40})\)", rest)
    if gm and not STRENGTH_RX.search(gm.group(1)):
        generic = gm.group(1).strip()
        rest_for_name = rest.replace(gm.group(0), " ")
    else:
        rest_for_name = rest

    # medicine name = leading words up to first strength / frequency / stop word
    cut = len(rest_for_name)
    for rx in (STRENGTH_RX, _PATTERN_RX, _STOP_NAME, re.compile(r"\d")):
        mm = rx.search(rest_for_name)
        if mm:
            cut = min(cut, mm.start())
    name_raw = re.sub(r"[^A-Za-z0-9 +\-/']", " ", rest_for_name[:cut]).strip(" -+/")
    name_raw = re.sub(r"\s+", " ", name_raw)
    words = name_raw.split()[:4]
    name = " ".join(words) if words else None

    result = {
        "medicine_name": name, "generic_name": generic, "strength": strength, "form": form,
        "route": route, "frequency": freq, "duration": dur,
        "instructions": ", ".join(instr) if instr else None,
        "raw_text": raw.strip()[:240], "ocr_line_confidence": round(float(line_conf), 3),
        "warnings": [],
    }

    # Typo correction against the known list — recorded, never silent
    if name:
        first = words[0]
        best, score, ties = _closest_drug(first)
        if best and best != first.lower() and score >= 0.86 and len(ties) == 1:
            result["warnings"].append(f"name_corrected:{first}->{best}")
            words[0] = best.capitalize()
            result["medicine_name"] = " ".join(words)
        elif best and len(ties) > 1 and best != first.lower():
            result["warnings"].append("ambiguous_name:" + "|".join(ties))
        elif not best and first.lower() not in _DRUG_INDEX and len(first) < 4:
            result["warnings"].append("short_unknown_name")
    return result


def score_candidate(c: dict, doc_conf: float) -> dict:
    """Confidence + verification status. Missing fields are left null and lower the score."""
    completeness = (0.4 if c.get("medicine_name") else 0) + (0.3 if c.get("strength") else 0) + (0.3 if c.get("frequency") else 0)
    ocr = min(doc_conf, c.get("ocr_line_confidence", doc_conf)) if doc_conf < 1.0 else 1.0
    conf = round(max(0.0, ocr * (0.4 + 0.6 * completeness)), 3)
    c["confidence"] = conf
    c["confidence_label"] = "high" if conf >= 0.85 else "medium" if conf >= 0.6 else "low"
    review = (
        conf < 0.6
        or not c.get("medicine_name")
        or (not c.get("strength") and not c.get("frequency"))
        or any(w.startswith(("ambiguous_name", "short_unknown_name")) for w in c["warnings"])
        or ocr < 0.7
    )
    if not c.get("strength"):
        c["warnings"].append("missing_strength")
    if not c.get("frequency"):
        c["warnings"].append("missing_frequency")
    c["verification_status"] = "needs_review" if review else "pending"
    c["source"] = "ocr"
    return c


def parse_medications(text: str, doc_confidence: float = 1.0, line_confidences: Optional[list] = None,
                      existing_names: Optional[list] = None) -> list:
    text = normalize_text(text)
    lines = text.splitlines()
    confs = line_confidences if line_confidences and len(line_confidences) == len(lines) else [doc_confidence] * len(lines)
    found: list = []
    med_idx: list = []
    for i, (ln, cf) in enumerate(zip(lines, confs)):
        c = parse_line(ln, cf)
        if c:
            c["_i"] = i
            found.append(c)
    # Pharmacy labels print directions ABOVE the drug name (Indian prescriptions usually put them on the same line).
    # Attach the nearest unclaimed directions line, previous line first, and say so.
    claimed: set = set()
    for c in found:
        if c.get("frequency"):
            continue
        for j in (c["_i"] - 1, c["_i"] + 1, c["_i"] - 2, c["_i"] + 2):
            if 0 <= j < len(lines) and j not in claimed and is_sig_line(lines[j]):
                sig = parse_sig(lines[j])
                if sig["frequency"]:
                    for k in ("frequency", "route", "duration", "instructions"):
                        if not c.get(k) and sig.get(k):
                            c[k] = sig[k]
                    c["warnings"].append("directions_from_adjacent_line")
                    claimed.add(j)
                    break
    found = [score_candidate(c, doc_confidence) for c in found]

    # duplicates inside one document: keep the more complete one, flag the other
    seen: dict = {}
    out: list = []
    for c in found:
        key = ((c.get("medicine_name") or "").lower(), (c.get("strength") or "").lower())
        if key[0] and key in seen:
            prev = seen[key]
            if sum(v is not None for v in c.values()) > sum(v is not None for v in prev.values()):
                prev.update({k: v for k, v in c.items() if v is not None})
            prev["warnings"].append("duplicate_in_document")
            continue
        seen[key] = c
        out.append(c)

    existing = {(n or "").lower() for n in (existing_names or [])}
    for c in out:
        if (c.get("medicine_name") or "").lower() in existing:
            c["warnings"].append("already_in_record")
    _plog(f"medication extracted count={len(out)}")
    return out


# ------------------------------------------------------------------
# Pipeline (service-side)
# ------------------------------------------------------------------

def process_document(svc, patient_uuid: str, data: bytes, filename: str, *, uploaded_by: Optional[str] = None,
                     facility_id: Optional[str] = None, storage_path: Optional[str] = None,
                     plain_text: Optional[str] = None) -> dict:
    """Extract + stage. Returns {document, items, status, message}. Never activates anything."""
    ext = extract_text(data, filename, plain_text)
    doc_row = {
        "patient_id": patient_uuid, "document_type": "prescription", "storage_path": storage_path,
        "filename": filename, "uploaded_by": uploaded_by, "facility_id": facility_id,
        "extracted_text": ext.text or None,
        "ocr_metadata": {"method": ext.method, "confidence": round(ext.confidence, 3), "pages": ext.pages, "error": ext.error or None},
        "ocr_status": "failed" if (ext.error or not ext.text) else "done",
    }
    doc = svc.repo.create_document(doc_row)

    if ext.error or not ext.text.strip():
        return {"document": doc, "items": [], "status": "unreadable", "message": UNREADABLE, "error": ext.error}
    if ext.method == "ocr" and ext.confidence < 0.35:
        svc.repo.update_document(doc["id"], {"ocr_status": "low_confidence"})
        return {"document": doc, "items": [], "status": "unreadable", "message": UNREADABLE, "error": "low_confidence"}

    existing = [m.get("medicine_name") for m in svc.get_medications(patient_uuid)]
    cands = parse_medications(ext.text, ext.confidence, ext.line_confidences, existing)
    staged = []
    for c in cands:
        row = {k: c.get(k) for k in ("medicine_name", "generic_name", "strength", "form", "route", "frequency",
                                     "duration", "instructions", "confidence", "verification_status", "raw_text")}
        row["warnings"] = c["warnings"]
        staged.append(svc.stage_medication(patient_uuid, row, document_id=doc["id"]))
    if not staged:
        return {"document": doc, "items": [], "status": "no_medications",
                "message": "I read the document but couldn't find any medicines in it."}
    return {"document": doc, "items": staged, "status": "staged",
            "message": f"{len(staged)} medicine(s) staged for clinician review. Nothing is active yet."}
