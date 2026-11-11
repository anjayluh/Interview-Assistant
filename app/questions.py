import re
import time
from collections import deque

def normalize(text):
    return ' '.join(re.findall(r"[a-z0-9]+", text.lower()))

def is_question(text):
    words = normalize(text)
    if len(words.split()) < 3:
        return False
    if '?' in text:
        return True
    return bool(re.match(r'(?:(?:so|and|okay|right|now) )?(what|why|how|when|where|which|who)\b', words) or
                re.match(r'(tell me|walk me|describe|explain|compare|design|implement|write|debug|optimise|optimize|discuss|give (me |us )?an example|can you|could you|would you|have you|do you|did you)\b', words))

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
