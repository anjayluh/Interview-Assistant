"""Local claim guard. General advice is generated; career facts use immutable evidence slots."""
import re


def evidence_bank(professional):
    values = []
    for field in ('selected_cv_excerpts', 'achievements', 'employment', 'skills', 'recent_profile_notes'):
        source = professional.get(field, [])
        if isinstance(source, str): source = [source]
        for line in source:
            if isinstance(line, str) and line.strip() and line not in values:
                values.append(line.strip())
    return {f'E{i+1}': value for i, value in enumerate(values[:20])}


def spoken_evidence(value):
    line = value.lstrip('•- ').strip()
    if re.match(r'^(?:Reduced|Contributed|Led|Owned|Developed|Delivered|Extended|Designed|Implemented|Organise|Coordinate|Co-build)\b', line):
        return 'I ' + line[0].lower() + line[1:]
    return line


def guard_answer(answer, professional, question="", discussion=None):
    bank = evidence_bank(professional)
    conversation_bank = (discussion or {}).get('candidate_statements', {})
    conversation_used = []
    used, removed, safe = [], 0, []
    # Inspect generated prose before inserting verified evidence, never treat prior
    # suggestions, live speech, company research or job requirements as career proof.
    for paragraph in re.split(r'(?<=[.!?])\s+|\n+', answer['text']):
        if not paragraph.strip(): continue
        inspection = re.sub(r'\[\[(?:E|C)\d+\]\]', '', paragraph).replace('’', "'")
        hypothetical = re.search(r"\b(?:I|we)\s+(?:would|could|might)|\b(?:I'd|we'd)|\bif\b", inspection, re.I)
        personal = re.search(r"\b(?:I|we|my|our|I've|we've|I'm|we're)\b", inspection, re.I)
        historical = re.search(r"\b(?:in my|in our|at Numida|at Emata|at Laboremus|last role|recent (?:role|project)|we used|I used|I built|I led|I implemented|I refactored|I reduced|I've used)\b", inspection, re.I)
        historical = historical or re.search(r"\b(?:I|we)(?:'ve| have| had)?\s+(?:(?:previously|once|recently|personally)\s+)?(?:used|built|led|implemented|refactored|reduced|increased|cut|deployed|handled|processed|saved|achieved|delivered)\b", inspection, re.I)
        outcome = re.search(r'\b(?:reduced|increased|improved|cut|processed|saved|grew|onboarded|deployed|refactored|achieved|delivered|took|handled|scaled)\b|\d+\s*(?:%|percent|million|billion)', inspection, re.I)
        recap = re.search(r"\b(?:as (?:discussed|mentioned|covered)|(?:I|we|you)(?:'ve| have)?\s+(?:covered|discussed|mentioned)|(?:I|we)\s+use\b.*\b(?:daily|every day))", inspection, re.I)
        # Conservative: unsupported autobiography is withheld even when plausible.
        if recap or historical or (personal and not hypothetical) or (outcome and not hypothetical):
            removed += 1
            continue
        def resolve(match):
            identifier = match[1]
            if identifier.startswith('C'):
                row = conversation_bank.get(identifier)
                if not isinstance(row,dict) or not isinstance(row.get('text'),str): return '[Conversation reference needs confirmation]'
                if identifier not in conversation_used: conversation_used.append(identifier)
                return 'Earlier, I said: “' + row['text'] + '”'
            if identifier not in bank: return '[Personal example needs your confirmation]'
            if identifier not in used: used.append(identifier)
            return spoken_evidence(bank[identifier])
        safe.append(re.sub(r'\[\[([EC]\d+)\]\]', resolve, paragraph.strip()))
    text = ' '.join(safe).strip()
    if not text:
        from .questions import conversation_move
        if conversation_move(question) == 'closing_question':
            text = 'What would success look like in the first three months, and what is the most important problem you want this person to solve?'
        elif bank:
            identifier = next(iter(bank)); used.append(identifier)
            text = spoken_evidence(bank[identifier])
        else:
            text = "I'd need to confirm a personal example before claiming that experience."
    if len(text) > 4200:
        text = "I'd use a shorter verified example; the available evidence needs to be condensed before presenting it as my experience."
        removed += 1
        used = []
    answer = dict(answer, text=text)
    answer['conversation_support'] = [dict(conversation_bank[key], id=key) for key in conversation_used]
    answer['evidence'] = [{'id': key, 'text': bank[key]} for key in used]
    answer['experience'] = 'Verified background + proposed approach' if used else 'Conversation self-report + proposed approach' if conversation_used else 'General recommendation'
    answer['grounding_warning'] = ('Unverified personal claims were removed. No additional provider request was made.' if removed else '')
    answer['grounding'] = {'removed_sentences': removed, 'evidence_ids': used, 'conversation_ids':conversation_used, 'method': 'evidence-slots-and-local-guard'}
    return answer
