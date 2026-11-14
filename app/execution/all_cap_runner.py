"""Independent Violet Lab all-cap spot paper engine. No credential loading or remote writes."""
from __future__ import annotations

import asyncio
import math
import fcntl
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path

from app.accounting.pnl_tracker import build_pnl_report, classify_research_cap_category
from app.core.config import AllCapConfig, RiskPolicyConfig
from app.core.models import Direction, ExchangeId, MarketType, OrderType
from app.core.security import assert_paper_only
from app.exchanges.timestamps import source_timestamp_ms
from app.execution.paper_engine import PaperExecutionEngine, EXCHANGE_FEE_SCHEDULE
from app.execution.paper_campaign import PaperCampaign
from app.execution.violet_campaign_config import read_historical_archive_summary
from app.persistence.database import PaperDatabase
from app.risk.guardian import GlobalRiskGuardian
from app.scanner.market_cap import MarketCapBand, MarketCapProvider, classify_market_cap_band
from app.scanner.market_scanner import GlobalMarketScanner
from app.scanner.mid_low_cap_discovery import DynamicSpotMarketDiscoverer, evaluate_safety_filters
from app.strategies.mid_low_cap_selector import MidLowCapStrategySelector, compute_net_reward_risk
from app.strategies.orchestrator import AIStrategyOrchestrator

BANDS = tuple(b for b in MarketCapBand if b != MarketCapBand.UNKNOWN)
# Smaller assets receive lower risk and tighter execution gates, never leverage.
POLICIES = {  # volume, depth, spread bps, risk fraction
    MarketCapBand.MICRO: (150_000, 5_000, 35, .0005),
    MarketCapBand.LOW: (250_000, 10_000, 30, .001),
    MarketCapBand.MID: (750_000, 20_000, 25, .0015),
    MarketCapBand.LARGE: (2_000_000, 40_000, 15, .002),
    MarketCapBand.MEGA: (10_000_000, 75_000, 12, .0025),
}
BAND_RISK_WEIGHTS = {
    MarketCapBand.MICRO: 0.4,
    MarketCapBand.LOW: 0.5,
    MarketCapBand.MID: 0.6,
    MarketCapBand.LARGE: 0.8,
    MarketCapBand.MEGA: 1.0,
}


def source_age(timestamp, now, limit):
    timestamp = source_timestamp_ms(timestamp)
    return timestamp is not None and -5 <= now.timestamp() - timestamp / 1000 <= limit


def _quantize_to_step(value: float, step: float, round_nearest: bool = False) -> float:
    if not (math.isfinite(value) and math.isfinite(step) and step > 0):
        return value
    d_val = Decimal(str(value))
    d_step = Decimal(str(step))
    rounding = ROUND_HALF_UP if round_nearest else ROUND_DOWN
    steps = (d_val / d_step).to_integral_value(rounding=rounding)
    return float(steps * d_step)


class VioletMarketCapProvider(MarketCapProvider):
    """All-cap identity gate: never select a ticker winner by market-cap dominance."""
    def classify_symbol(self, base_symbol, now=None):
        record = super().classify_symbol(base_symbol, now)
        rows = self._cache.get(base_symbol.strip().upper(), [])
        if len({row.get('id') for row in rows}) > 1:
            return record.model_copy(update={'band': MarketCapBand.UNKNOWN, 'is_fresh': False,
                                             'ambiguous': True, 'reason': 'ambiguous_ticker_collision'})
        return record


class CheckedPublicAdapter:
    """Only data methods; source timestamps are mandatory, receipt times cannot substitute."""
    def __init__(self, adapter, max_age):
        self.adapter, self.max_age = adapter, max_age
        self.exchange_id = adapter.exchange_id
        self.last_error = None

    async def fetch_ticker(self, symbol, market_type):
        ticker = await self.adapter.fetch_ticker(symbol, market_type)
        ticker = dict(ticker, timestamp=source_timestamp_ms(ticker.get('timestamp')))
        if not source_age(ticker.get('timestamp'), datetime.now(timezone.utc), self.max_age):
            self.last_error = 'missing_or_stale_ticker_source_timestamp'
            raise ValueError(self.last_error)
        return ticker

    async def fetch_order_book(self, *args, **kwargs):
        book = await self.adapter.fetch_order_book(*args, **kwargs)
        if not source_age(book.source_timestamp_ms, datetime.now(timezone.utc), self.max_age):
            self.last_error = 'missing_or_stale_book_source_timestamp'
            raise ValueError(self.last_error)
        return book

    async def fetch_ohlcv(self, *args, **kwargs):
        return await self.adapter.fetch_ohlcv(*args, **kwargs)


