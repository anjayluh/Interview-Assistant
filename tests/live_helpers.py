from datetime import datetime, timedelta, timezone
from app.cost import LiveOptions

ORIGIN = {'origin': 'http://testserver'}


def live_options(**overrides):
    now = datetime.now(timezone.utc)
    free = {
        'assembly_credits_usd': '50', 'assembly_hourly_usd': '0.15', 'assembly_keyterms_usd': '0.04',
        'assembly_plan': 'unupgraded_trial', 'assembly_no_paid_fallback': True,
        'assembly_source': 'Synthetic trial fixture, no paid access',
        'groq_plan': 'free', 'groq_no_paid_fallback': True,
        'groq_remaining_requests': 1000, 'groq_remaining_tokens': 2000000,
        'groq_source': 'Synthetic free-plan fixture', 'checked_at': now.isoformat(),
        'valid_until': (now + timedelta(days=1)).isoformat(), 'exclusive_balances': True,
    }
    free.update(overrides.pop('free', {}))
    fields = {
        'cap_ugx': 0, 'assembly_billing_checked': True,
        'groq_billing_checked': True, 'confirmed': True, 'free': free,
    }
    return LiveOptions(**(fields | overrides))


def start_command(client, *, interview=None, options=None):
    options = options or live_options()
    interview = interview or {}
    response = client.post('/api/live-quote', headers=ORIGIN, json={
        'options': options.model_dump(mode='json'), 'keyterms': interview.get('keyterms', False),
    })
    assert response.status_code == 200
    data = response.json()
    assert data.get('quote_id'), data
    return {'cmd': 'start', 'mic': 0, 'system': 1, 'interview': interview,
            'quote_id': data['quote_id'], 'live': options.model_dump(mode='json')}
