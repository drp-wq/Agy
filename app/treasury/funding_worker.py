"""Recover paper funding from captured exposure and read-only venue history."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.core.models import MarketType
from app.core.security import assert_paper_only
from app.exchanges.funding_history import FundingEvidenceBlocked, fetch_funding_evidence
from app.treasury import paper_exposure
from app.treasury.funding_coordinator import settle_captured_funding


class PaperFundingWorker:
    def __init__(self, db, treasury, registry, *, probe_keys=(), provider=fetch_funding_evidence,
                 clock=lambda:datetime.now(timezone.utc), poll_seconds=120,
                 publication_grace_seconds=60):
        self.db=db; self.treasury=treasury; self.registry=registry
        self.probe_keys=set(probe_keys); self.provider=provider; self.clock=clock
        self.poll_seconds=poll_seconds; self.grace=publication_grace_seconds
        self._lock=asyncio.Lock(); self._checked_at=None
        self.report={'status':'NOT_TESTED','keys':{}}

    @staticmethod
    def key(bot,exchange,symbol):
        return f'{bot}:{exchange}:{symbol}'

    def can_increase(self,bot_id,exchange,symbol):
        """Readiness is ephemeral; restart/network failure cannot leave a green gate."""
        checked=self._checked_at
        if checked is None or not 0<=(self.clock()-checked).total_seconds()<=self.poll_seconds*2:
            return False
        key=self.key(bot_id,exchange.value if hasattr(exchange,'value') else exchange,symbol)
        return self.report.get('keys',{}).get(key,{}).get('status') in {
            'VERIFIED_CAPTURED_RANGE','READY_NO_CAPTURED_EXPOSURE'}

    def legacy_count(self,bot,exchange,symbol):
        with self.db._lock:
            if not paper_exposure.exists(self.db._conn):
                return self.db._conn.execute('''SELECT COUNT(*) FROM paper_orders
                    WHERE bot_id=? AND exchange=? AND symbol=? AND market_type='perp' AND filled_qty>0''',
                    (bot,exchange,symbol)).fetchone()[0]
            return self.db._conn.execute('''SELECT COUNT(*) FROM paper_orders o
                WHERE o.bot_id=? AND o.exchange=? AND o.symbol=? AND o.market_type='perp'
                AND o.filled_qty>0 AND NOT EXISTS
                (SELECT 1 FROM paper_exposure_events e WHERE e.source_order_id=o.order_id)''',
                (bot,exchange,symbol)).fetchone()[0]

    async def run_once(self, *, force=False):
        assert_paper_only('paper')
        async with self._lock:
            now=self.clock()
            if (not force and self._checked_at is not None
                    and 0<=(now-self._checked_at).total_seconds()<self.poll_seconds):
                return self.report
            captured={(r['bot_id'],r['exchange'],r['symbol']):datetime.fromisoformat(r['coverage_from'])
                      for r in paper_exposure.keys(self.db)}
            open_keys={(p.bot_id,p.exchange.value,p.symbol) for p in self.db.list_positions()
                       if p.market_type==MarketType.PERP}
            targets=set(captured)|open_keys|self.probe_keys
            report={'status':'VERIFIED_NO_FUTURES' if not targets else 'VERIFIED_CAPTURED_RANGE',
                    'observed_at':now.isoformat(),'checked_until':(now-timedelta(seconds=self.grace)).isoformat(),
                    'scope':'captured_exposure_only_not_historical_certification','keys':{}}
            until=now-timedelta(seconds=self.grace)
            # Clear green status before I/O so an in-flight failure cannot leave
            # stale readiness available to other execution threads.
            self.report={'status':'CHECKING','keys':{}}
            for key in sorted(targets):
                bot,exchange,symbol=key
                label=self.key(*key)
                status={'status':'BLOCKED','posted':0,'duplicates':0}
                report['keys'][label]=status
                try:
                    if self.treasury is None:
                        raise FundingEvidenceBlocked('paper_treasury_disabled')
                    coverage=captured.get(key)
                    if coverage is None and key in open_keys:
                        raise FundingEvidenceBlocked('open_position_exposure_history_unavailable')
                    if coverage is not None and coverage>until:
                        raise FundingEvidenceBlocked('capture_newer_than_publication_grace')
                    batch=await self.provider(self.registry.get(exchange),symbol=symbol,
                        coverage_from=coverage or until,until=until)
                    status.update(coverage_from=coverage.isoformat() if coverage else None,
                                  pages=batch.pages,history_rows=batch.rows,
                                  checked_until=batch.checked_until.isoformat())
                    # A provider may not silently shrink the requested window.
                    if batch.requested_from!=(coverage or until) or batch.checked_until!=until:
                        raise FundingEvidenceBlocked('provider_window_incomplete')
                    if batch.rows==0:
                        raise FundingEvidenceBlocked('no_historical_funding_context')
                    if coverage is not None:
                        previous=None
                        for event in batch.events:
                            if event.at<coverage or event.at>until or (previous is not None and event.at<=previous):
                                raise FundingEvidenceBlocked('provider_event_order_or_range_invalid')
                            previous=event.at
                            result=settle_captured_funding(self.db,self.treasury,bot_id=bot,
                                exchange=exchange,symbol=symbol,settlement_at=event.at,
                                interval_start_at=event.previous_at,interval_seconds=event.interval_seconds,
                                rate=event.rate,mark_price=event.mark_price,source=event.source,now=now,
                                interval_evidence=event.interval_evidence)
                            status['duplicates' if result['duplicate'] else 'posted']+=1
                    legacy=self.legacy_count(*key)
                    status['unmapped_historical_orders']=legacy
                    status['status']=('PARTIALLY_VERIFIED' if legacy else
                        'VERIFIED_CAPTURED_RANGE' if coverage is not None else 'READY_NO_CAPTURED_EXPOSURE')
                    if legacy:
                        status['reason']='historical_funding_coverage_unresolved'
                except FundingEvidenceBlocked as exc:
                    status.update(status='BLOCKED',reason=str(exc))
                except Exception as exc:
                    # Never expose upstream request bodies, URLs or credentials.
                    status.update(status='BLOCKED',reason='funding_delivery_failed',error_type=type(exc).__name__)
                if status['status'] not in {'VERIFIED_CAPTURED_RANGE','READY_NO_CAPTURED_EXPOSURE'}:
                    report['status']='BLOCKED'
            self._checked_at=now
            self.report=report
            return report
