"""Conversation evidence is attributed speech, never independently verified CV history."""
import re
from .profile import terms

TOPICS = re.compile(r'\b(factory|strategy|facade|façade|saga|decorator|observer|singleton|adapter|repository)\b', re.I)


def conversation_state(turns, question=''):
    bank, reports, introduced = {}, {}, set()
    candidate = []
    for turn in turns:
        if not turn.get('final') or turn.get('duplicate_of'): continue
        text=turn.get('text','').replace('’', "'")
        topics={match[1].lower().replace('ç','c').title() for match in TOPICS.finditer(text)}
        if turn.get('role')=='interviewer':
            introduced.update(topics)
            continue
        if turn.get('role')!='me': continue
        for sentence in re.split(r'(?<=[.!?])\s+',text):
            if len(sentence)>600: continue
            assertion=re.search(r"\bI(?:'ve| have| haven't| have not| don't| do not|'m| am)?\s+(?:(?:only|mostly|mainly|not|never)\s+)?(?:used|use|worked|know|familiar|sure|understand)\b",sentence,re.I)
            if not assertion or re.search(r'\b(?:if|would|could|might)\b',sentence,re.I): continue
            # Keep the candidate's actual sentence, excluding any interviewer framing.
            quote=sentence[assertion.start():].strip()
            candidate.append({'turn_key':turn['key'],'text':quote,'kind':'candidate_self_report_not_verified_cv'})
            for match in TOPICS.finditer(quote):
                topic=match[1].lower().replace('ç','c').title()
                prefix=quote[:match.start()]
                negatives=list(re.finditer(r"haven't|have not|don't know|do not know|never|\bnot\b",prefix,re.I))
                positives=list(re.finditer(r"\bI(?:'ve| have|'m| am)?\s+(?:(?:only|mostly|mainly)\s+)?(?:used|use|know|familiar)\b",prefix,re.I))
                negative=bool(negatives and (not positives or negatives[-1].start()>positives[-1].start()))
                reports[topic]={'topic':topic,'status':'uncertain_or_no_experience' if negative else 'self_reported_familiarity',
                    'turn_key':turn['key'],'quote':quote}
    # Preserve explicit candidate positions even when they fall outside recent chat.
    query=terms(question)
    relevant=[row for row in candidate if TOPICS.search(row['text']) or query & terms(row['text'])]
    selected=[]
    for row in (relevant[-4:]+candidate[-2:]):
        if row not in selected: selected.append(row)
    for index,row in enumerate(selected,1): bank['C'+str(index)]=row
    return {'candidate_statements':bank,'candidate_topic_reports':list(reports.values())[-12:],
        'interviewer_introduced_topics':sorted(introduced),
        'rule':'Interviewer mentions and generated suggestions never establish candidate familiarity. Candidate speech is self-report, not verified career evidence.'}
