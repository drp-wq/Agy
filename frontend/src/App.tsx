import React, { useState, useEffect, useCallback, useRef, Suspense, lazy } from 'react';
import {
  LayoutDashboard,
  Radar,
  Target,
  Layers,
  Briefcase,
  ClipboardList,
  PieChart,
  ShieldAlert,
  Landmark,
  Bell,
  Server,
  RefreshCw,
  Menu,
  X,
  ChevronRight
} from 'lucide-react';
import {
  FuturesPosition,
  MarketOpportunity,
  MiniAppHealthResponse,
  MiniAppStateResponse,
  NavSectionId,
  Timeframe
} from './types/quant';
import { quantApi, TelegramAuthError } from './services/quantApi';
import { useTelegramWebApp } from './hooks/useTelegramWebApp';
import {
  AuthErrorBanner,
  LoadingTerminalSkeleton,
  ReadOnlySecurityFooterNotice,
  StaleWarningStrip
} from './components/common/TerminalPrimitives';

const GlobalOverviewView = lazy(() =>
  import('./components/workspaces/GlobalOverviewView').then((m) => ({ default: m.GlobalOverviewView }))
);
const MarketScannerView = lazy(() =>
  import('./components/workspaces/MarketScannerView').then((m) => ({ default: m.MarketScannerView }))
);
const OpportunitiesView = lazy(() =>
  import('./components/workspaces/OpportunitiesView').then((m) => ({ default: m.OpportunitiesView }))
);
const ExchangeWorkspaceView = lazy(() =>
  import('./components/workspaces/ExchangeWorkspaceView').then((m) => ({ default: m.ExchangeWorkspaceView }))
);
const PositionsTerminalView = lazy(() =>
  import('./components/workspaces/PositionsTerminalView').then((m) => ({ default: m.PositionsTerminalView }))
);
const PaperOrdersView = lazy(() =>
  import('./components/workspaces/PaperOrdersView').then((m) => ({ default: m.PaperOrdersView }))
);
const PortfolioAnalyticsView = lazy(() =>
  import('./components/workspaces/PortfolioAnalyticsView').then((m) => ({ default: m.PortfolioAnalyticsView }))
);
const RiskCommandView = lazy(() =>
  import('./components/workspaces/RiskCommandView').then((m) => ({ default: m.RiskCommandView }))
);
const TreasuryAuditView = lazy(() =>
  import('./components/workspaces/TreasuryAuditView').then((m) => ({ default: m.TreasuryAuditView }))
);
const TelegramAlertsView = lazy(() =>
  import('./components/workspaces/TelegramAlertsView').then((m) => ({ default: m.TelegramAlertsView }))
);
const SystemHealthView = lazy(() =>
  import('./components/workspaces/SystemHealthView').then((m) => ({ default: m.SystemHealthView }))
);
const PositionDetailDrawer = lazy(() =>
  import('./components/workspaces/PositionDetailDrawer').then((m) => ({ default: m.PositionDetailDrawer }))
);
const ProMarketChart = lazy(() =>
  import('./components/charts/ProMarketChart').then((m) => ({ default: m.ProMarketChart }))
);

interface NavItem {
  id: NavSectionId;
  label: string;
  shortLabel: string;
  group: 'Core Terminal' | 'Exchange Workspaces' | 'Execution & Risk' | 'Operations';
  icon: React.ElementType;
}

const NAV_ITEMS: NavItem[] = [
  { id: 'overview', label: 'Global Overview', shortLabel: 'Overview', group: 'Core Terminal', icon: LayoutDashboard },
  { id: 'scanner', label: 'Market Scanner', shortLabel: 'Scanner', group: 'Core Terminal', icon: Radar },
  { id: 'opportunities', label: 'Trading Opportunities', shortLabel: 'Opportunities', group: 'Core Terminal', icon: Target },
  { id: 'binance', label: 'Binance Workspace', shortLabel: 'Binance', group: 'Exchange Workspaces', icon: Layers },
  { id: 'bybit', label: 'Bybit Workspace', shortLabel: 'Bybit', group: 'Exchange Workspaces', icon: Layers },
  { id: 'okx', label: 'OKX Workspace', shortLabel: 'OKX', group: 'Exchange Workspaces', icon: Layers },
  { id: 'positions', label: 'Positions', shortLabel: 'Positions', group: 'Execution & Risk', icon: Briefcase },
  { id: 'orders', label: 'Paper Orders', shortLabel: 'Orders', group: 'Execution & Risk', icon: ClipboardList },
  { id: 'analytics', label: 'Portfolio Analytics', shortLabel: 'Analytics', group: 'Execution & Risk', icon: PieChart },
  { id: 'risk', label: 'Risk Management', shortLabel: 'Risk', group: 'Execution & Risk', icon: ShieldAlert },
  { id: 'treasury', label: '18-Agent Treasury & Audit', shortLabel: 'Treasury', group: 'Execution & Risk', icon: Landmark },
  { id: 'alerts', label: 'Telegram Alerts', shortLabel: 'Alerts', group: 'Operations', icon: Bell },
  { id: 'system', label: 'System Health', shortLabel: 'System', group: 'Operations', icon: Server }
];

