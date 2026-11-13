import { EquityPoint, ExchangeId, MiniAppStateResponse, RiskCommandState } from '../types/quant';
import { EXCHANGE_META } from '../components/common/TerminalPrimitives';
import { formatSignedUsd, formatUsd } from './formatters';

export interface RealizedUnrealizedSummary {
  title: string;
  primaryUsd: number;
  realizedUsd: number | null;
  unrealizedUsd: number;
  realizedLabel: string;
  lifetimeRealizedUsd: number | null;
  isDailyAvailable: boolean;
}

export interface DrawdownSummary {
  available: boolean;
  currentPct: number | null;
  hwmEquityUsd: number | null;
  limitPct: number;
  displayValue: string;
  subtitle: string;
}

export interface ExposureAllocationSummary {
  grossNotionalUsd: number;
  reservedMarginUsd: number;
  freeCapitalUsd: number;
  marginAllocationPct: number;
  notionalExposurePct: number;
}

export interface LiveReadOnlyAuthSummary {
  authedCount: number;
  totalExchanges: number;
  authedExchanges: ExchangeId[];
  usdValuationAvailable: boolean;
  headline: string;
  detail: string;
}

export function summarizeRealizedUnrealized(risk: RiskCommandState): RealizedUnrealizedSummary {
  const dailyAvailable = risk.daily_realized_available !== false && risk.daily_realized_pnl_usd !== null;
  const lifetimeUsd =
    risk.lifetime_realized_pnl_usd !== undefined ? risk.lifetime_realized_pnl_usd : null;

  if (dailyAvailable && risk.daily_realized_pnl_usd !== null) {
    return {
      title: 'UTC Day Realized / Open PnL',
      primaryUsd: Number((risk.daily_realized_pnl_usd + risk.daily_unrealized_pnl_usd).toFixed(2)),
      realizedUsd: risk.daily_realized_pnl_usd,
      unrealizedUsd: risk.daily_unrealized_pnl_usd,
      realizedLabel: 'UTC Day Realized',
      lifetimeRealizedUsd: lifetimeUsd,
      isDailyAvailable: true
    };
  }

  const primaryUsd =
    lifetimeUsd !== null
      ? Number((lifetimeUsd + risk.daily_unrealized_pnl_usd).toFixed(2))
      : Number(risk.daily_unrealized_pnl_usd.toFixed(2));

  return {
    title: lifetimeUsd !== null ? 'Lifetime Realized / Open PnL' : 'Open Unrealized PnL',
    primaryUsd,
    realizedUsd: lifetimeUsd,
    unrealizedUsd: risk.daily_unrealized_pnl_usd,
    realizedLabel:
      lifetimeUsd !== null ? 'Lifetime Realized (UTC Day Unavailable)' : 'UTC Day Realized Unavailable',
    lifetimeRealizedUsd: lifetimeUsd,
    isDailyAvailable: false
  };
}

export function summarizeDrawdown(risk: RiskCommandState): DrawdownSummary {
  const limitPct = Number.isFinite(risk.drawdown_limit_pct) ? risk.drawdown_limit_pct : 0;
  const available = risk.drawdown_available !== false && risk.current_drawdown_pct !== null;
  const hwmEquityUsd = risk.hwm_equity_usd ?? null;

  if (!available || risk.current_drawdown_pct === null) {
    return {
      available: false,
      currentPct: null,
      hwmEquityUsd,
      limitPct,
      displayValue: 'Unavailable',
      subtitle: `HWM untracked · Policy Limit -${limitPct.toFixed(1)}%`
    };
  }

  const currentPct = Math.max(0, risk.current_drawdown_pct);
  const displayValue = currentPct > 0.004 ? `-${currentPct.toFixed(2)}%` : '0.00%';
  const subtitle =
    hwmEquityUsd !== null && hwmEquityUsd > 0
      ? `HWM ${formatUsd(hwmEquityUsd)} · Limit -${limitPct.toFixed(1)}%`
      : `Limit -${limitPct.toFixed(1)}%`;

  return {
    available: true,
    currentPct,
    hwmEquityUsd,
    limitPct,
    displayValue,
    subtitle
  };
}

