import json
from app.dashboard.violet_solana_bridge import solana_summary
class Resp:
    def __init__(self, data): self.data=json.dumps(data).encode()
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self,n): return self.data[:n]
def test_safe_summary():
    def opener(req,timeout):
        if req.full_url.endswith('health'): return Resp({'read_only':True,'paper_only':True,'live_orders_enabled':False})
        return Resp({'paper_only':True,'verdict':'NOT PROVEN PROFITABLE','quotes':{'sample':{'evaluated_quotes':5,'mean_conservative_net_lamports':-30}},'secret':'excluded'})
    x=solana_summary(opener)
    assert x['status']=='healthy' and x['observations']==5 and 'secret' not in x
def test_fail_closed():
    x=solana_summary(lambda req,timeout: Resp({'read_only':False,'paper_only':True,'live_orders_enabled':True}))
    assert x['status']=='safety_check_failed' and not x['routes']
