"""Conservative Spanish sentence segmentation; no model dependencies."""
import re

ABBREVIATIONS = {"sr", "sra", "srta", "dr", "dra", "d", "dña", "ud", "uds", "pág", "págs", "núm", "aprox", "ej", "etc", "av", "tel"}

def _boundary(text, i):
    ch = text[i]
    previous = text[i - 1] if i else ""
    following = text[i + 1] if i + 1 < len(text) else ""
    if ch in ".:" and previous.isdigit() and following.isdigit():
        return False  # decimals, thousands separated by '.', and 12:30
    if ch == ".":
        if following == ".":
            return False
        word = re.search(r"([\wÁÉÍÓÚÜÑáéíóúüñ]+)$", text[:i])
        if word and (word.group(1).lower() in ABBREVIATIONS or
                     (len(word.group(1)) == 1 and word.group(1).isalpha())):
            return False
        if following and not following.isspace() and following not in '\"”»)]':
            return False  # domains and multi-dot abbreviations
    return ch in ".?!;:"

def segment_text(text, min_chars=28, max_chars=180):
    if min_chars < 1 or max_chars < min_chars:
        raise ValueError("Invalid segment length limits")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    units, start = [], 0
    for i in range(len(text)):
        if _boundary(text, i):
            end = i + 1
            while end < len(text) and text[end] in '\"”»)]?!':
                end += 1
            if end > start:
                units.append(text[start:end].strip())
                start = end
    if start < len(text):
        units.append(text[start:].strip())
    # Hard upper limit: prefer a comma/dash near the limit only for long units;
    # otherwise fall back to whitespace. Never rewrite the spoken text.
    bounded = []
    for unit in units:
        while len(unit) > max_chars:
            candidates = [m.end() for m in re.finditer(r"[,—–]", unit[:max_chars]) if m.end() >= max(min_chars, max_chars // 2)]
            cut = candidates[-1] if candidates else unit.rfind(" ", 0, max_chars + 1)
            if cut < 1:
                cut = max_chars
            bounded.append(unit[:cut].strip())
            unit = unit[cut:].strip()
        if unit:
            bounded.append(unit)
    segments, pending = [], ""
    for unit in bounded:
        joined = f"{pending} {unit}".strip()
        if pending and len(joined) > max_chars:
            segments.append(pending)
            pending = unit
        else:
            pending = joined
        if len(pending) >= min_chars:
            segments.append(pending)
            pending = ""
    if pending:
        if segments and len(segments[-1]) + len(pending) + 1 <= max_chars:
            segments[-1] += " " + pending
        else:
            segments.append(pending)
    return segments
