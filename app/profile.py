"""Local professional facts and interview context."""
from datetime import date
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_CVS = 30
MAX_FACTS = 16000


class ProfessionalProfile(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(max_length=150)
    identity: str = Field(max_length=250)
    career_start: str = Field(pattern=r'^\d{4}-\d{2}$')
    employment: str = Field(max_length=6000)
    skills: str = Field(max_length=6000)
    achievements: str = Field(max_length=6000)
    notes: str = Field(default='', max_length=4000)

    @field_validator('career_start')
    @classmethod
    def valid_month(cls, value):
        start = date.fromisoformat(value + '-01')
        if start > date.today():
            raise ValueError('Choose a career start month in the past.')
        return value


SEED = ProfessionalProfile(
    name='Angella Naigaga',
    identity='Senior Full-Stack Software Engineer / Technical Lead',
    career_start='2018-10',
    employment=(
        'Laboremus Uganda | Software Developer | October 2018 – September 2019\n'
        'Emata Uganda | Software Developer | January 2019 – April 2021\n'
        'Numida Technologies | Technical Lead | July 2021 – December 2024'
    ),
    skills=(
        'Backend: Python, Django, Django REST Framework, Flask, REST APIs, backend architecture and integrations\n'
        'Frontend: React, React Native, TypeScript, JavaScript, Vue.js, Redux\n'
        'Data: PostgreSQL, Redis, database design and performance optimisation\n'
        'Infrastructure: Docker, AWS, CI/CD, monitoring, logging and alerting\n'
        'Domains: fintech, digital lending, KYC, payments, third-party integrations\n'
        'Leadership: technical leadership, mentoring developers, architecture decisions, technical planning, working with Product teams'
    ),
    achievements=(
        'Reduced payment request failures by 40% through payment-flow redesign and stronger API error handling.\n'
        'Improved credit-team productivity by 53%.\n'
        'Contributed to 12% lending-portfolio growth.\n'
        'Contributed to a 3.5% reduction in customer acquisition cost.\n'
        'Improved new-hire time-to-productivity by 50% through onboarding playbooks, templates and CI workflows.\n'
        'Worked on a national KYC system supporting more than 50 banks.\n'
        'Contributed to financial services reaching more than 6,780 farmers and 30 cooperatives through CRM implementation.\n'
        'Developed a Twitter data-importer plugin serving more than 5,000 users.'
    ),
)


def experience(start, today=None):
    """Use elapsed career time from a month-precision date, independent of jobs."""
    today = today or date.today()
    start = date.fromisoformat(start + '-01')
    months = max(0, (today.year - start.year) * 12 + today.month - start.month)
    years, months = divmod(months, 12)
    return {'years': years, 'months': months,
            'claim': f'{years}+ years' if years else f'{months} months'}


class CVFields(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=150)
    facts: str = Field(min_length=1, max_length=MAX_FACTS)


class InterviewSetup(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    cv_id: str | None = Field(default=None, max_length=32)
    title: str = Field(default='', max_length=200)
    company: str = Field(default='', max_length=200)
    description: str = Field(default='', max_length=10000)
    notes: str = Field(default='', max_length=4000)
    keyterms: bool = True


class ProfileStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = RLock()

    def _read(self):
        if not self.path.exists():
            return {'profile': SEED.model_dump(), 'cvs': {}, 'default_cv': None}
        return json.loads(self.path.read_text(encoding='utf-8'))

    def _write(self, state):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                delete=False) as tmp:
            temp_path = Path(tmp.name)
            try:
                json.dump(state, tmp, ensure_ascii=False)
                tmp.flush()
                os.fsync(tmp.fileno())
                os.replace(temp_path, self.path)
            finally:
                temp_path.unlink(missing_ok=True)

    def summary(self):
        with self.lock:
            state = self._read()
            return {'profile': state['profile'], 'experience': experience(state['profile']['career_start']),
                    'cvs': [{'id': key, 'name': cv['name']} for key, cv in state['cvs'].items()],
                    'default_cv': state['default_cv']}

    def save_profile(self, profile):
        with self.lock:
            state = self._read()
            state['profile'] = profile.model_dump()
            self._write(state)
            return self.summary()

    @staticmethod
    def _cv(state, cv_id):
        try:
            return state['cvs'][cv_id]
        except KeyError:
            raise ValueError('That CV version no longer exists.') from None

    def get_cv(self, cv_id):
        with self.lock:
            return self._cv(self._read(), cv_id)

    def save_cv(self, fields, cv_id=None):
        with self.lock:
            state = self._read()
            if cv_id is None:
                if len(state['cvs']) >= MAX_CVS:
                    raise ValueError('Delete a CV before adding another (maximum 30).')
                cv_id = uuid4().hex
            else:
                self._cv(state, cv_id)
            state['cvs'][cv_id] = fields.model_dump()
            self._write(state)
            return {'id': cv_id, **self.summary()}

    def delete_cv(self, cv_id):
        with self.lock:
            state = self._read()
            self._cv(state, cv_id)
            del state['cvs'][cv_id]
            if state['default_cv'] == cv_id:
                state['default_cv'] = None
            self._write(state)
            return self.summary()

    def set_default(self, cv_id):
        with self.lock:
            state = self._read()
            if cv_id:
                self._cv(state, cv_id)
            state['default_cv'] = cv_id or None
            self._write(state)
            return self.summary()

    def interview(self, setup):
        with self.lock:
            state = self._read()
            cv_id = state['default_cv'] if setup.cv_id is None else setup.cv_id
            cv = self._cv(state, cv_id) if cv_id else {}
            return InterviewContext(ProfessionalProfile(**state['profile']), setup, cv.get('facts', ''))


def lines(text):
    return [line.strip(' \t•–') for line in text.splitlines() if line.strip(' \t•–')]


def compare_facts(facts, profile, previous=''):
    """Flag possible differences for human review. Omission is never a conflict."""
    master = lines('\n'.join(profile[field] for field in ('employment', 'skills', 'achievements', 'notes')))
    sources = [('Master profile', line) for line in master[:200]]
    sources.extend(('Saved CV', line) for line in lines(previous)[:100])
    normalized = [(source, line, line.casefold()) for source, line in sources]
    results = []
    for candidate in lines(facts)[:100]:
        folded = candidate.casefold()
        if any(folded == other and source == 'Master profile' for source, _, other in normalized):
            continue
        close = [
            (SequenceMatcher(None, folded[:240], other[:240]).ratio(), source, line)
            for source, line, other in normalized
            if folded != other and abs(len(other) - len(folded)) < 120
        ]
        similarity, source, match = max(close, default=(0, '', ''))
        if similarity < .58:
            if any(folded == other for _, _, other in normalized):
                continue
            results.append({'kind': 'Addition', 'candidate': candidate, 'existing': '', 'source': ''})
        else:
            before_numbers = set(re.findall(r'\d[\d,.]*%?', match))
            after_numbers = set(re.findall(r'\d[\d,.]*%?', candidate))
            kind = 'Possible contradiction' if before_numbers != after_numbers else 'Possible change'
            results.append({'kind': kind, 'candidate': candidate, 'existing': match, 'source': source})
        if len(results) == 30:
            break
    return results


def terms(text):
    return set(re.findall(r'[a-z0-9][a-z0-9+#]*(?:\.[a-z0-9]+)*', text.casefold())) - {
        'the', 'and', 'for', 'what', 'how', 'would', 'you', 'your', 'did',
        'have', 'with', 'that', 'this', 'about', 'could', 'will', 'from',
    }


def relevant(text, query, limit, *, role_terms=frozenset(), fallback=False):
    ranked = []
    for line in lines(text):
        # Contact details and URLs are unnecessary for an interview answer.
        if '@' in line or re.search(r'https?://|\+?\d[\d ()-]{8,}\d', line):
            continue
        vocabulary = terms(line)
        score = (len(query & vocabulary), len(role_terms & vocabulary))
        if any(score) or fallback:
            ranked.append((score, line[:350]))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [line for _, line in ranked[:limit]]


class InterviewContext:
    def __init__(self, profile=None, setup=None, cv_facts=''):
        self.profile = profile or SEED
        self.setup = setup or InterviewSetup()
        self.cv_facts = cv_facts

    def for_question(self, question):
        p, s = self.profile, self.setup
        query = terms(question)
        role_terms = terms(s.title + ' ' + s.description + ' ' + s.notes)
        background = bool(query & {'yourself', 'background', 'experience', 'career', 'role', 'led', 'example'})
        facts = {
            'name': p.name, 'identity': p.identity,
            'experience': experience(p.career_start)['claim'],
            'employment': relevant(p.employment, query, 5, role_terms=role_terms, fallback=True),
            'skills': relevant(p.skills, query, 5, role_terms=role_terms, fallback=True),
            'achievements': relevant(p.achievements, query, 4, role_terms=role_terms, fallback=background),
            'recent_profile_notes': relevant(p.notes, query, 3, role_terms=role_terms),
            'selected_cv_excerpts': relevant(self.cv_facts, query, 4, role_terms=role_terms),
            'target_role': s.title, 'target_company': s.company,
            'job_description_excerpt': s.description[:1400],
            'role_notes': s.notes[:500],
        }
        return json.dumps(facts, ensure_ascii=False)
