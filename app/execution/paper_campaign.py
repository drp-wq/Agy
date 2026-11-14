"""Durable 72-hour PAPER_ONLY entry window with continued protective exits."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path


class PaperCampaign:
    key = 'violet_paper_campaign'

    def __init__(self, db, root, hours=None, clock=None, campaign_id=None, initial_budget_usd=None, sleeve_capital_usd=None):
        self.db, self.root = db, Path(root)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.record = db.load_risk_state(self.key)
        if self.record is None and hours is not None:
            if not 0 < hours <= 72:
                raise ValueError('paper_campaign_duration_must_be_at_most_72_hours')
            start = self.clock()
            self.record = {
                'campaign_id': campaign_id or self.root.name,
                'mode': 'PAPER_ONLY',
                'started_at': start.isoformat(),
                'ends_at': (start + timedelta(hours=hours)).isoformat(),
                'duration_hours': hours,
                'initial_budget_usd': initial_budget_usd,
                'sleeve_capital_usd': sleeve_capital_usd,
                'baseline_fills': len(db.list_fills(limit=100000)),
                'baseline_orders': len(db.list_orders(limit=100000)),
                'cycles_observed': 0,
            }
            self.db.save_risk_state(self.key, self.record)
        elif self.record is not None:
            changed = False
            if campaign_id and not self.record.get('campaign_id'):
                self.record['campaign_id'] = campaign_id
                changed = True
            if initial_budget_usd is not None and self.record.get('initial_budget_usd') is None:
                self.record['initial_budget_usd'] = initial_budget_usd
                changed = True
            if sleeve_capital_usd is not None and self.record.get('sleeve_capital_usd') is None:
                self.record['sleeve_capital_usd'] = sleeve_capital_usd
                changed = True
            if changed:
                self.db.save_risk_state(self.key, self.record)

    def entries_allowed(self):
        if self.record is None:
            return True
        now = self.clock()
        return datetime.fromisoformat(self.record['started_at']) <= now < datetime.fromisoformat(self.record['ends_at'])

    def status(self):
        if self.record is None:
            return {'status':'unbounded_test_or_legacy_run'}
        now = self.clock()
        start = datetime.fromisoformat(self.record['started_at'])
        end = datetime.fromisoformat(self.record['ends_at'])
        phase = 'active' if self.entries_allowed() else ('waiting_for_start' if now < end else
                'protective_exits_only' if self.db.list_positions() else 'completed')
        remaining_sec = max(0.0, (end - now).total_seconds())
        elapsed_sec = max(0.0, (now - start).total_seconds())
        return dict(
            self.record,
            status=phase,
            entries_allowed=self.entries_allowed(),
            remaining_seconds=remaining_sec,
            remaining_hours=round(remaining_sec / 3600.0, 2),
            elapsed_hours=round(elapsed_sec / 3600.0, 2),
        )

    def observe(self, state):
        if self.record is None:
            return
        self.record['cycles_observed'] += 1
        self.record['last_observed_at'] = self.clock().isoformat()
        fills = self.db.list_fills(limit=100000)
        self.record['public_paper_fills_during_run'] = max(0,len(fills)-self.record['baseline_fills'])
        self.record['paper_orders_during_run'] = max(0,len(self.db.list_orders(limit=100000))-self.record['baseline_orders'])
        self.record['open_positions'] = len(self.db.list_positions())
        self.record['accounts'] = self.db.list_accounts()
        self.db.save_risk_state(self.key, self.record)
        summary = self.status()
        snapshot = {'observed_at':self.record['last_observed_at'], 'cycle':state.get('cycles'),
            'status':summary['status'], 'discovered':state.get('discovered',0), 'scanned':state.get('scanned',0),
            'fills':self.record['public_paper_fills_during_run'], 'positions':self.record['open_positions'],
            'rejections':state.get('rejections',[]), 'worker_health':state.get('worker_health')}
        with (self.root/'72h-observations.jsonl').open('a') as stream:
            stream.write(json.dumps(snapshot,allow_nan=False)+'\n')
        tmp = self.root/'72H_RUN_REPORT.tmp'
        tmp.write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
        tmp.replace(self.root/'72H_RUN_REPORT.json')
