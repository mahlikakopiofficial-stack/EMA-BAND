from core.digitalocean import DigitalOceanClient


def test_digitalocean_summary_reports_account_usage_and_droplet(monkeypatch):
    responses = {
        '/account': {'account': {'email': 'owner@example.com', 'status': 'active'}},
        '/customers/my/balance': {
            'balance': {
                'month_to_date_usage': '2.50',
                'month_to_date_balance': '10.00',
            }
        },
        '/droplets': {
            'droplets': [{
                'id': 123,
                'name': 'ema-bot',
                'status': 'active',
                'region': {'slug': 'nyc3'},
                'size': {'slug': 's-1vcpu-1gb'},
            }]
        },
    }

    def fake_get(url, **kwargs):
        path = url.split('/v2', 1)[1]
        return type('Response', (), {
            'raise_for_status': lambda self: None,
            'json': lambda self: responses[path],
        })()

    monkeypatch.setattr('core.digitalocean.requests.get', fake_get)
    summary = DigitalOceanClient('token', '123').summary()

    assert 'DigitalOcean: CONNECTED' in summary
    assert 'Month-to-date usage: $2.5000' in summary
    assert 'ema-bot: active | nyc3 | s-1vcpu-1gb' in summary


def test_digitalocean_summary_handles_missing_configuration():
    assert 'NOT CONFIGURED' in DigitalOceanClient('').summary()