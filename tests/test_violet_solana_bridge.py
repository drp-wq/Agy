import json
from app.dashboard.violet_solana_bridge import solana_summary


class Resp:
    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, n):
        return self.data[:n]


def test_safe_summary():
    def opener(req, timeout):
        if req.full_url.endswith('health'):
            return Resp({'read_only': True, 'paper_only': True, 'live_orders_enabled': False})
        return Resp({
            'paper_only': True,
            'verdict': 'NOT PROVEN PROFITABLE',
            'quotes': {
                'sample': {
                    'evaluated_quotes': 5,
                    'latest_age_seconds': 2.5,
                    'mean_gross_lamports': 100,
                    'mean_conservative_net_lamports': -30,
                    'median_conservative_net_lamports': -35,
                    'positive_conservative_quotes': 0,
                    'cost_assumptions': {'network_fee_lamports': 15000, 'slippage_bps_per_leg': 10.0, 'secret_key': 'drop'},
                    'warning': 'Fixed cost stress',
                }
            },
            'funding': {
                'hyperliquid_7d.csv': {
                    'gross_funding_usd': 2.1,
                    'fees_usd': 20.0,
                    'net_pnl_usd': -17.9,
                    'hours': 168,
                    'observations': 168,
                    'coverage_pct': 100.0,
                    'assumption': 'continuous_hold_no_rebalance',
                }
            },
            'secret': 'excluded',
        })

    x = solana_summary(opener)
    assert x['status'] == 'healthy' and x['observations'] == 5 and 'secret' not in x
    route = x['routes'][0]
    assert route['latest_age_seconds'] == 2.5
    assert route['mean_gross_lamports'] == 100
    assert route['median_conservative_net_lamports'] == -35
    assert route['positive_conservative_quotes'] == 0
    assert route['cost_assumptions'] == {'network_fee_lamports': 15000, 'slippage_bps_per_leg': 10.0}
    assert x['funding'][0]['dataset'] == 'hyperliquid_7d.csv'
    assert x['funding'][0]['net_pnl_usd'] == -17.9


def test_fail_closed():
    x = solana_summary(lambda req, timeout: Resp({'read_only': False, 'paper_only': True, 'live_orders_enabled': True}))
    assert x['status'] == 'safety_check_failed' and not x['routes'] and not x['funding']