class AllCapPaperRunner:
    def __init__(
        self,
        root: Path,
        registry,
        config: AllCapConfig | None = None,
        cap_provider=None,
        campaign_hours=None,
        campaign_id: str | None = None,
        historical_archive_dir: Path | None = None,
    ):
        assert_paper_only('paper')
        self.config = config or AllCapConfig()
        # Caller supplies an independent Lab directory. Refuse known frozen/preview ledgers.
        self.root = Path(root).resolve()
        if str(self.root).startswith('/root/multi-exchange-paper-audit-fixes'):
            raise ValueError('production_path_forbidden')
        self.historical_archive_dir = Path(historical_archive_dir).resolve() if historical_archive_dir else None
        if self.historical_archive_dir is not None and self.root == self.historical_archive_dir:
            raise ValueError('active_campaign_dir_cannot_equal_historical_archive_dir')
        self.campaign_id = campaign_id or self.root.name
        if any(getattr(a, 'credentials', None) for a in registry.all().values()):
            raise ValueError('exchange_credentials_forbidden')
        self.registry = registry
        self.root.mkdir(parents=True, exist_ok=True)
        self._worker_lock = (self.root / 'worker.lock').open('a')
        try:
            fcntl.flock(self._worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._worker_lock.close()
            raise RuntimeError('all_cap_worker_already_active') from None
        self.db = PaperDatabase(self.root / 'violet-all-cap.sqlite3')
        self.fee_schedule = dict(EXCHANGE_FEE_SCHEDULE)
        for key, value in (self.db.load_risk_state('violet_instrument_fees') or {}).items():
            exchange, market_type, symbol = key.split('|', 2)
            self.fee_schedule[(ExchangeId(exchange), MarketType(market_type), symbol)] = value
        # Spot-only 0x borrowed leverage enforced at guardian level (max_leverage=1.0).
        self.engine = PaperExecutionEngine(
            self.db,
            GlobalRiskGuardian(RiskPolicyConfig(max_leverage=1.0), db=self.db),
            self.fee_schedule,
            marketable_limit_taker=True,
        )
        self.recovery = self.engine.recover_on_startup()
        self.provider = cap_provider or VioletMarketCapProvider(self.config, cache_path=self.root/'market-cap-cache.json')
        self.discovery = DynamicSpotMarketDiscoverer(self.provider, self.config)
        self.discovery.target_bands = BANDS
        self.scanner = GlobalMarketScanner(registry)
        self.selector = MidLowCapStrategySelector(AIStrategyOrchestrator(), self.config, self.fee_schedule)
        self._lock = asyncio.Lock()
        self.state = self.db.load_risk_state('violet_all_cap_heartbeat') or {'cycles': 0}
        if self.config.enabled:
            for band in BANDS:
                self.engine.ensure_bot_account(self.bot_id(band), ExchangeId.BINANCE, self.config.sleeve_capital_usd)
            self._verify_sleeve_accounts()
        self.campaign = PaperCampaign(
            self.db,
            self.root,
            campaign_hours,
            campaign_id=self.campaign_id,
            initial_budget_usd=round(len(BANDS) * float(self.config.sleeve_capital_usd), 2),
            sleeve_capital_usd=round(float(self.config.sleeve_capital_usd), 2),
        )
        self._populate_summary_fields()

    def _verify_sleeve_accounts(self):
        accounts = [self.db.get_account(self.bot_id(band)) for band in BANDS]
        if len(self.db.list_accounts()) != len(BANDS) or any(a is None for a in accounts):
            raise ValueError('unexpected_sleeve_account_count')
        for band, acct in zip(BANDS, accounts):
            if abs(float(acct['initial_equity_usd']) - float(self.config.sleeve_capital_usd)) > 1e-6:
                raise ValueError('sleeve_initial_equity_mismatch')
            bot_id = acct['bot_id']
            self.db.append_ledger_event(
                event_id=f"alloc:{bot_id}",
                timestamp_utc=str(acct.get("updated_at") or datetime.now(timezone.utc).isoformat()),
                event_type="CAPITAL_ALLOCATION",
                bot_id=bot_id,
                exchange="binance",
                market_cap_band=band.value,
                amount_usd=str(self.config.sleeve_capital_usd),
                details={"sleeve": band.value, "mode": "PAPER_ONLY"},
                idempotency_key=f"alloc:{bot_id}",
            )

    @staticmethod
    def bot_id(band):
        return f'violet_all_cap_{band.value}_spot_paper'

    def policy(self, band):
        volume, depth, spread, risk = POLICIES[band]
        if self.config.max_risk_per_trade_pct > 0.0025:
            band_risk = round(self.config.max_risk_per_trade_pct * BAND_RISK_WEIGHTS[band], 6)
        else:
            band_risk = min(risk, self.config.max_risk_per_trade_pct)
        return self.config.model_copy(update={
            'min_24h_volume_usd': max(volume, self.config.min_24h_volume_usd),
            'min_order_book_depth_usd': max(depth, self.config.min_order_book_depth_usd),
            'max_spread_bps': min(spread, self.config.max_spread_bps),
            'max_risk_per_trade_pct': band_risk,
        })

    def safety_reasons(self, snap, cap, now=None):
        now = now or datetime.now(timezone.utc)
        band = classify_market_cap_band(cap.market_cap_usd, self.config, is_fresh=cap.is_fresh, ambiguous=cap.ambiguous)
        if band not in BANDS:
            return ['unverified_cap_band']
        # Reuse legacy execution gates without changing legacy cap eligibility semantics.
        legacy_cap = cap.model_copy(update={'band': MarketCapBand.MID})
        reasons = evaluate_safety_filters(snap, legacy_cap, self.policy(band), self.db.list_positions(), now).reasons
        for label, timestamp in [('ticker', snap.ticker_source_timestamp_ms), ('book', snap.book_source_timestamp_ms)]:
            if not source_age(timestamp, now, self.config.max_market_data_age_sec):
                reasons.append(f'missing_or_stale_{label}_source_timestamp')
        if not math.isfinite(snap.liquidity_score) or snap.liquidity_score < self.config.min_liquidity_score:
            reasons.append('insufficient_liquidity_score')
        if (cap.circulating_supply is None or not math.isfinite(cap.circulating_supply)
                or cap.circulating_supply <= 0 or not cap.source_id):
            reasons.append('unverified_circulating_supply_or_asset_id')
        levels = list(snap.bids or []) + list(snap.asks or [])
        if not snap.bids or not snap.asks or any(not math.isfinite(p) or not math.isfinite(q)
                                                or p <= 0 or q <= 0 for p,q in levels):
            reasons.append('invalid_or_missing_order_book_levels')
        values = [snap.last_price, snap.bid, snap.ask, snap.spread_bps, snap.order_book_depth_usd,
                  snap.volume_24h_usd, snap.atr, cap.market_cap_usd]
        if not all(math.isfinite(v) for v in values):
            reasons.append('nonfinite_market_data')
        if cap.source_updated_at is None or not -5 <= (now-cap.source_updated_at).total_seconds() <= self.config.market_cap_max_age_sec:
            reasons.append('missing_or_stale_cap_source_timestamp')
        for tf, interval in [('15m', 900), ('1h', 3600)]:
            candles = snap.candles_by_tf.get(tf, [])
            if not candles or not source_age(candles[-1].timestamp, now, interval + self.config.max_market_data_age_sec):
                reasons.append(f'missing_or_stale_candles:{tf}')
            elif any(b.timestamp <= a.timestamp or b.timestamp-a.timestamp != interval*1000 for a,b in zip(candles, candles[1:])):
                reasons.append(f'noncontiguous_candles:{tf}')
            if any(not all(math.isfinite(v) and v > 0 for v in (c.open,c.high,c.low,c.close))
                   or c.high < max(c.open,c.close) or c.low > min(c.open,c.close)
                   or not math.isfinite(c.volume) or c.volume < 0 for c in candles):
                reasons.append(f'invalid_candles:{tf}')
        return reasons

    def size(self, signal, snap, band):
        policy = self.policy(band)
        if (signal.estimated_fee_pct < .001 or signal.estimated_slippage_pct < 0
                or not all(math.isfinite(v) for v in (signal.estimated_fee_pct, signal.estimated_slippage_pct))):
            return 0.0
        # Size at the maximum allowed fill price; local LIMIT simulation cannot sweep above it.
        entry = max(snap.ask * (1 + signal.estimated_slippage_pct), signal.entry_high)
        stop = signal.stop_loss
        target = signal.take_profit_targets[0] if signal.take_profit_targets else 0
        if (signal.direction != Direction.LONG or signal.market_type != MarketType.SPOT
                or not all(math.isfinite(v) for v in (entry, stop, target))
                or not 0 < stop < entry < target
                or not all(math.isfinite(v) for v in (signal.entry_low, signal.entry_high))
                or not signal.entry_low <= entry <= signal.entry_high
                or compute_net_reward_risk(entry, stop, target, signal.estimated_fee_pct,
                                          signal.estimated_slippage_pct) < policy.min_risk_reward_ratio):
            return 0.0
        account = self.db.get_account(self.bot_id(band))
        positions = self.db.list_positions(self.bot_id(band))
        equity = float(account['cash_usd']) + sum(p.unrealized_pnl_usd for p in positions)
        free = max(0, float(account['cash_usd']) - float(account.get('reserved_margin_usd') or 0))
        risk_fraction = (entry-stop)/entry + 2*(signal.estimated_fee_pct + signal.estimated_slippage_pct)
        value = min(equity * policy.max_risk_per_trade_pct / risk_fraction,
                    free * .95, equity * .2, snap.order_book_depth_usd * min(.01, policy.max_notional_to_depth_ratio))
        # Guardian converts requested notional to quantity at last_price. Convert the
        # worst-fill notional back to that basis, leaving room for quantity rounding.
        requested = max(0, value) * snap.last_price / entry
        return max(0, math.floor(requested*100)/100 - .01)

    def safe_for_protective_exit(self, snap):
        """Exit existing risk using fresh prices, independent of entry eligibility."""
        now = datetime.now(timezone.utc)
        values = [snap.last_price, snap.bid, snap.ask]
        levels = list(snap.bids or []) + list(snap.asks or [])
        return (all(math.isfinite(v) and v > 0 for v in values)
                and snap.bid <= snap.ask and bool(snap.bids) and bool(snap.asks)
                and all(math.isfinite(p) and math.isfinite(q) and p > 0 and q > 0 for p,q in levels)
                and 0 <= (now-snap.timestamp).total_seconds() <= self.config.max_market_data_age_sec
                and all(source_age(ts, now, self.config.max_market_data_age_sec)
                        for ts in (snap.ticker_source_timestamp_ms, snap.book_source_timestamp_ms)))

    async def instrument_reasons(self, adapter, signal, snap, notional):
        """Fail closed when public instrument rules exceed the simulator's model."""
        try:
            spec = await self.public_spec(adapter, signal.symbol)
        except Exception:
            return ['instrument_spec_unavailable']
        values = [spec.price_precision, spec.amount_precision, spec.min_amount]
        if (not all(math.isfinite(v) and v > 0 for v in values)
                or not all(math.isfinite(v) and v >= 0 for v in (spec.min_cost,spec.maker_fee,spec.taker_fee))):
            return ['invalid_instrument_spec']
        schedule = self.fee_schedule.get((signal.exchange, MarketType.SPOT, signal.symbol)) or self.fee_schedule.get((signal.exchange, MarketType.SPOT), {})
        if (spec.taker_fee > schedule.get('taker', 0) or spec.maker_fee > schedule.get('maker', 0)
                or signal.estimated_fee_pct < spec.taker_fee):
            return ['instrument_fees_exceed_validated_simulation_model']
        qty = round(notional / snap.last_price, 6)
        if qty < spec.min_amount or qty * signal.entry_high < spec.min_cost:
            return ['instrument_minimum_amount_or_notional']
        def aligned(value, tick):
            steps = value / tick
            return abs(steps-round(steps)) < 1e-6
        if not aligned(qty, spec.amount_precision) or not aligned(signal.entry_high, spec.price_precision):
            return ['instrument_precision_not_supported_by_simulated_order']
        return []

    @staticmethod
    async def public_spec(adapter, symbol):
        if hasattr(adapter, 'get_validated_spot_spec'):
            return await adapter.get_validated_spot_spec(symbol)
        return await adapter.get_instrument_spec(symbol, MarketType.SPOT)

    async def prepare_fees(self, adapter, symbol):
        spec = await self.public_spec(adapter, symbol)
        if not all(math.isfinite(f) and 0 <= f <= .05 for f in (spec.maker_fee,spec.taker_fee)):
            raise ValueError('invalid_instrument_fees')
        key = (adapter.exchange_id, MarketType.SPOT, symbol)
        previous = self.fee_schedule.get(key) or self.fee_schedule.get(key[:2]) or {'maker':.001,'taker':.001}
        # Conservative rates never decrease during a position's lifetime/run.
        self.fee_schedule[key] = {'maker':max(previous['maker'],spec.maker_fee),
                                  'taker':max(previous['taker'],spec.taker_fee,.001)}
        self.db.save_risk_state('violet_instrument_fees', {
            '|'.join((k[0].value,k[1].value,k[2])):v for k,v in self.fee_schedule.items() if len(k)==3})

    def sleeve_daily_state(self, band):
        bot = self.bot_id(band)
        account = self.db.get_account(bot)
        equity = float(account['cash_usd']) + sum(p.unrealized_pnl_usd for p in self.db.list_positions(bot))
        day = datetime.now(timezone.utc).date().isoformat()
        key = f'{bot}:daily'
        daily = self.db.load_risk_state(key) or {}
        if daily.get('day') != day:
            daily = {'day':day,'start':equity,'tripped':False}
        daily['tripped'] |= equity <= daily['start'] * (1-self.config.daily_loss_limit_pct)
        self.db.save_risk_state(key,daily)
        return daily

    def _compute_entry_gate(self, state, heartbeat_age_sec=None):
        cycles = int(state.get('cycles') or 0)
        heartbeat = state.get('completed_at') or state.get('started_at')
        if heartbeat_age_sec is None and heartbeat:
            heartbeat_age_sec = (datetime.now(timezone.utc) - datetime.fromisoformat(heartbeat)).total_seconds()
        is_stale = heartbeat is not None and (heartbeat_age_sec is None or heartbeat_age_sec > self.config.cycle_timeout_sec + 120)
        risk = state.get('risk') or {}
        cb_map = state.get('daily_circuit_breakers') or {}
        tripped_sleeves = [b for b, v in cb_map.items() if isinstance(v, dict) and v.get('tripped')]
        rejections = state.get('rejections') or []
        orders_count = len(state.get('orders') or [])
        min_notional_blocked = sum(
            1 for r in rejections
            if r.get('reason') == 'blocked_below_min_order_notional'
            or any('instrument_minimum' in str(x) for x in (r.get('reasons') or []))
        )
        stale_blocked = sum(
            1 for r in rejections
            if 'stale' in str(r.get('reason') or '')
            or any('stale' in str(x) for x in (r.get('reasons') or []))
        )
        no_markets = any(r.get('reason') == 'no_verified_markets' for r in rejections)
        worker_error = state.get('worker_health') in ('cycle_error', 'degraded')

        reasons = []
        if cycles == 0 and heartbeat is None:
            gate_status = 'INITIALIZING'
            reasons.append('awaiting_first_cycle')
        elif is_stale:
            gate_status = 'STALE'
            reasons.append('worker_heartbeat_stale')
        elif worker_error:
            gate_status = 'BLOCKED'
            reasons.append('worker_cycle_error')
        elif not self.campaign.entries_allowed():
            gate_status = 'EXPIRED'
            reasons.append('campaign_window_closed')
        elif risk.get('emergency_stop_active') or risk.get('circuit_breaker_tripped'):
            gate_status = 'BLOCKED'
            reasons.append('portfolio_risk_circuit_breaker')
        elif len(tripped_sleeves) == len(BANDS):
            gate_status = 'BLOCKED'
            reasons.append('all_sleeves_daily_loss_tripped')
        elif no_markets:
            gate_status = 'BLOCKED'
            reasons.append('no_verified_markets')
        elif orders_count == 0 and min_notional_blocked > 0:
            gate_status = 'BLOCKED'
            reasons.append('min_order_notional_blocked')
        elif orders_count == 0 and stale_blocked > 0:
            gate_status = 'BLOCKED'
            reasons.append('stale_market_data_blocked')
        else:
            gate_status = 'OPEN'

        if tripped_sleeves and 'all_sleeves_daily_loss_tripped' not in reasons:
            reasons.append(f'sleeve_daily_breaker:{",".join(tripped_sleeves)}')

        return {
            'status': gate_status,
            'reasons': reasons,
            'min_notional_blocked_count': min_notional_blocked,
            'stale_blocked_count': stale_blocked,
            'tripped_sleeves': tripped_sleeves,
            'entries_allowed': self.campaign.entries_allowed() and gate_status == 'OPEN',
        }

    def _populate_summary_fields(self, state_dict=None):
        target = self.state if state_dict is None else state_dict
        accounts = self.db.list_accounts()
        positions = self.db.list_positions()
        by_bot = {a['bot_id']: a for a in accounts}
        pos_by_bot = {}
        for p in positions:
            pos_by_bot.setdefault(p.bot_id, []).append(p)

        sleeves = []
        total_initial = 0.0
        total_cash = 0.0
        total_reserved = 0.0
        total_realized = 0.0
        total_unrealized = 0.0

        for band in BANDS:
            bot = self.bot_id(band)
            acct = by_bot.get(bot)
            pol = self.policy(band)
            if acct is None:
                continue
            bot_positions = pos_by_bot.get(bot, [])
            initial_u = float(acct.get('initial_equity_usd') or 0.0)
            cash_u = float(acct.get('cash_usd') or 0.0)
            reserved_u = float(acct.get('reserved_margin_usd') or 0.0)
            realized_u = float(acct.get('realized_pnl_usd') or 0.0)
            unrealized_u = sum(float(p.unrealized_pnl_usd or 0.0) for p in bot_positions)
            equity_u = cash_u + unrealized_u
            free_u = max(0.0, cash_u - reserved_u)
            daily = self.db.load_risk_state(f'{bot}:daily') or {}

            total_initial += initial_u
            total_cash += cash_u
            total_reserved += reserved_u
            total_realized += realized_u
            total_unrealized += unrealized_u

            sleeves.append({
                'band': band.value,
                'bot_id': bot,
                'initial_usdt': round(initial_u, 2),
                'cash_usdt': round(cash_u, 2),
                'reserved_usdt': round(reserved_u, 2),
                'free_usdt': round(free_u, 2),
                'realized_pnl_usdt': round(realized_u, 4),
                'unrealized_pnl_usdt': round(unrealized_u, 4),
                'equity_usdt': round(equity_u, 2),
                'open_positions': len(bot_positions),
                'max_risk_per_trade_pct': pol.max_risk_per_trade_pct,
                'max_risk_per_trade_usdt': round(equity_u * pol.max_risk_per_trade_pct, 2),
                'max_position_usdt': round(equity_u * 0.20, 2),
                'daily_loss_tripped': bool(daily.get('tripped', False)),
            })

        total_equity = total_cash + total_unrealized
        target['campaign_id'] = self.campaign_id
        target['mode'] = 'PAPER_ONLY'
        target['accounts'] = accounts
        target['position_details'] = [p.model_dump(mode='json') for p in positions]
        target['positions'] = len(positions)
        target['fills'] = len(self.db.list_fills(limit=10000))
        target['risk'] = self.engine.risk.status_summary(self.engine.total_portfolio_equity(), positions)
        target['daily_circuit_breakers'] = {
            band.value: self.db.load_risk_state(f'{self.bot_id(band)}:daily') for band in BANDS
        }
        target['reconciliation'] = self.engine.reconcile_positions()
        target['campaign'] = self.campaign.status()
        target['sleeves'] = sleeves
        target['budget'] = {
            'total_initial_usdt': round(total_initial, 2),
            'sleeve_initial_usdt': round(float(self.config.sleeve_capital_usd), 2),
            'sleeve_count': len(sleeves),
            'current_equity_usdt': round(total_equity, 2),
            'current_cash_usdt': round(total_cash, 2),
            'reserved_margin_usdt': round(total_reserved, 2),
            'free_cash_usdt': round(max(0.0, total_cash - total_reserved), 2),
            'current_realized_pnl_usdt': round(total_realized, 4),
            'current_unrealized_pnl_usdt': round(total_unrealized, 4),
            'current_total_pnl_usdt': round(total_equity - total_initial, 4),
            'leverage': 0,
            'borrowed_leverage': 0.0,
            'separate_from': [
                'closed_loop_unified_router',
                '18_agent_treasury',
                'legacy_preview_ledger',
                'historical_5000_archive',
            ],
        }
        target['risk_budget'] = {
            'leverage': 0,
            'max_leverage_ratio': self.engine.risk.policy.max_leverage,
            'min_order_notional_usd': self.config.min_order_notional_usd,
            'max_position_pct_per_sleeve': 0.20,
            'max_simultaneous_positions_per_sleeve': self.config.max_simultaneous_positions,
            'daily_loss_limit_pct_per_sleeve': self.config.daily_loss_limit_pct,
            'portfolio_daily_loss_limit_pct': self.engine.risk.policy.daily_loss_limit_pct,
            'portfolio_max_drawdown_pct': self.engine.risk.policy.max_drawdown_pct,
            'portfolio_max_exposure_pct': self.engine.risk.policy.max_portfolio_exposure_pct,
            'per_symbol_max_exposure_pct': self.engine.risk.policy.max_per_symbol_exposure_pct,
            'band_risk_pct': {band.value: self.policy(band).max_risk_per_trade_pct for band in BANDS},
        }
        target['historical_archive'] = read_historical_archive_summary(self.historical_archive_dir)
        target['entry_gate'] = self._compute_entry_gate(target)

    async def run_cycle(self):
        if not self.config.enabled:
            return {'enabled': False}
        async with self._lock:
            self.state = {'cycles': self.state.get('cycles', 0)+1, 'started_at': datetime.now(timezone.utc).isoformat(),
                          'mode': 'PAPER_ONLY', 'worker_health': 'running', 'rejections': [], 'orders': [],
                          'gate_io': 'blocked:no_validated_adapter'}
            self._populate_summary_fields()
            self.db.save_risk_state('violet_all_cap_heartbeat', self.state)
            try:
                await asyncio.wait_for(self._cycle(), self.config.cycle_timeout_sec)
            except Exception as exc:
                self.state['rejections'].append({'reason': f'cycle_failed:{type(exc).__name__}'})
            finally:
                self.state['completed_at'] = datetime.now(timezone.utc).isoformat()
                self.state['exchanges'] = {key.value: adapter.health_status() for key, adapter in self.registry.all().items()
                                           if hasattr(adapter, 'health_status')}
                self.state['gate_io'] = 'public_adapter_enabled' if ExchangeId.GATEIO in self.registry.all() else 'disabled:no_validated_public_adapter'
                self.state['worker_health'] = 'degraded' if any(r.get('reason', '').startswith('cycle_failed:') for r in self.state['rejections']) else 'running'
                self._populate_summary_fields()
                self.campaign.observe(self.state)
                self.state['campaign'] = self.campaign.status()
                self.state['entry_gate'] = self._compute_entry_gate(self.state)
                self.db.save_risk_state('violet_all_cap_heartbeat', self.state)
            return self.state

    async def _cycle(self):
        self.selector._oos_cache.clear()
        for band in BANDS:
            self.sleeve_daily_state(band)
        self.engine.risk.update_equity_watermarks(self.engine.total_portfolio_equity())
        rejections = self.state['rejections']
        markets = []
        for adapter in self.registry.all().values() if self.campaign.entries_allowed() else []:
            try:
                markets.extend(await self.discovery.discover_for_exchange(adapter, force=True))
            except Exception as exc:
                rejections.append({'exchange': adapter.exchange_id.value, 'reason': f'discovery_failed:{type(exc).__name__}'})
        # Interleave venue and cap band so a bounded batch covers every venue.
        exchanges = list(self.registry.all())
        groups = {(band,exchange):[m for m in markets if m.cap_record.band == band and m.exchange == exchange]
                  for band in BANDS for exchange in exchanges}
        fair = []
        for i in range(max((len(g) for g in groups.values()), default=0)):
            for j in range(len(exchanges)):
                for k, band in enumerate(BANDS):
                    group = groups[(band,exchanges[(j+k)%len(exchanges)])]
                    if i < len(group):
                        fair.append(group[i])
        batch = self.discovery.next_global_scan_batch(fair)
        self.state.update(discovered=len(markets), scanned=len(batch), cap_provider=self.provider.status_summary(),
                          discovered_by_band={band.value: sum(m.cap_record.band == band for m in markets) for band in BANDS})
        tier_labels = {
            MarketCapBand.MICRO: 'Micro Cap (<$50M)',
            MarketCapBand.LOW: 'Low Cap ($50M-$300M)',
            MarketCapBand.MID: 'Mid Cap ($300M-$10B)',
            MarketCapBand.LARGE: 'High / Large Cap ($10B-$200B)',
            MarketCapBand.MEGA: 'Mega Cap ($200B+)',
        }
        tiers_summary = {}
        for band in BANDS:
            band_markets = sorted(
                [m for m in markets if m.cap_record.band == band],
                key=lambda m: float(m.cap_record.market_cap_usd or 0.0),
                reverse=True,
            )
            tiers_summary[band.value] = {
                'band': band.value,
                'label': tier_labels[band],
                'discovered_count': len(band_markets),
                'scanned_count': sum(1 for m in batch if m.cap_record.band == band),
                'passed_count': 0,
                'top_symbols': [
                    {
                        'symbol': m.symbol,
                        'exchange': m.exchange.value,
                        'last_price': getattr(m, 'last_price', None),
                        'volume_24h_usd': round(float(getattr(m, 'volume_24h_usd', 0.0) or 0.0), 2),
                        'market_cap_usd': round(float(m.cap_record.market_cap_usd or 0.0), 2),
                    }
                    for m in band_markets[:6]
                ],
            }
        self.state['market_cap_tiers'] = tiers_summary
        res_cats = {
            'mega_100b_plus': {'label': 'Mega Cap ($100B+)', 'count': 0, 'symbols': []},
            'large_10b_100b': {'label': 'Large Cap ($10B-$100B)', 'count': 0, 'symbols': []},
            'mid_1b_10b': {'label': 'Mid Cap ($1B-$10B)', 'count': 0, 'symbols': []},
            'small_100m_1b': {'label': 'Small Cap ($100M-$1B)', 'count': 0, 'symbols': []},
            'micro_10m_100m': {'label': 'Micro Cap ($10M-$100M)', 'count': 0, 'symbols': []},
            'nano_under_10m': {'label': 'Nano Cap (<$10M)', 'count': 0, 'symbols': []},
            'unknown_unverified': {'label': 'New / Unverified Cap', 'count': 0, 'symbols': []},
        }
        for m in markets:
            cat = classify_research_cap_category(getattr(m.cap_record, 'market_cap_usd', None))
            res_cats[cat]['count'] += 1
            if len(res_cats[cat]['symbols']) < 6:
                res_cats[cat]['symbols'].append({'symbol': m.symbol, 'exchange': m.exchange.value, 'market_cap_usd': m.cap_record.market_cap_usd, 'trade_eligible': True})
        for extra_list in getattr(self.discovery, '_research_extra', {}).values():
            for item in extra_list:
                cat = classify_research_cap_category(item.get('market_cap_usd'))
                res_cats[cat]['count'] += 1
                if len(res_cats[cat]['symbols']) < 6:
                    res_cats[cat]['symbols'].append(item)
        try:
            from app.dashboard.violet_solana_bridge import solana_summary
            sol = solana_summary()
            for route in (sol.get('routes') or [])[:6]:
                res_cats['unknown_unverified']['count'] += 1
                if len(res_cats['unknown_unverified']['symbols']) < 6:
                    res_cats['unknown_unverified']['symbols'].append({
                        'symbol': route.get('dataset') or 'SOL_DEX',
                        'exchange': 'solana_dex',
                        'market_cap_usd': None,
                        'trade_eligible': False,
                        'research_only': True,
                        'reason': 'solana_dex_research_only',
                    })
        except Exception:
            pass
        self.state['research_cap_categories'] = res_cats
        self.state['discovered_markets'] = [
            {
                'symbol': m.symbol,
                'exchange': m.exchange.value,
                'market_type': 'spot',
                'band': m.cap_record.band.value,
                'last_price': getattr(m, 'last_price', None),
                'volume_24h_usd': round(float(getattr(m, 'volume_24h_usd', 0.0) or 0.0), 2),
                'market_cap_usd': round(float(m.cap_record.market_cap_usd or 0.0), 2),
            }
            for m in fair[:40]
        ]
        if not markets and self.campaign.entries_allowed():
            rejections.append({'reason': 'no_verified_markets', 'provider_error': self.provider.last_error})
        # Open positions are refreshed first even when outside the discovery batch.
        targets = {}
        for pos in self.db.list_positions():
            targets[(pos.exchange, pos.symbol)] = self.provider.classify_symbol(pos.symbol.split('/')[0])
        for m in batch:
            targets.setdefault((m.exchange,m.symbol),m.cap_record)
        fresh = {}
        for (exchange, symbol), cap in targets.items():
            checked = CheckedPublicAdapter(self.registry.get(exchange), self.config.max_market_data_age_sec)
            snap = await self.scanner.scan_pair(checked, symbol, MarketType.SPOT, False)
            if snap is None:
                rejections.append({'symbol': symbol, 'exchange': exchange.value, 'reason': checked.last_error or 'public_scan_failed'})
                continue
            reasons = self.safety_reasons(snap, cap)
            self.state.setdefault('market_data', {})[f'{exchange.value}:{symbol}'] = {
                'ticker_source_timestamp_ms': snap.ticker_source_timestamp_ms,
                'book_source_timestamp_ms': snap.book_source_timestamp_ms,
                'validated': not reasons, 'reasons': reasons}
            # Cap, volume and strategy entry gates must not strand an existing stop.
            if self.safe_for_protective_exit(snap):
                fresh[f'{exchange.value}:spot:{symbol}'] = snap
                self.engine.mark_to_market_and_check_exits({f'{exchange.value}:spot:{symbol}': snap})
            if not self.campaign.entries_allowed():
                rejections.append({'symbol': symbol, 'exchange': exchange.value, 'band': cap.band.value, 'reason': 'paper_campaign_entries_closed'})
                continue
            if reasons:
                rejections.append({'symbol': symbol, 'exchange': exchange.value, 'band': cap.band.value, 'reasons': reasons})
                continue
            if cap.band.value in self.state.get('market_cap_tiers', {}):
                self.state['market_cap_tiers'][cap.band.value]['passed_count'] += 1
            try:
                await self.prepare_fees(self.registry.get(exchange), symbol)
            except Exception as exc:
                rejections.append({'symbol': symbol, 'exchange': exchange.value, 'band': cap.band.value, 'reason': f'instrument_validation_failed:{type(exc).__name__}'})
                continue
            signals, diagnostics = self.selector.select_signals([snap], {cap.base_symbol: cap})
            for d in diagnostics:
                d.setdefault('exchange', exchange.value)
                d.setdefault('band', cap.band.value)
            rejections.extend(diagnostics)
            if not signals and not diagnostics:
                rejections.append({'symbol': symbol, 'exchange': exchange.value, 'band': cap.band.value, 'reason': 'no_regime_strategy_signal'})
            for sig in signals:
                band = cap.band
                bot = self.bot_id(band)
                positions = self.db.list_positions(bot)
                daily = self.sleeve_daily_state(band)
                if daily['tripped'] or len(positions) >= self.config.max_simultaneous_positions:
                    rejections.append({'symbol': symbol, 'exchange': exchange.value, 'band': band.value, 'strategy': sig.strategy_name, 'reason': 'sleeve_daily_loss_or_position_limit'})
                    continue
                # Recheck wall-clock freshness immediately before submitting to local ledger.
                reasons = self.safety_reasons(snap, self.provider.classify_symbol(cap.base_symbol))
                notional = self.size(sig, snap, band) if not reasons else 0
                if notional <= 0:
                    rejections.append({'symbol': symbol, 'exchange': exchange.value, 'band': band.value, 'strategy': sig.strategy_name, 'reason': 'invalid_stop_tp_net_rr_or_insufficient_risk_budget', 'details': reasons})
                    continue
                if notional < self.config.min_order_notional_usd:
                    rejections.append({
                        'symbol': symbol,
                        'exchange': exchange.value,
                        'band': band.value,
                        'strategy': sig.strategy_name,
                        'reason': 'blocked_below_min_order_notional',
                        'notional_usd': round(notional, 2),
                        'min_order_notional_usd': self.config.min_order_notional_usd,
                        'details': reasons,
                    })
                    continue
                try:
                    spec = await self.public_spec(self.registry.get(exchange), symbol)
                except Exception:
                    rejections.append({'symbol': symbol, 'exchange': exchange.value, 'band': band.value, 'strategy': sig.strategy_name, 'reasons': ['instrument_spec_unavailable']})
                    continue
                aligned_entry = _quantize_to_step(sig.entry_high, spec.price_precision, round_nearest=True)
                if aligned_entry <= 0:
                    aligned_entry = sig.entry_high
                aligned_qty = _quantize_to_step(notional / snap.last_price, spec.amount_precision, round_nearest=False)
                aligned_notional = round(aligned_qty * snap.last_price, 6)
                if aligned_notional < self.config.min_order_notional_usd or aligned_qty <= 0:
                    rejections.append({
                        'symbol': symbol,
                        'exchange': exchange.value,
                        'band': band.value,
                        'strategy': sig.strategy_name,
                        'reason': 'blocked_below_min_order_notional',
                        'notional_usd': round(aligned_notional, 2),
                        'min_order_notional_usd': self.config.min_order_notional_usd,
                    })
                    continue
                sig = sig.model_copy(update={'entry_high': aligned_entry})
                notional = aligned_notional
                instrument_reasons = await self.instrument_reasons(self.registry.get(exchange), sig, snap, notional)
                if instrument_reasons:
                    rejections.append({'symbol': symbol, 'exchange': exchange.value, 'band': band.value, 'strategy': sig.strategy_name, 'reasons': instrument_reasons})
                    continue
                if not self.campaign.entries_allowed():
                    rejections.append({'symbol': symbol, 'exchange': exchange.value, 'band': band.value, 'strategy': sig.strategy_name, 'reason': 'paper_campaign_entries_closed'})
                    continue
                oid = f'{bot}:{exchange.value}:{symbol}:{sig.strategy_name}:{int(snap.timestamp.timestamp()//60)}'
                order = self.engine.submit_signal_order(bot, sig, notional, 1, snap, oid, True,
                                                        order_type=OrderType.LIMIT, limit_price=sig.entry_high)
                self.state['orders'].append(order.model_dump(mode='json'))
                now_iso = datetime.now(timezone.utc).isoformat()
                self.db.append_ledger_event(
                    event_id=f"ord:{order.order_id}",
                    timestamp_utc=now_iso,
                    event_type="ORDER_SUBMITTED" if order.status.value != "rejected" else "ORDER_REJECTED",
                    bot_id=bot,
                    exchange=exchange.value,
                    symbol=symbol,
                    market_cap_band=band.value,
                    strategy=sig.strategy_name,
                    order_id=order.order_id,
                    amount_usd=str(round(notional, 4)),
                    details={"status": order.status.value, "reason": order.reject_reason},
                    idempotency_key=f"ord:{order.order_id}",
                )
                for fill in self.db.list_fills_for_order(order.order_id):
                    ev_type = "PARTIAL_FILL" if order.status.value == "partially_filled" else "FILL"
                    self.db.append_ledger_event(
                        event_id=f"fill:{fill.fill_id}",
                        timestamp_utc=fill.timestamp.isoformat(),
                        event_type=ev_type,
                        bot_id=bot,
                        exchange=exchange.value,
                        symbol=symbol,
                        market_cap_band=band.value,
                        strategy=sig.strategy_name,
                        order_id=order.order_id,
                        fill_id=fill.fill_id,
                        amount_usd=str(round(fill.qty * fill.price, 4)),
                        fee_usd=str(round(fill.fee_usd, 6)),
                        details={"qty": fill.qty, "price": fill.price, "slippage_bps": fill.slippage_bps},
                        idempotency_key=f"fill:{fill.fill_id}",
                    )
        now = datetime.now(timezone.utc)
        fresh = {key: snap for key, snap in fresh.items()
                 if 0 <= (now-snap.timestamp).total_seconds() <= self.config.max_market_data_age_sec}
        self.engine.mark_to_market_and_check_exits(fresh)
        for closure in self.db.list_realized_closures():
            cid = closure.get('order_id')
            if not cid:
                continue
            net_realized = float(closure.get('gross_realized_usd') or 0.0) - float(closure.get('exit_fee_usd') or 0.0)
            self.db.append_ledger_event(
                event_id=f"close:{cid}",
                timestamp_utc=str(closure.get('created_at') or now.isoformat()),
                event_type="POSITION_CLOSE",
                bot_id=str(closure.get('bot_id') or ''),
                exchange=str(closure.get('exchange') or ''),
                symbol=str(closure.get('symbol') or ''),
                market_cap_band='',
                strategy='',
                order_id=str(cid),
                amount_usd=str(round(float(closure.get('close_qty') or 0.0) * float(closure.get('exit_price') or 0.0), 4)),
                fee_usd=str(round(float(closure.get('exit_fee_usd') or 0.0), 6)),
                realized_pnl_usd=str(round(net_realized, 6)),
                details={"entry_price": closure.get("entry_price"), "exit_price": closure.get("exit_price"), "close_qty": closure.get("close_qty")},
                idempotency_key=f"close:{cid}",
            )
        self.state['positions'] = len(self.db.list_positions())
        self.state['fills'] = len(self.db.list_fills(limit=10000))
        self.state['rejections'] = rejections[:100]

    def pnl_report(self, **filters):
        cap_lookup = {}
        for item in (self.state.get('discovered_markets') or []):
            sym = item.get('symbol')
            if sym:
                cap_lookup[sym] = {
                    'band': item.get('band'),
                    'market_cap_usd': item.get('market_cap_usd'),
                    'category': classify_research_cap_category(item.get('market_cap_usd')),
                }
        return build_pnl_report(
            self.db,
            rejections=self.state.get('rejections') or [],
            cap_lookup=cap_lookup,
            **filters,
        )

    def close(self):
        self.db.close()
        self._worker_lock.close()

    def status(self):
        result = dict(self.state)
        if not result.get('budget') or not result.get('sleeves') or not result.get('campaign_id'):
            self._populate_summary_fields(result)
        result['campaign'] = self.campaign.status()
        if hasattr(self,'legacy_protection'):
            result['legacy_preview'] = self.legacy_protection
        heartbeat = result.get('completed_at') or result.get('started_at')
        age = (datetime.now(timezone.utc)-datetime.fromisoformat(heartbeat)).total_seconds() if heartbeat else None
        result['heartbeat_age_sec'] = age
        result.setdefault('worker_health', 'running')
        if age is None or age > self.config.cycle_timeout_sec + 120:
            result['worker_health'] = 'stale_or_not_started'
        result['entry_gate'] = self._compute_entry_gate(result, heartbeat_age_sec=age)
        return result
