"""Deterministic Spanish boundaries and metric-driven, explicitly selected modes.

No model, RNG or audio processing. Chooses each next span only when synthesis
is ready to begin it, using current playback reserve. Durations are estimates,
not a guarantee of a 5-7s first response or improved RTF from bigger chunks.
"""
from dataclasses import dataclass
import re
from .segmenter import _boundary


@dataclass(frozen=True)
class Strategy:
    name: str
    first_target_chars: int
    first_min_chars: int
    following_min_chars: int
    following_target_chars: int
    maximum_chars: int = 180
    permitted_wait_seconds: float = .5


STRATEGIES = {
    "tranquilo": Strategy("tranquilo", 70, 45, 45, 90, permitted_wait_seconds=.4),
    "rapido": Strategy("rapido", 34, 28, 32, 85, permitted_wait_seconds=.8),
}


def linguistic_boundaries(text):
    """Return offsets and quality, protecting decimals, initials and abbreviations.

    Commas qualify only before selected discourse/coordinating connectors,
    never enumerations or comma decimals. Whitespace is a last-resort bound
    for an excessively long clause, not the normal unit selection rule.
    """
    endings = {}
    for i, char in enumerate(text):
        safe = _boundary(text, i)
        if char == "." and safe:
            # EE.UU., U.S.A., a.m. etc.; retain conservative abbreviation logic.
            prefix = text[:i+1]
            if re.search(r"(?:[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{1,3}\.){2,}$", prefix):
                safe = False
        quality = "sentence" if char in ".?!" else "clause"
        if char == ",":
            safe = bool(re.match(r"\s+(?:pero|sin embargo|por tanto|además|aun así|después|entonces)\b",
                                 text[i+1:], flags=re.I))
            quality = "discourse_comma"
        if not safe:
            continue
        end = i+1
        while end < len(text) and text[end] in '\"”»)]?!':
            end += 1
        if end < len(text) and not text[end].isspace():
            continue
        endings[end] = quality
    endings[len(text)] = "end"
    return sorted(endings.items())


class AdaptiveSegmenter:
    def __init__(self, text, strategy="tranquilo"):
        if strategy not in STRATEGIES:
            raise ValueError(f"Unknown strategy {strategy!r}; choose tranquilo or rapido")
        self.strategy = STRATEGIES[strategy]
        self.text = re.sub(r"\s+", " ", text).strip()
        self.boundaries = linguistic_boundaries(self.text)
        self.offset = 0
        self.index = 0
        self.synthesis_seconds_per_char = .09
        self.played_seconds_per_char = .10
        self.last_synthesis = None
        self.last_duration = None
        self.last_ratio = None
        self.last_room_total = 0.

    @property
    def finished(self):
        return self.offset >= len(self.text)

    def observe(self, text, synthesis_seconds, played_seconds, postprocessing_seconds=0):
        count = max(1, len(text))
        # Include stretch cost in availability prediction; keep synthesis RTF separate.
        measured = (synthesis_seconds+postprocessing_seconds)/count
        self.synthesis_seconds_per_char = .55*measured+.45*self.synthesis_seconds_per_char
        self.played_seconds_per_char = .55*(played_seconds/count)+.45*self.played_seconds_per_char
        self.last_synthesis = synthesis_seconds
        self.last_duration = played_seconds
        self.last_ratio = synthesis_seconds/played_seconds

    def next(self, snapshot=None):
        if self.finished:
            return None
        snapshot = dict(snapshot or {})
        ahead = max(0., float(snapshot.get("voice_ahead_seconds",0)))
        room_total = float(snapshot.get("room_only_total_seconds",0))
        recent_wait = max(0.,room_total-self.last_room_total)
        self.last_room_total = room_total
        first = self.index == 0
        mode = self.strategy
        minimum = mode.first_min_chars if first else mode.following_min_chars
        target = mode.first_target_chars if first else mode.following_target_chars
        reason = "longer_initial_reserve" if mode.name == "tranquilo" else "short_initial_unit"
        if not first:
            # Low reserve -> deliver the next natural unit sooner. Once reserve
            # returns, allow longer units for prosody/less per-call overhead.
            budget = ahead+mode.permitted_wait_seconds
            safe_chars = round(budget/max(.025,self.synthesis_seconds_per_char))
            if recent_wait > .08 or (self.last_ratio is not None and self.last_ratio > 1.05):
                target = max(minimum, min(target, 60))
                reason = "recover_after_wait_or_slow_generation"
            elif ahead > 7 and self.last_ratio is not None and self.last_ratio < .95:
                target = min(125,target+25)
                reason = "reserve_allows_longer_prosodic_unit"
            else:
                reason = "fit_next_generation_to_current_reserve"
            target = max(minimum,min(target,safe_chars,mode.maximum_chars))
        start = self.offset
        remaining = len(self.text)-start
        candidates = [(end,quality) for end,quality in self.boundaries
                      if end > start and minimum <= end-start <= mode.maximum_chars]
        if remaining <= minimum:
            candidates = [(len(self.text),"short_complete_answer_or_tail")]
        forced = False
        if not candidates:
            # Prefer a linguistic boundary even if under minimum, when the next
            # complete clause would exceed the safety bound.
            small = [(end,q) for end,q in self.boundaries if start < end <= start+mode.maximum_chars]
            if small and small[-1][0]-start >= 20:
                candidates = [small[-1]]
            else:
                upper = min(len(self.text),start+mode.maximum_chars)
                lower = start+min(minimum,remaining)
                spaces = [m.start() for m in re.finditer(r"\s+",self.text)
                          if lower <= m.start() <= upper]
                if spaces:
                    cut = min(spaces,key=lambda end:abs(end-start-target))
                    candidates = [(cut,"long_clause_word_boundary")]
                else:
                    # Never split an unbroken word/URL in half merely to meet chars.
                    word_end = self.text.find(" ",upper)
                    candidates = [(len(self.text) if word_end < 0 else word_end,"unbroken_token_exceeds_bound")]
                forced = True
        penalties = {"sentence":0,"end":0,"clause":5,"discourse_comma":12,
                     "long_clause_word_boundary":40}
        def score(candidate):
            end,quality = candidate
            length = end-start
            tiny_tail = 0 < len(self.text)-end < mode.following_min_chars
            return abs(length-target)+penalties.get(quality,0)+(50 if tiny_tail else 0)
        end,quality = min(candidates,key=score)
        segment = self.text[start:end].strip()
        self.offset = end
        while self.offset < len(self.text) and self.text[self.offset].isspace():
            self.offset += 1
        decision = {"strategy":mode.name,"index":self.index,"target_chars":target,
                    "actual_chars":len(segment),"boundary":quality,"reason":reason,
                    "forced_word_boundary":forced,"is_final":self.finished,
                    "estimated_availability_seconds":len(segment)*self.synthesis_seconds_per_char,
                    "estimated_stretched_duration_seconds":len(segment)*self.played_seconds_per_char,
                    "snapshot":dict(snapshot,last_synthesis_seconds=self.last_synthesis,
                                     last_played_seconds=self.last_duration,last_ratio=self.last_ratio,
                                     recent_room_wait_seconds=recent_wait)}
        self.index += 1
        return segment,decision