export function summarizeExposureAndAllocation(risk: RiskCommandState): ExposureAllocationSummary {
  const reservedMarginUsd =
    risk.reserved_margin_usd !== undefined ? risk.reserved_margin_usd : risk.gross_margin_exposure_usd;
  const grossNotionalUsd =
    risk.gross_notional_exposure_usd !== undefined
      ? risk.gross_notional_exposure_usd
      : risk.gross_margin_exposure_usd;
  const freeCapitalUsd =
    risk.free_paper_capital_usd !== undefined
      ? risk.free_paper_capital_usd
      : Math.max(0, Number((risk.total_paper_equity_usd - reservedMarginUsd).toFixed(2)));
  const marginAllocationPct =
    risk.margin_allocation_pct !== undefined
      ? risk.margin_allocation_pct
      : risk.total_paper_equity_usd > 0
        ? Number(((reservedMarginUsd / risk.total_paper_equity_usd) * 100).toFixed(2))
        : risk.risk_allocation_pct;
  const notionalExposurePct =
    risk.notional_exposure_pct !== undefined
      ? risk.notional_exposure_pct
      : risk.total_paper_equity_usd > 0
        ? Number(((grossNotionalUsd / risk.total_paper_equity_usd) * 100).toFixed(2))
        : 0;

  return {
    grossNotionalUsd,
    reservedMarginUsd,
    freeCapitalUsd,
    marginAllocationPct,
    notionalExposurePct
  };
}

export function summarizeLiveReadOnlyAuth(state: MiniAppStateResponse): LiveReadOnlyAuthSummary {
  const order: ExchangeId[] = ['binance', 'bybit', 'okx'];
  const authedExchanges = order.filter((ex) => Boolean(state.exchanges[ex]?.private_auth_ok));
  const authedLabels = authedExchanges.map((ex) => EXCHANGE_META[ex]?.name ?? ex.toUpperCase());
  const totalUsd = state.risk.total_live_readonly_equity_usd;

  if (totalUsd !== null && Number.isFinite(totalUsd)) {
    return {
      authedCount: authedExchanges.length,
      totalExchanges: order.length,
      authedExchanges,
      usdValuationAvailable: true,
      headline: formatUsd(totalUsd),
      detail: `${authedExchanges.length}/${order.length} exchanges synced`
    };
  }

  if (authedExchanges.length > 0) {
    return {
      authedCount: authedExchanges.length,
      totalExchanges: order.length,
      authedExchanges,
      usdValuationAvailable: false,
      headline: `${authedExchanges.length}/${order.length} Authenticated`,
      detail: `Synced: ${authedLabels.join(', ')} · USD aggregate unavailable without FX quotes`
    };
  }

  return {
    authedCount: 0,
    totalExchanges: order.length,
    authedExchanges: [],
    usdValuationAvailable: false,
    headline: 'Not Authenticated',
    detail: 'Public market feeds active · Private read-only sync unauthenticated'
  };
}

export function formatExchangeRealizedPnl(
  realized24h: number | null | undefined,
  lifetimeRealized?: number | null
): { text: string; caption: string; isPositiveOrZero: boolean } {
  if (realized24h !== null && realized24h !== undefined && Number.isFinite(realized24h)) {
    return {
      text: formatSignedUsd(realized24h),
      caption:
        lifetimeRealized !== undefined && lifetimeRealized !== null
          ? `Lifetime ${formatSignedUsd(lifetimeRealized)}`
          : '24h Realized',
      isPositiveOrZero: realized24h >= 0
    };
  }
  if (lifetimeRealized !== undefined && lifetimeRealized !== null && Number.isFinite(lifetimeRealized)) {
    return {
      text: formatSignedUsd(lifetimeRealized),
      caption: 'Lifetime Realized (24h N/A)',
      isPositiveOrZero: lifetimeRealized >= 0
    };
  }
  return {
    text: 'Unavailable',
    caption: 'No verified realized ledger',
    isPositiveOrZero: true
  };
}

export function normalizeEquityCurve(points: EquityPoint[]): EquityPoint[] {
  const byTime = new Map<number, number>();
  for (const pt of points) {
    if (Number.isFinite(pt.time) && Number.isFinite(pt.equity)) {
      byTime.set(Math.floor(pt.time), pt.equity);
    }
  }
  return Array.from(byTime.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([time, equity]) => ({ time, equity }));
}
