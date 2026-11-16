import re
import time
from collections import deque

def normalize(text):
    return ' '.join(re.findall(r"[a-z0-9]+", text.lower()))

def is_question(text):
    words = normalize(text)
    if not words:
        return False
    if '?' in text or words in {'why', 'how so', 'any questions', 'and then', 'what next'}:
        return True
    return bool(re.search(
        r'(?:^|[.!?]\s+|,\s+)(?:(?:so|and|okay|right|now|please)\s+)*'
        r'(?:what|why|how|when|where|which|who|tell (?:me|us)|walk (?:me|us)|'
        r'describe|explain|compare|design|implement|write|debug|optimise|optimize|discuss|'
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
