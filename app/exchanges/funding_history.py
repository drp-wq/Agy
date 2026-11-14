"""Credential-free, paginated historical evidence for linear paper funding.

Only Binance currently exposes the settlement mark in the implemented history
endpoint. Other venues are explicitly blocked, never valued at a current ticker.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.core.models import MarketType
from app.core.security import assert_paper_only

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
ENDPOINT = 'https://fapi.binance.com/fapi/v1/fundingRate'


class FundingEvidenceBlocked(ValueError):
    """Missing or contradictory evidence; never a successful zero settlement."""


def milliseconds(at):
    if at.tzinfo is None or at.utcoffset() is None:
        raise FundingEvidenceBlocked('timezone_required')
    delta=at.astimezone(timezone.utc)-EPOCH
    return (delta.days*86400+delta.seconds)*1000+delta.microseconds//1000


@dataclass(frozen=True)
class FundingEvent:
    at: datetime
    previous_at: datetime
    rate: str
    mark_price: str
    source: dict
    interval_evidence: dict | None = None

    @property
    def interval_seconds(self):
        # Observed native event spacing, not a claimed scheduled interval.
        return (self.at-self.previous_at).total_seconds()


@dataclass(frozen=True)
class FundingBatch:
    events: tuple[FundingEvent, ...]
    requested_from: datetime
    checked_until: datetime
    pages: int
    rows: int


def canonical_number(value, *, positive=False):
    try:
        number=Decimal(str(value))
    except Exception as exc:
        raise FundingEvidenceBlocked('invalid_funding_number') from exc
    if not number.is_finite() or (positive and number<=0):
        raise FundingEvidenceBlocked('invalid_funding_number')
    return '0' if number==0 else format(number.normalize(),'f')


async def fetch_funding_evidence(adapter, *, symbol: str, coverage_from: datetime,
                                until: datetime, page_size=1000, max_pages=32):
    """Exhaust a bounded GET history range before any caller posts cash.

    Seven days of predecessor search is explicit and bounded. Missing predecessor
    evidence blocks an event rather than assuming an interval. No cursor hides
    late-published events: the worker re-queries from exposure coverage on retry.
    """
    assert_paper_only('paper')
    if adapter.exchange_id.value!='binance':
        raise FundingEvidenceBlocked('historical_settlement_mark_unavailable')
    if not (isinstance(page_size,int) and not isinstance(page_size,bool) and 1<=page_size<=1000
            and isinstance(max_pages,int) and not isinstance(max_pages,bool) and 1<=max_pages<=128):
        raise FundingEvidenceBlocked('invalid_pagination_bounds')
    coverage_ms=milliseconds(coverage_from)
    end=milliseconds(until)
    if coverage_from>until:
        raise FundingEvidenceBlocked('invalid_history_window')
    start=milliseconds(coverage_from-timedelta(days=7))
    markets=await adapter.load_markets()
    norm=adapter.normalize_symbol(symbol,MarketType.PERP)
    market=markets.get(norm) or {}
    if not (market.get('swap') is True and market.get('linear') is True
            and market.get('settle')=='USDT' and market.get('id') and symbol.endswith('/USDT')):
        raise FundingEvidenceBlocked('verified_linear_usdt_market_required')
    native=market['id']
    cursor=start; pages=0; records=[]
    while cursor<=end:
        if pages>=max_pages:
            raise FundingEvidenceBlocked('history_pagination_limit_incomplete')
        params={'symbol':native,'startTime':cursor,'endTime':end,'limit':page_size}
        response=await adapter._run(lambda:adapter._public_client.fapiPublicGetFundingRate(params),
                                    f'paper_funding_history:{native}')
        pages+=1
        if not isinstance(response,list) or len(response)>page_size:
            raise FundingEvidenceBlocked('invalid_history_response')
        previous_time=cursor-1
        for row in response:
            if not isinstance(row,dict) or row.get('symbol')!=native:
                raise FundingEvidenceBlocked('history_symbol_mismatch')
            timestamp=row.get('fundingTime')
            if (not isinstance(timestamp,int) or isinstance(timestamp,bool)
                    or timestamp<cursor or timestamp>end or timestamp<=previous_time):
                raise FundingEvidenceBlocked('history_timestamp_or_order_mismatch')
            previous_time=timestamp
            records.append({'symbol':native,'fundingTime':timestamp,
                'fundingRate':canonical_number(row.get('fundingRate')),
                'markPrice':canonical_number(row.get('markPrice'),positive=True)})
        if len(response)<page_size:
            break
        cursor=previous_time+1
    events=[]
    for i,row in enumerate(records):
        if row['fundingTime']<coverage_ms:
            continue
        at=EPOCH+timedelta(milliseconds=row['fundingTime'])
        if at<coverage_from:
            continue
        if i==0:
            raise FundingEvidenceBlocked('preceding_funding_event_unavailable')
        previous=records[i-1]
        # Exposure may begin between millisecond boundaries: never settle an
        # event earlier than the exact first captured snapshot.
        source={'endpoint':ENDPOINT,'venue_event_id':f"{native}:{row['fundingTime']}",
                'settlement_record':row,
                'boundary_policy':'native_fundingTime_no_rounding',
                'interval_policy':'observed_adjacent_native_events',
                'valuation_policy':'venue_funding_history_markPrice'}
        events.append(FundingEvent(at,EPOCH+timedelta(milliseconds=previous['fundingTime']),
                                   row['fundingRate'],row['markPrice'],source,
                                   {'previous_settlement_record':previous}))
    return FundingBatch(tuple(events),coverage_from,until,pages,len(records))
