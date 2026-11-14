"""Strictly read-only Solana research summary for independent VIOLET preview."""
import json
from urllib.request import Request, urlopen

BASE = 'http://127.0.0.1:8795'

def solana_summary(opener=urlopen):
    result = {'status': 'unavailable', 'mode': 'PAPER_ONLY', 'live_orders_enabled': False,
              'verdict': 'UNKNOWN', 'observations': None, 'routes': [], 'blockers': [],
              'alerts': ['Solana data unavailable; no live execution permitted']}
    try:
        with opener(Request(BASE + '/api/health', headers={'Accept':'application/json'}), timeout=3) as response:
            health = json.loads(response.read(65536))
        if not (health.get('read_only') is True and health.get('paper_only') is True and health.get('live_orders_enabled') is False):
            result['status'] = 'safety_check_failed'
            return result
        result['status'] = 'healthy'
        with opener(Request(BASE + '/api/diagnostics', headers={'Accept':'application/json'}), timeout=4) as response:
            diagnostics = json.loads(response.read(524288))
        if diagnostics.get('paper_only') is not True:
            result['status'] = 'safety_check_failed'
            return result
        result['verdict'] = diagnostics.get('verdict') if diagnostics.get('verdict') in ('NOT PROVEN PROFITABLE','NOT PROVEN') else 'UNKNOWN'
        quotes = diagnostics.get('quotes', {})
        if isinstance(quotes, dict):
            for name, item in list(quotes.items())[:12]:
                if not isinstance(item, dict): continue
                count = item.get('evaluated_quotes')
                result['routes'].append({'dataset':str(name)[:80], 'observations':count if type(count) is int else None,
                    'stale':item.get('stale_or_empty') is True,
                    'mean_conservative_net_lamports':item.get('mean_conservative_net_lamports') if type(item.get('mean_conservative_net_lamports')) in (float,int) else None})
            result['observations'] = sum(x['observations'] or 0 for x in result['routes'])
        result['alerts'] = []
        if result['verdict'] != 'UNKNOWN': result['alerts'].append('Profitability is not proven; estimates are not realized trading P&L')
        return result
    except (OSError, ValueError, TypeError, OverflowError):
        return result
