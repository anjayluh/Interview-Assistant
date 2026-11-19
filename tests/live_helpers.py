from app.cost import LiveOptions

ORIGIN = {'origin': 'http://testserver'}


def live_options(**overrides):
    return LiveOptions(**({'cap_ugx': 0, 'confirmed': True} | overrides))


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
