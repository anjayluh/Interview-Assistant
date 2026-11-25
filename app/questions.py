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
    if re.search(r'\b(?:are there|was there|have you|did you|do you|can you|could you|would you|are you|what is your|what are your|what about your)\b', text, re.I):
        return True
    return bool(re.search(
        r'(?:^|[.!?]\s+|,\s+|\bquestion\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\b\s*[:.)\-–—]?\s+)'
        r'(?:(?:so|and|okay|right|now|please|first|next)\s+)*'
        r'(?:what|why|how|when|where|which|who|tell (?:me|us)|walk (?:me|us)|'
        r'describe|explain|compare|design|implement|write|debug|optimise|optimize|discuss|solve|find|'
        r'share|summari[sz]e|outline|introduce yourself|'
        r'give (?:me |us )?(?:(?:a |an )?(?:brief |quick |short )?(?:summary|overview|introduction))|'
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


def conversation_move(text):
    lowered = normalize(text)
    if re.search(r"\b(?:thank you.*(?:making the time|get back|stakeholders)|have a good evening|have a good day|goodbye|end (?:the|this) interview)\b", lowered): return 'goodbye'
    if re.search(r"\b(?:do you have (?:any|anything|questions)|any questions for (?:us|me)|anything (?:else|you).*ask)\b", lowered): return 'closing_question'
    if re.search(r"(?:you get what i mean|do you (?:follow|understand)|you get|does that make sense)\s*$", lowered): return 'check_in'
    if re.search(r"\b(?:not asking|not talking about|i mean|i meant|my question is|let me rephrase|i was referring)\b", lowered): return 'clarification'
    return ''


def interviewer_intent(text):
    move = conversation_move(text)
    if move in ('goodbye', 'check_in'): return move
    if move == 'closing_question': return 'question'
    if re.search(r'\b(?:write|implement|debug|optimise|optimize|code|function|algorithm|coding|input|output|complexity|array|linked list|duplicates|substring)\b', text, re.I) and is_question(text): return 'coding'
    if is_question(text): return 'question'
    if move == 'clarification': return 'clarification'
    if re.match(r'(?:(?:and|also|please|okay|so|now)\s+)*(?:given|constraint|assume|must|should|without|for example|input|output|instead|maximum|minimum|use|return|include|preserve|make sure|n[ ≤<>=])\b', text, re.I): return 'instruction'
    return 'statement'


def likely_bleed(left, right, seconds):
    from difflib import SequenceMatcher
    a, b = normalize(left), normalize(right)
    if seconds > 15 or min(len(a.split()), len(b.split())) < 6: return False
    return a in b or b in a or SequenceMatcher(None, a, b).ratio() >= .91
