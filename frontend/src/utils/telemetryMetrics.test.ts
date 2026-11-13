import { describe, it, expect } from 'vitest';
import assert from 'node:assert/strict';
import {
  formatExchangeRealizedPnl,
  normalizeEquityCurve,
  summarizeDrawdown,
  summarizeExposureAndAllocation,
  summarizeLiveReadOnlyAuth,
  summarizeRealizedUnrealized
} from './telemetryMetrics.ts';
import { buildDemoStateResponse } from '../services/demoFixtures.ts';

describe('telemetryMetrics', () => {
  it('distinguishes UTC day realized PnL vs lifetime fallback when daily is unavailable', () => {
    const base = buildDemoStateResponse();
    base.risk.daily_realized_pnl_usd = -12.34;
    base.risk.daily_realized_available = true;
    base.risk.daily_unrealized_pnl_usd = 3.98;
    base.risk.lifetime_realized_pnl_usd = -87.45;

    const dailyOk = summarizeRealizedUnrealized(base.risk);
    assert.equal(dailyOk.isDailyAvailable, true);
    assert.equal(dailyOk.primaryUsd, -8.36);
    assert.equal(dailyOk.realizedLabel, 'UTC Day Realized');
    assert.equal(dailyOk.lifetimeRealizedUsd, -87.45);

    base.risk.daily_realized_pnl_usd = null;
    base.risk.daily_realized_available = false;
    const legacy = summarizeRealizedUnrealized(base.risk);
    assert.equal(legacy.isDailyAvailable, false);
    assert.equal(legacy.realizedLabel, 'Lifetime Realized (UTC Day Unavailable)');
    assert.equal(legacy.primaryUsd, -83.47);
  });

  it('never renders -0.00% drawdown and marks untracked HWM as Unavailable', () => {
    const base = buildDemoStateResponse();
    base.risk.drawdown_available = false;
    base.risk.current_drawdown_pct = null;
    base.risk.hwm_equity_usd = null;
    const untracked = summarizeDrawdown(base.risk);
    assert.equal(untracked.available, false);
    assert.equal(untracked.displayValue, 'Unavailable');

    base.risk.drawdown_available = true;
    base.risk.current_drawdown_pct = 0;
    base.risk.hwm_equity_usd = 14000;
    const flat = summarizeDrawdown(base.risk);
    assert.equal(flat.available, true);
    assert.equal(flat.displayValue, '0.00%');

    base.risk.current_drawdown_pct = 0.61;
    const activeDd = summarizeDrawdown(base.risk);
    assert.equal(activeDd.displayValue, '-0.61%');
    assert.match(activeDd.subtitle, /14,000\.00/);
  });

  it('separates notional exposure, reserved margin, and free capital', () => {
    const base = buildDemoStateResponse();
    base.risk.total_paper_equity_usd = 13916.52;
    base.risk.gross_notional_exposure_usd = 1311.76;
    base.risk.reserved_margin_usd = 1158.18;
    base.risk.gross_margin_exposure_usd = 1158.18;
    base.risk.free_paper_capital_usd = 12754.36;
    base.risk.margin_allocation_pct = 8.32;
    base.risk.notional_exposure_pct = 9.43;

    const exp = summarizeExposureAndAllocation(base.risk);
    assert.equal(exp.reservedMarginUsd, 1158.18);
    assert.equal(exp.grossNotionalUsd, 1311.76);
    assert.equal(exp.freeCapitalUsd, 12754.36);
    assert.equal(exp.marginAllocationPct, 8.32);
    assert.equal(exp.notionalExposurePct, 9.43);
  });

  it('distinguishes per-exchange private auth from aggregate USD valuation unavailability', () => {
    const base = buildDemoStateResponse();
    const unauthed = summarizeLiveReadOnlyAuth(base);
    assert.equal(unauthed.authedCount, 0);
    assert.equal(unauthed.headline, 'Not Authenticated');

    base.exchanges.binance.private_auth_ok = true;
    base.exchanges.bybit.private_auth_ok = true;
    base.exchanges.okx.private_auth_ok = true;
    base.risk.total_live_readonly_equity_usd = null;

    const allAuthedNoUsd = summarizeLiveReadOnlyAuth(base);
    assert.equal(allAuthedNoUsd.authedCount, 3);
    assert.equal(allAuthedNoUsd.usdValuationAvailable, false);
    assert.equal(allAuthedNoUsd.headline, '3/3 Authenticated');
    assert.match(allAuthedNoUsd.detail, /USD aggregate unavailable/);
  });

  it('handles null realized_pnl_24h and deduplicates equity curve timestamps', () => {
    const na = formatExchangeRealizedPnl(null, -38.12);
    assert.equal(na.caption, 'Lifetime Realized (24h N/A)');
    assert.equal(na.text, '-$38.12');

    const empty = formatExchangeRealizedPnl(null, null);
    assert.equal(empty.text, 'Unavailable');

    const curve = normalizeEquityCurve([
      { time: 200, equity: 10050 },
      { time: 100, equity: 10000 },
      { time: 200, equity: 10075 }
    ]);
    assert.deepEqual(curve, [
      { time: 100, equity: 10000 },
      { time: 200, equity: 10075 }
    ]);
  });
});