const NAV_GROUPS: Array<NavItem['group']> = [
  'Core Terminal',
  'Exchange Workspaces',
  'Execution & Risk',
  'Operations'
];

export default function App() {
  const telegram = useTelegramWebApp();

  const [activeSection, setActiveSection] = useState<NavSectionId>('overview');
  const [state, setState] = useState<MiniAppStateResponse | null>(null);
  const [health, setHealth] = useState<MiniAppHealthResponse | null>(null);
  const [isDemo, setIsDemo] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  // Mobile "All Workspaces" Sheet
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  // Drawers & Modals
  const [inspectedPosition, setInspectedPosition] = useState<FuturesPosition | null>(null);
  const [inspectedChartOpp, setInspectedChartOpp] = useState<{
    exchange: 'binance' | 'bybit' | 'okx';
    marketType: 'spot' | 'futures';
    symbol: string;
    timeframe: Timeframe;
  } | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  const fetchAll = useCallback(
    async (isBackground = false) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      if (!isBackground) setRefreshing(true);
      setAuthError(null);
      setFetchError(null);

      try {
        const [stateRes, healthRes] = await Promise.all([
          quantApi.getState(telegram.initData, controller.signal),
          quantApi.getHealth(telegram.initData, controller.signal)
        ]);
        if (!controller.signal.aborted) {
          setState(stateRes.data);
          setHealth(healthRes.data);
          setIsDemo(stateRes.isDemo || healthRes.isDemo);
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        if (err instanceof TelegramAuthError) {
          setAuthError(err.message);
        } else {
          setFetchError(err instanceof Error ? err.message : 'Unable to reach FastAPI backend');
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [telegram.initData]
  );

  useEffect(() => {
    fetchAll(false);
    const interval = setInterval(() => {
      if (document.visibilityState === 'visible') {
        fetchAll(true);
      }
    }, 15000);
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        fetchAll(true);
      }
    };
    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      clearInterval(interval);
      document.removeEventListener('visibilitychange', handleVisibility);
      abortRef.current?.abort();
    };
  }, [fetchAll]);

  const handleSelectSection = (id: NavSectionId) => {
    telegram.hapticSelection();
    setActiveSection(id);
    setMobileMenuOpen(false);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const handleInspectOpportunityChart = (opp: MarketOpportunity) => {
    telegram.hapticImpact('light');
    setInspectedChartOpp({
      exchange: opp.exchange,
      marketType: opp.market_type,
      symbol: opp.symbol,
      timeframe: '15m'
    });
  };

  const activeNavMeta = NAV_ITEMS.find((n) => n.id === activeSection) || NAV_ITEMS[0];
  const dataAgeSec = state ? Math.max(0, Math.floor(Date.now() / 1000) - state.generated_at) : 0;

  return (
    <div className="min-h-screen bg-[#05080F] text-[#E2E8F0] flex flex-col lg:flex-row pt-safe">
      {/* DESKTOP SIDEBAR (lg+) */}
      <aside className="hidden lg:flex lg:flex-col lg:w-64 lg:fixed lg:inset-y-0 bg-[#080D1A] border-r border-white/[0.08] z-30">
        {/* Brand */}
        <div className="px-5 py-4 border-b border-white/[0.08] flex items-center justify-between">
          <span className="font-display font-bold text-base tracking-tight text-white whitespace-nowrap shrink-0">
            AURORA V7
          </span>
          <span className="text-[10px] font-mono text-[#10B981]">PAPER</span>
        </div>

        {/* Navigation Groups */}
        <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-5" aria-label="Main Terminal Navigation">
          {NAV_GROUPS.map((group) => (
            <div key={group}>
              <div className="px-3 mb-1.5 text-[11px] font-medium text-slate-500">
                {group}
              </div>
              <div className="space-y-0.5">
                {NAV_ITEMS.filter((item) => item.group === group).map((item) => {
                  const Icon = item.icon;
                  const isActive = activeSection === item.id;
                  return (
                    <button
                      key={item.id}
                      onClick={() => handleSelectSection(item.id)}
                      className={`w-full px-3 py-2 min-h-[40px] rounded-lg text-xs font-medium flex items-center gap-2.5 transition-colors whitespace-nowrap ${
                        isActive
                          ? 'bg-[#00E5FF]/15 text-[#00E5FF]'
                          : 'text-slate-400 hover:text-white hover:bg-white/[0.04]'
                      }`}
                    >
                      <Icon className="w-4 h-4 shrink-0" />
                      <span className="truncate">{item.label}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* Sidebar Security & Owner Context */}
        <div className="p-4 border-t border-white/[0.08] text-[11px] font-mono text-slate-400 space-y-1">
          <div className="text-slate-300 truncate">
            {telegram.user?.username && telegram.initData && !authError
              ? `@${telegram.user.username}`
              : isDemo
                ? 'Demo Preview'
                : authError
                  ? 'Session Not Verified'
                  : state?.telegram_owner?.auth_method === 'signed_init_data'
                    ? 'Verified Telegram Session'
                    : state
                      ? 'Authenticated Paper Session'
                      : 'Verifying Session'}
          </div>
          <div className="text-[#10B981]">Live Execution Blocked</div>
        </div>
      </aside>

      {/* MAIN WORKSPACE COLUMN */}
      <div className="flex-1 lg:pl-64 flex flex-col min-w-0">
        {/* TOP BAR (Strict 3-Zone Contract: Title/Breadcrumb — Status — 1 Primary Action) */}
        <header className="sticky top-0 z-30 bg-[#05080F]/90 backdrop-blur-md border-b border-white/[0.08] px-4 lg:px-8 h-14 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <span className="lg:hidden font-display font-bold text-sm text-white whitespace-nowrap shrink-0">
              NEON QUANT
            </span>
            <span className="lg:hidden text-slate-600">/</span>
            <span className="text-xs sm:text-sm font-medium text-slate-200 truncate">
              {activeNavMeta.label}
            </span>
          </div>

          <div className="hidden md:flex items-center gap-2 text-xs font-mono text-slate-400 whitespace-nowrap shrink-0">
            <span
              className={`w-2 h-2 rounded-full ${
                authError ? 'bg-[#EF4444]' : fetchError || !state ? 'bg-amber-400' : isDemo ? 'bg-[#8B5CF6]' : 'bg-[#10B981]'
              }`}
            />
            <span>{authError ? 'Auth Required' : fetchError ? 'Sync Failed' : !state ? 'Connecting…' : isDemo ? 'DEMO Mode' : 'FastAPI Synced'}</span>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => fetchAll(false)}
              disabled={refreshing}
              className="px-3.5 py-2 min-h-[38px] rounded-lg bg-[#00E5FF]/15 hover:bg-[#00E5FF]/25 border border-[#00E5FF]/35 text-[#00E5FF] text-xs font-medium flex items-center gap-1.5 whitespace-nowrap shrink-0 transition-colors"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
              <span>Sync Telemetry</span>
            </button>
          </div>
        </header>

        {/* IPAD COMPACT HORIZONTAL TAB BAR (md to lg) */}
        <div className="hidden md:flex lg:hidden border-b border-white/[0.08] bg-[#080D1A] px-4 py-2 overflow-x-auto no-scrollbar gap-1.5">
          {NAV_ITEMS.map((item) => {
            const isActive = activeSection === item.id;
            return (
              <button
                key={item.id}
                onClick={() => handleSelectSection(item.id)}
                className={`px-3 py-1.5 min-h-[36px] rounded-lg text-xs font-medium whitespace-nowrap shrink-0 transition-colors ${
                  isActive
                    ? 'bg-[#00E5FF]/20 text-[#00E5FF]'
                    : 'text-slate-400 hover:text-white hover:bg-white/[0.04]'
                }`}
              >
                {item.shortLabel}
              </button>
            );
          })}
        </div>

        {/* MAIN CONTENT AREA */}
        <main className="flex-1 px-4 lg:px-8 py-6 pb-24 md:pb-10 max-w-[1440px] w-full mx-auto space-y-6">
          {/* Stale / Demo Warning Banner */}
          {state && !authError && (
            <StaleWarningStrip
              ageSec={dataAgeSec}
              isDemo={isDemo}
              onRefresh={() => fetchAll(false)}
            />
          )}

          {/* Auth Error State */}
          {authError && <AuthErrorBanner message={authError} onRetry={() => fetchAll(false)} />}

          {/* Generic Network Error State */}
          {fetchError && !authError && (
            <div className="nq-panel rounded-xl p-6 border border-[#EF4444]/35 text-xs space-y-3">
              <div className="text-sm font-semibold text-[#EF4444]">Backend Telemetry Unreachable</div>
              <p className="text-slate-300">{fetchError}</p>
              <button
                onClick={() => fetchAll(false)}
                className="px-4 py-2 rounded-lg bg-[#00E5FF]/15 border border-[#00E5FF]/35 text-[#00E5FF] font-medium"
              >
                Retry Connection
              </button>
            </div>
          )}

          {/* Loading Skeleton */}
          {loading && !state && <LoadingTerminalSkeleton />}

          {/* Active Workspace Render */}
          {state && !authError && !fetchError && (
            <Suspense fallback={<LoadingTerminalSkeleton />}>
              {activeSection === 'overview' && (
                <GlobalOverviewView
                  state={state}
                  initData={telegram.initData}
                  onNavigate={handleSelectSection}
                  onInspectPosition={setInspectedPosition}
                />
              )}
              {activeSection === 'scanner' && (
                <MarketScannerView
                  state={state}
                  onInspectOpportunityChart={handleInspectOpportunityChart}
                />
              )}
              {activeSection === 'opportunities' && (
                <OpportunitiesView
                  state={state}
                  onInspectChart={handleInspectOpportunityChart}
                />
              )}
              {activeSection === 'binance' && (
                <ExchangeWorkspaceView
                  exchange="binance"
                  state={state}
                  initData={telegram.initData}
                  onInspectPosition={setInspectedPosition}
                />
              )}
              {activeSection === 'bybit' && (
                <ExchangeWorkspaceView
                  exchange="bybit"
                  state={state}
                  initData={telegram.initData}
                  onInspectPosition={setInspectedPosition}
                />
              )}
              {activeSection === 'okx' && (
                <ExchangeWorkspaceView
                  exchange="okx"
                  state={state}
                  initData={telegram.initData}
                  onInspectPosition={setInspectedPosition}
                />
              )}
              {activeSection === 'positions' && (
                <PositionsTerminalView
                  state={state}
                  onInspectPosition={setInspectedPosition}
                />
              )}
              {activeSection === 'orders' && <PaperOrdersView state={state} />}
              {activeSection === 'analytics' && <PortfolioAnalyticsView state={state} />}
              {activeSection === 'risk' && <RiskCommandView state={state} health={health} />}
              {activeSection === 'treasury' && <TreasuryAuditView state={state} />}
              {activeSection === 'alerts' && <TelegramAlertsView state={state} telegram={telegram} />}
              {activeSection === 'system' && (
                <SystemHealthView health={health} isDemo={isDemo} onRefresh={() => fetchAll(false)} />
              )}

              <ReadOnlySecurityFooterNotice />
            </Suspense>
          )}
        </main>
      </div>

      {/* MOBILE / TELEGRAM MINI APP FIXED BOTTOM NAVIGATION (< md, <= 15% sticky cap, pb-safe) */}
      <nav
        aria-label="Mobile Bottom Navigation"
        className="md:hidden fixed bottom-0 left-0 right-0 z-40 bg-[#080D1A]/95 backdrop-blur-md border-t border-white/[0.1] pb-safe"
      >
        <div className="grid grid-cols-4 h-14 items-center">
          {[
            { id: 'overview' as NavSectionId, label: 'Overview', icon: LayoutDashboard },
            { id: 'scanner' as NavSectionId, label: 'Scanner', icon: Radar },
            { id: 'positions' as NavSectionId, label: 'Positions', icon: Briefcase }
          ].map((tab) => {
            const Icon = tab.icon;
            const isActive = activeSection === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => handleSelectSection(tab.id)}
                className={`min-h-[44px] flex flex-col items-center justify-center gap-0.5 text-[10px] font-medium transition-colors ${
                  isActive ? 'text-[#00E5FF]' : 'text-slate-400'
                }`}
              >
                <Icon className="w-4 h-4" />
                <span>{tab.label}</span>
              </button>
            );
          })}

          <button
            onClick={() => setMobileMenuOpen(true)}
            className={`min-h-[44px] flex flex-col items-center justify-center gap-0.5 text-[10px] font-medium transition-colors ${
              mobileMenuOpen ? 'text-[#00E5FF]' : 'text-slate-400'
            }`}
          >
            <Menu className="w-4 h-4" />
            <span>Workspaces</span>
          </button>
        </div>
      </nav>

      {/* MOBILE ALL-12 WORKSPACES BOTTOM SHEET */}
      {mobileMenuOpen && (
        <div
          className="md:hidden fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-end"
          onClick={() => setMobileMenuOpen(false)}
        >
          <div
            className="w-full bg-[#0B1120] border-t border-white/[0.12] rounded-t-2xl p-4 pb-safe max-h-[82vh] overflow-y-auto space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between pb-2 border-b border-white/[0.08]">
              <span className="font-display font-semibold text-sm text-white">All Terminal Workspaces</span>
              <button
                onClick={() => setMobileMenuOpen(false)}
                className="p-2 min-h-[44px] min-w-[44px] flex items-center justify-center text-slate-400 hover:text-white"
                aria-label="Close workspace drawer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {NAV_GROUPS.map((group) => (
              <div key={group} className="space-y-1">
                <div className="text-[11px] font-medium text-slate-500 px-1">{group}</div>
                <div className="grid grid-cols-2 gap-2">
                  {NAV_ITEMS.filter((i) => i.group === group).map((item) => {
                    const Icon = item.icon;
                    const isActive = activeSection === item.id;
                    return (
                      <button
                        key={item.id}
                        onClick={() => handleSelectSection(item.id)}
                        className={`min-h-[44px] px-3 py-2 rounded-xl border text-left text-xs font-medium flex items-center justify-between transition-colors ${
                          isActive
                            ? 'bg-[#00E5FF]/15 border-[#00E5FF]/40 text-[#00E5FF]'
                            : 'bg-[#05080F] border-white/[0.08] text-slate-300'
                        }`}
                      >
                        <span className="flex items-center gap-2 truncate">
                          <Icon className="w-3.5 h-3.5 shrink-0" />
                          <span className="truncate">{item.shortLabel}</span>
                        </span>
                        <ChevronRight className="w-3 h-3 text-slate-500 shrink-0" />
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* POSITION DETAIL DRAWER */}
      <Suspense fallback={null}>
        <PositionDetailDrawer
          position={inspectedPosition}
          onClose={() => setInspectedPosition(null)}
          onOpenChart={(pos) => {
            setInspectedPosition(null);
            setInspectedChartOpp({
              exchange: pos.exchange,
              marketType: 'futures',
              symbol: pos.symbol,
              timeframe: '15m'
            });
          }}
        />
      </Suspense>

      {/* QUICK OPPORTUNITY / POSITION CHART MODAL */}
      {inspectedChartOpp && (
        <div
          className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-3 md:p-6"
          onClick={() => setInspectedChartOpp(null)}
        >
          <div
            className="w-full max-w-5xl bg-[#0B1120] border border-white/[0.15] rounded-2xl overflow-hidden shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-4 py-3 border-b border-white/[0.08] flex items-center justify-between">
              <div className="text-sm font-display font-semibold text-white">
                {inspectedChartOpp.exchange.toUpperCase()} · {inspectedChartOpp.symbol} ({inspectedChartOpp.marketType.toUpperCase()})
              </div>
              <button
                onClick={() => setInspectedChartOpp(null)}
                className="p-2 min-h-[38px] min-w-[38px] flex items-center justify-center rounded-lg text-slate-400 hover:text-white hover:bg-white/5"
                aria-label="Close chart modal"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="p-3">
              <Suspense fallback={<LoadingTerminalSkeleton />}>
                <ProMarketChart
                  exchange={inspectedChartOpp.exchange}
                  marketType={inspectedChartOpp.marketType}
                  symbol={inspectedChartOpp.symbol}
                  timeframe={inspectedChartOpp.timeframe}
                  onExchangeChange={(ex) => setInspectedChartOpp({ ...inspectedChartOpp, exchange: ex })}
                  onMarketTypeChange={(mt) => setInspectedChartOpp({ ...inspectedChartOpp, marketType: mt })}
                  onSymbolChange={(sym) => setInspectedChartOpp({ ...inspectedChartOpp, symbol: sym })}
                  onTimeframeChange={(tf) => setInspectedChartOpp({ ...inspectedChartOpp, timeframe: tf })}
                  activePosition={
                    state?.futures_positions.find(
                      (p) =>
                        p.status === 'open' &&
                        p.exchange === inspectedChartOpp.exchange &&
                        p.symbol === inspectedChartOpp.symbol
                    ) || null
                  }
                  initData={telegram.initData}
                />
              </Suspense>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
