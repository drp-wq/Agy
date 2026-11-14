"""Strictly read-only Solana research summary for independent VIOLET preview."""
import json
from urllib.request import Request, urlopen

BASE = 'http://127.0.0.1:8795'


def _num(value):
    return value if type(value) in (int, float) else None


def _allowlisted_cost_assumptions(raw):
    if not isinstance(raw, dict):
        return None
    allowed = ('network_fee_lamports', 'priority_fee_lamports', 'jito_tip_lamports',
               'slippage_bps_per_leg', 'extra_buffer_bps')
    cleaned = {k: raw[k] for k in allowed if type(raw.get(k)) in (int, float)}
    return cleaned or None


def solana_summary(opener=urlopen):
    result = {'status': 'unavailable', 'mode': 'PAPER_ONLY', 'live_orders_enabled': False,
              'verdict': 'UNKNOWN', 'observations': None, 'routes': [], 'funding': [],
              'blockers': [], 'alerts': ['Solana data unavailable; no live execution permitted']}
    try:
        with opener(Request(BASE + '/api/health', headers={'Accept': 'application/json'}), timeout=3) as response:
            health = json.loads(response.read(65536))
        if not (health.get('read_only') is True and health.get('paper_only') is True and health.get('live_orders_enabled') is False):
            result['status'] = 'safety_check_failed'
            return result
        result['status'] = 'healthy'
        with opener(Request(BASE + '/api/diagnostics', headers={'Accept': 'application/json'}), timeout=4) as response:
            diagnostics = json.loads(response.read(524288))
        if diagnostics.get('paper_only') is not True:
            result['status'] = 'safety_check_failed'
            return result
        result['verdict'] = diagnostics.get('verdict') if diagnostics.get('verdict') in ('NOT PROVEN PROFITABLE', 'NOT PROVEN') else 'UNKNOWN'
        quotes = diagnostics.get('quotes', {})
        if isinstance(quotes, dict):
            for name, item in list(quotes.items())[:12]:
                if not isinstance(item, dict):
                    continue
                count = item.get('evaluated_quotes')
                positive = item.get('positive_conservative_quotes')
                warning = item.get('warning')
                result['routes'].append({
                    'dataset': str(name)[:80],
                    'observations': count if type(count) is int else None,
                    'stale': item.get('stale_or_empty') is True,
                    'latest_age_seconds': _num(item.get('latest_age_seconds')),
                    'mean_gross_lamports': _num(item.get('mean_gross_lamports')),
                    'mean_conservative_net_lamports': _num(item.get('mean_conservative_net_lamports')),
                    'median_conservative_net_lamports': _num(item.get('median_conservative_net_lamports')),
                    'positive_conservative_quotes': positive if type(positive) is int else None,
                    'cost_assumptions': _allowlisted_cost_assumptions(item.get('cost_assumptions')),
                    'warning': str(warning)[:240] if isinstance(warning, str) else None,
                })
            result['observations'] = sum(x['observations'] or 0 for x in result['routes'])
        funding = diagnostics.get('funding', {})
        if isinstance(funding, dict):
            for name, item in list(funding.items())[:8]:
                if not isinstance(item, dict):
                    continue
                hours = item.get('hours')
                obs = item.get('observations')
                assumption = item.get('assumption')
                warning = item.get('warning')
                result['funding'].append({
                    'dataset': str(name)[:80],
                    'gross_funding_usd': _num(item.get('gross_funding_usd')),
                    'fees_usd': _num(item.get('fees_usd')),
                    'net_pnl_usd': _num(item.get('net_pnl_usd')),
                    'hours': hours if type(hours) is int else None,
                    'observations': obs if type(obs) is int else None,
                    'coverage_pct': _num(item.get('coverage_pct')),
                    'assumption': str(assumption)[:80] if isinstance(assumption, str) else None,
                    'warning': str(warning)[:240] if isinstance(warning, str) else None,
                })
        result['alerts'] = []
        if result['verdict'] != 'UNKNOWN':
            result['alerts'].append('Profitability is not proven; estimates are not realized trading P&L')
        return result
    except (OSError, ValueError, TypeError, OverflowError):
        return result
