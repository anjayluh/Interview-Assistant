"""Offline practice prompts and transparent, heuristic feedback."""
import json
import re

from pydantic import BaseModel, Field

from .profile import InterviewSetup, terms


BANK = {
    'Recruiter screening': [
        ('Career', 'Walk me through your CV and what you want from your next role.', ['relevance', 'clarity', 'evidence']),
        ('Logistics', 'What are your salary expectations and when could you start?', ['clarity', 'communication']),
    ],
    'HR and culture fit': [
        ('Motivation', 'What kind of working environment helps you do your best work?', ['relevance', 'self-awareness']),
        ('Behavioural', 'Tell me about a disagreement with a colleague.', ['ownership', 'communication', 'outcome']),
    ],
    'Hiring manager': [
        ('Role fit', 'What would you prioritise in your first 90 days here?', ['judgement', 'relevance', 'communication']),
        ('Delivery', 'How would you handle a change in scope halfway through delivery?', ['trade-offs', 'ownership']),
    ],
    'Senior engineer': [
        ('Backend', 'How would you design an idempotent payment API?', ['technical correctness', 'trade-offs']),
        ('Database', 'How would you diagnose a slow PostgreSQL query?', ['technical correctness', 'evidence']),
    ],
    'Technical lead': [
        ('Architecture', 'How would you lead a decision between a modular monolith and microservices?', ['trade-offs', 'communication']),
        ('Mentoring', 'How do you help a junior engineer take ownership of a project?', ['ownership', 'outcome']),
    ],
    'Engineering manager': [
        ('People management', 'How would you give feedback to someone repeatedly missing commitments?', ['judgement', 'communication']),
        ('Strategy', 'How would you measure team effectiveness without rewarding busywork?', ['evidence', 'trade-offs']),
    ],
    'Behavioural and competency': [
        ('Behavioural', 'Describe a mistake you made and what changed after it.', ['ownership', 'evidence', 'outcome']),
        ('Behavioural', 'Tell me about a time you had to adapt to an unexpected change.', ['clarity', 'judgement', 'outcome']),
    ],
    'Company and business': [
        ('Company', 'What do you understand about our products, customers and business model?', ['relevance', 'evidence']),
        ('Role fit', 'Why do you want to join us and how would you contribute?', ['relevance', 'judgement']),
    ],
    'Leadership and stakeholders': [
        ('Leadership', 'How would you handle disagreement with a senior stakeholder?', ['communication', 'judgement']),
        ('Prioritisation', 'Two teams need the same engineer urgently. How would you decide?', ['trade-offs', 'judgement']),
    ],
    'Coding assessment': [
        ('Coding', 'Write a function that finds duplicate values in a list. Discuss complexity and tests.', ['technical correctness', 'edge cases', 'clarity']),
        ('Coding', 'Find the longest substring without repeated characters and explain your invariant.', ['technical correctness', 'edge cases']),
    ],
    'System design': [
        ('System design', 'Design a notification service with retries, preferences and delivery tracking.', ['technical correctness', 'trade-offs', 'reliability']),
        ('Architecture', 'How would you handle load spikes and provider outages?', ['reliability', 'observability']),
    ],
    'Debugging and practical engineering': [
        ('Debugging', 'A Django API slows down after a deployment. Walk me through your investigation.', ['technical correctness', 'evidence', 'ownership']),
        ('Incident response', 'Retries are creating duplicate payments. What would you do first?', ['judgement', 'reliability', 'communication']),
    ],
}
MIXED = 'Mixed recruitment interview'
CLOSING = 'Closing'
BANK[CLOSING] = [
    ('Closing', 'What questions do you have for us about this role or team?', ['relevance', 'judgement', 'communication']),
]



class PracticeTurn(BaseModel):
    scenario: str
    index: int = Field(default=0, ge=0, le=20)
    stage: int = Field(default=-1, ge=-1, le=12)
    phase: int = Field(default=0, ge=0, le=1)
    visited: list[int] = Field(default_factory=list, max_length=13)
    answer: str = Field(default='', max_length=5000)
    interview: InterviewSetup = Field(default_factory=InterviewSetup)


def practice(turn, context):
    if turn.scenario != MIXED and turn.scenario not in BANK:
        raise ValueError('Choose a practice scenario.')
    stages = list(BANK)
    stage = max(0, turn.stage)
    visited = set(turn.visited) | {stage}
    index = turn.index
    phase = turn.phase
    scenario = stages[stage] if turn.scenario == MIXED else turn.scenario
    questions = BANK[scenario]
    if index >= len(questions):
        raise ValueError('Start a new practice scenario.')
    category, prompt, criteria = questions[index]
    feedback = []
    answer = turn.answer.strip()
    if answer:
        words = re.findall(r"\b[\w'-]+\b", answer)
        evidence = bool(re.search(r'\d|\b(?:measured|verified|tested|result|because|observed)\b', answer, re.I))
        feedback.append(f'{len(words)} words. ' + ('Add a concrete action or example.' if len(words) < 25 else 'Check that your main point comes first.'))
        feedback.append('Evidence language detected. Check that it supports your claim.' if evidence else
                        'Add a concrete outcome, measurement or verification step where relevant.')
        feedback.append('Check the rubric yourself. Automated checks do not assess factual or technical correctness.')
        if phase == 0:
            if len(words) < 25:
                prompt = 'Can you walk me through one specific action and why you chose it?'
            elif not evidence:
                prompt = 'What evidence would show that your approach worked?'
            elif re.search(r'\b(?:team|stakeholder|manager|product)\b', answer, re.I):
                prompt = 'What would you do if the people involved disagreed with that decision?'
            else:
                prompt = 'What trade-off would change your decision, and how would you validate it?'
            phase = 1
        else:
            phase = 0
            if turn.scenario == MIXED:
                remaining = [i for i in range(1, len(stages)) if i not in visited]
                if not remaining:
                    return {'done': True, 'feedback': feedback}
                if len(remaining) > 1:
                    remaining = [i for i in remaining if stages[i] != CLOSING]
                vocabulary = terms(answer)
                stage = max(remaining, key=lambda i: len(vocabulary & terms(
                    stages[i] + ' ' + ' '.join(question for _, question, _ in BANK[stages[i]])
                )))
                visited.add(stage)
                scenario = stages[stage]
                index = 0
            else:
                index += 1
                if index == len(questions):
                    return {'done': True, 'feedback': feedback}
            category, prompt, criteria = BANK[scenario][index]
    setup = context.setup
    if phase == 0 and category in ('Company', 'Role fit'):
        target = ' at '.join(filter(None, [setup.title, setup.company]))
        if target:
            prompt += f' Use the context for {target}.'
    grounded = context.for_question(prompt)
    facts = json.loads(grounded)
    candidates = facts['selected_cv_excerpts'] + facts['achievements'] + facts['recent_profile_notes']
    hints = list(dict.fromkeys(candidates))[:3]
    return {'prompt': prompt, 'category': category, 'criteria': criteria, 'feedback': feedback,
            'hints': hints, 'index': index, 'stage': stage, 'phase': phase, 'visited': sorted(visited)}
