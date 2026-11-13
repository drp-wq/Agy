import React, { useEffect, useRef } from 'react';
import { createChart, AreaSeries, ColorType, Time } from 'lightweight-charts';
import { X, LineChart, ShieldCheck } from 'lucide-react';
import { FuturesPosition } from '../../types/quant';
import { EXCHANGE_META } from '../common/TerminalPrimitives';
import { formatPrice, formatSignedPct, formatSignedUsd, formatTimeShort, formatUsd } from '../../utils/formatters';

interface PositionDetailDrawerProps {
  position: FuturesPosition | null;
  onClose: () => void;
  onOpenChart: (pos: FuturesPosition) => void;
}

export const PositionDetailDrawer: React.FC<PositionDetailDrawerProps> = ({
  position,
  onClose,
  onOpenChart
}) => {
  const chartRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = chartRef.current;
    if (!container || !position?.pnl_history || position.pnl_history.length === 0) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 150,
      layout: {
        background: { type: ColorType.Solid, color: '#05080F' },
        textColor: '#94A3B8',
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 10
      },
      grid: {
        vertLines: { color: 'rgba(148, 163, 184, 0.05)' },
        horzLines: { color: 'rgba(148, 163, 184, 0.05)' }
      },
      rightPriceScale: { borderColor: 'rgba(148, 163, 184, 0.14)' },
      timeScale: { borderColor: 'rgba(148, 163, 184, 0.14)', timeVisible: true }
    });

    const isPositive = position.unrealized_pnl_usd >= 0;
    const area = chart.addSeries(AreaSeries, {
      lineColor: isPositive ? '#10B981' : '#EF4444',
      topColor: isPositive ? 'rgba(16, 185, 129, 0.28)' : 'rgba(239, 68, 68, 0.28)',
      bottomColor: 'rgba(5, 8, 15, 0.0)',
      lineWidth: 2
    });

    area.setData(
      position.pnl_history.map((pt) => ({
        time: pt.time as Time,
        value: pt.pnl_usd
      }))
    );
    chart.timeScale().fitContent();

    return () => chart.remove();
  }, [position]);

  if (!position) return null;

  const meta = EXCHANGE_META[position.exchange];
  const pnlUsd = position.status === 'open' ? position.unrealized_pnl_usd : position.realized_pnl_usd;

  return (
    <div
      className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex justify-end items-end md:items-stretch"
      onClick={onClose}
    >
      <div
        className="w-full md:max-w-md bg-[#0B1120] border-t md:border-t-0 md:border-l border-white/[0.12] rounded-t-2xl md:rounded-none p-5 pb-safe max-h-[88vh] md:max-h-full overflow-y-auto flex flex-col justify-between gap-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="space-y-5">
          {/* Mobile grab handle */}
          <div className="w-10 h-1 bg-slate-700 rounded-full mx-auto md:hidden" />

          {/* Header */}
          <div className="flex items-start justify-between gap-3 pb-3 border-b border-white/[0.08]">
            <div>
              <div className="flex items-center gap-2 text-xs font-mono">
                <span className={`font-semibold uppercase ${meta.accentText}`}>{meta.name}</span>
                <span className="text-slate-500">·</span>
                <span className="uppercase text-slate-300">{position.account_mode}</span>
                <span className="text-slate-500">·</span>
                <span className="uppercase text-slate-400">{position.status}</span>
              </div>
              <h3 className="text-xl font-mono font-bold text-white mt-1">
                {position.instrument_id || position.symbol}{' '}
                <span className={position.side === 'LONG' ? 'text-[#10B981]' : 'text-[#EF4444]'}>
                  {position.side} {position.leverage}x
                </span>
              </h3>
            </div>
            <button
              onClick={onClose}
              aria-label="Close position drawer"
              className="p-2 min-h-[40px] min-w-[40px] flex items-center justify-center rounded-lg text-slate-400 hover:text-white hover:bg-white/5"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Primary PnL Readout */}
          <div className="p-4 rounded-xl bg-[#05080F] border border-white/[0.08] flex items-center justify-between font-mono">
            <div>
              <div className="text-[11px] text-slate-400 font-sans">
                {position.status === 'open' ? 'Unrealized PnL' : 'Realized PnL'}
              </div>
              <div className={`text-2xl font-bold mt-0.5 ${pnlUsd >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}`}>
                {formatSignedUsd(pnlUsd)}
              </div>
            </div>
            {position.status === 'open' && (
              <div className="text-right">
                <div className="text-[11px] text-slate-400 font-sans">Return on Margin</div>
                <div
                  className={`text-base font-semibold mt-0.5 ${
                    position.unrealized_pnl_pct >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'
                  }`}
                >
                  {formatSignedPct(position.unrealized_pnl_pct)}
                </div>
              </div>
            )}
          </div>

          {/* Position Intraday PnL Curve */}
          {position.pnl_history && position.pnl_history.length > 0 && (
            <div className="rounded-xl overflow-hidden border border-white/[0.08]">
              <div className="px-3 py-2 bg-[#05080F] border-b border-white/[0.06] text-[11px] font-mono text-slate-400">
                Position PnL Trajectory (USD)
              </div>
              <div ref={chartRef} className="w-full bg-[#05080F]" />
            </div>
          )}

          {/* Specification Grid */}
          <div className="divide-y divide-white/[0.06] text-xs font-mono">
            <div className="py-2 flex justify-between">
              <span className="text-slate-400 font-sans">Quantity</span>
              <span className="text-white">{position.quantity}</span>
            </div>
            <div className="py-2 flex justify-between">
              <span className="text-slate-400 font-sans">Notional Exposure</span>
              <span className="text-white">{formatUsd(position.notional_usd)}</span>
            </div>
            <div className="py-2 flex justify-between">
              <span className="text-slate-400 font-sans">Margin & Mode</span>
              <span className="text-white">
                {formatUsd(position.margin_usd)} ({position.margin_mode})
              </span>
            </div>
            <div className="py-2 flex justify-between">
              <span className="text-slate-400 font-sans">Entry / Mark Price</span>
              <span className="text-white">
                {formatPrice(position.entry_price)} / {formatPrice(position.mark_price)}
              </span>
            </div>
            <div className="py-2 flex justify-between">
              <span className="text-slate-400 font-sans">Liquidation Price</span>
              <span className="text-amber-400">
                {formatPrice(position.liquidation_price)}
                {position.liquidation_distance_pct ? ` (${position.liquidation_distance_pct.toFixed(1)}% away)` : ''}
              </span>
            </div>
            <div className="py-2 flex justify-between">
              <span className="text-slate-400 font-sans">Stop-Loss / Take-Profit</span>
              <span className="text-white">
                <span className="text-[#EF4444]">{formatPrice(position.stop_loss)}</span>
                {' / '}
                <span className="text-[#10B981]">{formatPrice(position.take_profit)}</span>
              </span>
            </div>
            <div className="py-2 flex justify-between">
              <span className="text-slate-400 font-sans">SL/TP Protection</span>
              <span className="text-[#00E5FF]">{position.sl_tp_status}</span>
            </div>
            <div className="py-2 flex justify-between">
              <span className="text-slate-400 font-sans">Strategy Engine</span>
              <span className="text-slate-200">{position.strategy_name}</span>
            </div>
            <div className="py-2 flex justify-between">
              <span className="text-slate-400 font-sans">Opened At</span>
              <span className="text-slate-300">{formatTimeShort(position.opened_at)}</span>
            </div>
          </div>
        </div>

        {/* Footer Actions */}
        <div className="pt-3 border-t border-white/[0.08] flex items-center justify-between gap-3">
          <div className="flex items-center gap-1.5 text-[11px] text-[#10B981]">
            <ShieldCheck className="w-4 h-4" />
            <span>Read-Only Inspector</span>
          </div>
          <button
            onClick={() => onOpenChart(position)}
            className="px-4 py-2 min-h-[42px] rounded-lg bg-[#00E5FF]/15 hover:bg-[#00E5FF]/25 border border-[#00E5FF]/35 text-[#00E5FF] text-xs font-medium flex items-center gap-1.5 transition-colors"
          >
            <LineChart className="w-3.5 h-3.5" />
            <span>Inspect on Chart</span>
          </button>
        </div>
      </div>
    </div>
  );
};
