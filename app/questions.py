import re
import time
from collections import deque

def normalize(text):
    return ' '.join(re.findall(r"[a-z0-9]+", text.lower()))

def is_question(text):
    text = text.strip()
    words = normalize(text)
    if not words:
        return False
    if '?' in text or words in {'why', 'how so', 'any questions', 'and then', 'what next'}:
        return True
    return bool(re.search(
        r'(?:^|[.!?]\s+|,\s+)(?:(?:so|and|okay|right|now|please)\s+)*'
        r'(?:what|why|how|when|where|which|who|tell (?:me|us)|walk (?:me|us)|'
        r'describe|explain|compare|design|implement|write|debug|optimise|optimize|discuss|solve|find|'
        r'give (?:me |us )?an example|(?:can|could|would|have|do|did|will|are) you|'
        r'is there|any questions|say more|help (?:me|us) understand)\b', text, re.I
    ) or re.search(
        r"\b(?:i(?:'d| would) like to (?:hear|know|understand)|i(?:'m| am) curious|"
        r"we(?:'d| would) like to (?:hear|know|understand)|i wonder)\b", text, re.I
    ))


class QuestionGate:
    def __init__(self):
        self.turns = deque(maxlen=400)
        self.recent = deque(maxlen=50)

    def claim(self, turn_key, text, now=None):
        now = time.monotonic() if now is None else now
        norm = normalize(text)
        if turn_key in self.turns or any(n == norm and now - t < 90 for n, t in self.recent):
            return False
        self.turns.append(turn_key)
        self.recent.append((norm, now))
        return True


def interviewer_intent(text):
    """Conservative local routing; source identity comes from the capture device."""
    if re.search(r'\b(?:write|implement|debug|optimise|optimize|code|function|algorithm|coding|input|output|complexity|array|linked list|duplicates|substring)\b', text, re.I) and is_question(text):
        return 'coding'
    if is_question(text):
        return 'question'
    if re.search(r'\b(?:given|constraint|assume|must|should|without|for example|input|output|also|instead|clarif|maximum|minimum|use |return |include |make sure|n[ ≤<>=])', text, re.I):
        return 'instruction'
    return 'statement'


def likely_bleed(left, right, seconds):
    """Text/time heuristic only; retain both original source records for inspection."""
    from difflib import SequenceMatcher
    a, b = normalize(left), normalize(right)
    return (seconds <= 3 and min(len(a.split()), len(b.split())) >= 6
            and (a == b or SequenceMatcher(None, a, b).ratio() >= .91))
