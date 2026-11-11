from app.cost import LiveOptions, PRICING_DATE

ORIGIN = {'origin': 'http://testserver'}


def live_options(**overrides):
    fields = {
        'cap_ugx': 10000, 'fx_ugx_per_usd': '4000',
        'fx_date': PRICING_DATE.isoformat(), 'fx_source': 'Synthetic test rate',
        'assembly_billing_checked': True, 'groq_billing_checked': True,
        'confirmed': True,
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
