"""Local full-session review data; never provider credentials, raw prompts or audio by default."""
import json
import copy
import math
import re
import time
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone
from .provider_errors import save_diagnostic

LIMIT = 20000


class Review:
    def __init__(self, metadata=None, saved=None):
        self.data = saved or {'schema_version': 2, 'id': uuid4().hex,
            'created_at': datetime.now(timezone.utc).isoformat(), 'metadata': metadata or {},
            'history': [], 'answers': [], 'partials': [], 'metrics': [], 'decisions': [],
            'failures': [], 'requests': [], 'receipts': [], 'states': [], 'connections': [], 'outcomes': [], 'limits_hit': []}
        self.data.setdefault('id', uuid4().hex)
        self.data.setdefault('schema_version', 1)
        for key in ('history','answers','partials','metrics','decisions','failures','requests','receipts','states','connections','outcomes','limits_hit'):
            self.data.setdefault(key, [])
        self.data.setdefault('metadata', {'coverage': 'legacy retained tail; earlier data unavailable'})
        self.data.setdefault('created_at', datetime.now(timezone.utc).isoformat())
        self.turns = {row['key']: row for row in self.data['history'] + self.data['partials']}
        self.version = 0
        self.receipt_keys = set()
        self.emitted = {}
        self.text_bytes = sum(len(row.get("text", "").encode("utf-8")) for row in self.turns.values())

    def append(self, key, item):
        target = self.data[key]
        if len(target) >= LIMIT:
            if key not in self.data['limits_hit']: self.data['limits_hit'].append(key)
            return
        target.append(item)
        self.version += 1

    def capture(self, event):
        kind = event['type']
        if kind == 'transcript':
            if 'key' not in event: return
            key = event['key']
            if key not in self.turns and len(self.turns) >= LIMIT:
                if 'transcript' not in self.data['limits_hit']: self.data['limits_hit'].append('transcript')
                return
            original = self.turns.get(key, {})
            delta_bytes = len(event.get('text','').encode('utf-8')) - len(original.get('text','').encode('utf-8'))
            if self.text_bytes + delta_bytes > 20_000_000:
                if 'transcript_bytes' not in self.data['limits_hit']: self.data['limits_hit'].append('transcript_bytes')
                return
            self.text_bytes += delta_bytes
            self.version += 1
            self.turns[key] = {**event, 'first_seen_at': original.get('first_seen_at', event.get('first_seen_at', event.get('received_at')))}
        elif kind == 'latency': self.append('metrics', dict(event))
        elif kind == 'answer':
            self.append('answers', dict(event))
            self.emitted[event['revision']] = time.monotonic()
        elif kind == 'connection': self.append('connections',dict(event))
        elif kind == 'answer_superseded': self.append('outcomes',dict(event))
        elif kind == 'routing': self.append('decisions', dict(event))
        elif kind == 'state':
            # State changes only: avoid duplicating transient level/status snapshots.
            entry = {key:event.get(key) for key in ('state','reason','duration','limit_minutes','sent_at')}
            if not self.data['states'] or self.data['states'][-1]['state'] != entry['state']:
                self.append('states', entry)
        elif kind == 'error':
            self.append('failures', {key:event[key] for key in ('message','diagnostic','provider','revision','sent_at') if key in event})

    def receipt(self, revision, device, render_ms, replay=False):
        if not isinstance(revision, int) or revision not in self.emitted: return
        if not isinstance(render_ms, (int,float)) or isinstance(render_ms,bool) or not math.isfinite(render_ms) or not 0 <= render_ms <= 60000: return
        key = (revision, device, bool(replay))
        if key in self.receipt_keys: return
        self.receipt_keys.add(key)
        self.append('receipts', {'revision':revision,'device':device,'render_ms':round(render_ms,2),
            'server_emit_to_receipt_ms':round((time.monotonic()-self.emitted[revision])*1000,2),
            'replay':bool(replay), 'meaning':'receipt includes round trip and client render; not one-way latency'})

    def snapshot(self):
        return copy.deepcopy({**self.data, 'history':[row for row in self.turns.values() if row.get('final')],
                'partials':[row for row in self.turns.values() if not row.get('final')]})


def valid_id(value):
    return isinstance(value,str) and re.fullmatch(r'[a-f0-9]{32}',value) is not None


def summary(record):
    stages = {}
    for item in record.get('metrics',[]):
        stages.setdefault(item['revision'],{})[item['stage']] = item['at_ms']
    turns = {row['key']:row for row in record.get('history',[])}
    timings, answered = [], set()
    for answer in record.get('answers',[]):
        revision=answer.get('revision'); answered.add(revision)
        item=stages.get(revision,{})
        if 'question_finalised' not in item or 'answer_complete' not in item: continue
        keys=answer.get('turn_keys',[])
        source=[turns[key] for key in keys if key in turns]
        timing={'revision':revision,'turn_keys':keys,
            'recognised_to_complete_ms':round(item['answer_complete']-item['question_finalised'],2),
            'generation_ms':round(item['answer_complete']-item.get('answer_generation_start',item['question_finalised']),2)}
        if source:
            timing['first_fragment_to_final_ms']=round((max(row['received_at'] for row in source)-min(row.get('first_seen_at',row['received_at']) for row in source))*1000,2)
        timings.append(timing)
    superseded = {row.get('superseded_revision') for row in record.get('outcomes',[])}
    failed = {row.get('revision') for row in record.get('failures',[])}
    missing=[{**row, 'outcome':'superseded' if row.get('target_revision') in superseded else 'failed' if row.get('target_revision') in failed else 'no_completed_suggestion_recorded'} for row in record.get('decisions',[]) if row.get('intent') in ('question','coding') and row.get('target_revision') not in answered]
    return {'timings':timings,'turns':len(record.get('history',[])),'answers':len(record.get('answers',[])),
        'questions_without_completed_suggestion':missing,
        'grounding_removed_sentences':sum(x.get('grounding',{}).get('removed_sentences',0) for x in record.get('answers',[])),
        'receipts':record.get('receipts',[]),
        'delivery_coverage':[{'revision':answer.get('revision'), 'devices':{
            device: ('new-answer-receipt' if any(r.get('revision')==answer.get('revision') and r.get('device')==device and not r.get('replay') for r in record.get('receipts',[])) else
            'replay-only' if any(r.get('revision')==answer.get('revision') and r.get('device')==device for r in record.get('receipts',[])) else 'unmeasured') for device in ('desktop','phone')}} for answer in record.get('answers',[])],
        'connections':record.get('connections',[]),
        'coverage':record.get('metadata',{}).get('coverage','legacy retained tail; earlier data unavailable'),
        'limits_hit':record.get('limits_hit',[]),
        'unmeasured':['exact human speech-end to display','physical-device paint beyond browser callback'] + ([] if record.get('metadata',{}).get('audio_recorded') else ['spoken vocal delivery; local audio was off'])}
