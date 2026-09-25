from __future__ import annotations

import requests


class DigitalOceanClient:
    def __init__(self, token: str, droplet_id: str = ''):
        self.token = token.strip()
        self.droplet_id = droplet_id.strip()
        self.base_url = 'https://api.digitalocean.com/v2'

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _get(self, path: str, **params):
        response = requests.get(
            f'{self.base_url}{path}',
            headers={'Authorization': f'Bearer {self.token}'},
            params=params or None,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def summary(self) -> str:
        if not self.enabled:
            return 'DigitalOcean: NOT CONFIGURED\nSet DIGITALOCEAN_API_TOKEN in .env.'
        try:
            account = self._get('/account').get('account') or {}
            balance = self._get('/customers/my/balance').get('balance') or {}
            droplets = self._get('/droplets', per_page=200).get('droplets') or []
            if self.droplet_id:
                droplets = [row for row in droplets if str(row.get('id')) == self.droplet_id]

            lines = [
                'DigitalOcean: CONNECTED',
                f"Account: {account.get('email') or '--'} ({account.get('status') or '--'})",
                f"Month-to-date usage: ${float(balance.get('month_to_date_usage') or 0):.4f}",
                f"Month-to-date balance: ${float(balance.get('month_to_date_balance') or 0):.4f}",
                f'Droplets: {len(droplets)}',
            ]
            for droplet in droplets[:10]:
                lines.append(
                    f"- {droplet.get('name') or droplet.get('id')}: "
                    f"{droplet.get('status') or '--'} | "
                    f"{(droplet.get('region') or {}).get('slug') or '--'} | "
                    f"{(droplet.get('size') or {}).get('slug') or '--'}"
                )
            if len(droplets) > 10:
                lines.append(f'... and {len(droplets) - 10} more')
            return '\n'.join(lines)
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            return f'DigitalOcean: CONNECTION FAILED\n{str(exc)[:180]}'