import { DiagnosticsPanel } from './components/workspaces/DiagnosticsPanel';
import React, { useState, useEffect, useCallback, useRef, Suspense, lazy } from 'react';
import {
  LayoutDashboard,
  Cpu,
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
const SolanaResearchView = lazy(() =>
  import('./components/workspaces/SolanaResearchView').then((m) => ({ default: m.SolanaResearchView }))
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
  { id: 'solana', label: 'Solana Research', shortLabel: 'Solana', group: 'Core Terminal', icon: Cpu },
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

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setMobileMenuOpen(false);
        setInspectedPosition(null);
        setInspectedChartOpp(null);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  const handleSelectSection = (id: NavSectionId) => {
    telegram.hapticSelection();
    setActiveSection(id);
    setMobileMenuOpen(false);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const handleTabKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>, currentIndex: number) => {
    let nextIndex: number | null = null;
    if (e.key === 'ArrowRight') nextIndex = (currentIndex + 1) % NAV_ITEMS.length;
    else if (e.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + NAV_ITEMS.length) % NAV_ITEMS.length;
    else if (e.key === 'Home') nextIndex = 0;
    else if (e.key === 'End') nextIndex = NAV_ITEMS.length - 1;
    if (nextIndex !== null) {
      e.preventDefault();
      handleSelectSection(NAV_ITEMS[nextIndex].id);
      const container = e.currentTarget.parentElement;
      const buttons = container?.querySelectorAll<HTMLButtonElement>('button[role="tab"]');
      buttons?.[nextIndex]?.focus();
    }
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
    <div className="min-h-screen bg-[#080305] text-[#ffe3e5] flex flex-col lg:flex-row pt-safe">
      {/* DESKTOP SIDEBAR (lg+) */}
      <aside className="hidden lg:flex lg:flex-col lg:w-64 lg:fixed lg:inset-y-0 bg-[#120508] border-r border-[#ff3555]/25 z-30">
        {/* Brand */}
        <div className="px-5 py-4 border-b border-[#ff3555]/20 flex items-center justify-between gap-2">
          <span className="font-display font-bold text-sm tracking-wide text-[#ffe3e5] whitespace-nowrap shrink-0">
            VIOLET // SCARLET
          </span>
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[#10b981]/15 border border-[#10b981]/35 text-[#10b981]">
            PAPER
          </span>
        </div>

        {/* Navigation Groups */}
        <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-5" aria-label="Main Terminal Navigation">
          {NAV_GROUPS.map((group) => (
            <div key={group}>
              <div className="px-3 mb-1.5 text-[11px] font-mono uppercase tracking-wider text-[#b8868e]">
                {group}
              </div>
              <div className="space-y-0.5">
                {NAV_ITEMS.filter((item) => item.group === group).map((item) => {
                  const Icon = item.icon;
                  const isActive = activeSection === item.id;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => handleSelectSection(item.id)}
                      aria-current={isActive ? 'page' : undefined}
                      className={`w-full px-3 py-2 min-h-[40px] rounded-lg text-xs font-medium flex items-center gap-2.5 transition-colors whitespace-nowrap ${
                        isActive
                          ? 'bg-[#ff3555]/20 text-[#ffe3e5] border border-[#ff3555]/45 shadow-[0_0_16px_rgba(255,53,85,0.18)]'
                          : 'text-[#d4a5ac] hover:text-[#ffe3e5] hover:bg-[#ff3555]/10 border border-transparent'
                      }`}
                    >
                      <Icon className={`w-4 h-4 shrink-0 ${isActive ? 'text-[#ff6375]' : ''}`} />
                      <span className="truncate">{item.label}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* Sidebar Security & Owner Context */}
        <div className="p-4 border-t border-[#ff3555]/20 text-[11px] font-mono text-[#d4a5ac] space-y-1">
          <div className="text-[#ffe3e5] truncate">
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
          <div className="text-[#10b981]">Live Execution Blocked</div>
        </div>
      </aside>

      {/* MAIN WORKSPACE COLUMN */}
      <div className="flex-1 lg:pl-64 flex flex-col min-w-0">
        {/* TOP BAR (Strict 3-Zone Contract: Title/Breadcrumb — Status — 1 Primary Action) */}
        <header className="sticky top-0 z-30 bg-[#080305]/92 backdrop-blur-md border-b border-[#ff3555]/25 px-4 lg:px-8 h-14 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <span className="lg:hidden font-display font-bold text-sm text-[#ffe3e5] whitespace-nowrap shrink-0">
              VIOLET // SCARLET
            </span>
            <span className="lg:hidden text-[#ff3555]/60">/</span>
            <span className="text-xs sm:text-sm font-medium text-[#ffe3e5] truncate">
              {activeNavMeta.label}
            </span>
          </div>

          <div className="hidden md:flex items-center gap-2 text-xs font-mono text-[#d4a5ac] whitespace-nowrap shrink-0">
            <span
              className={`w-2 h-2 rounded-full ${
                authError ? 'bg-[#ef4444]' : fetchError || !state ? 'bg-[#ffb16c]' : isDemo ? 'bg-[#ff6375]' : 'bg-[#10b981]'
              }`}
            />
            <span>{authError ? 'Auth Required' : fetchError ? 'Sync Failed' : !state ? 'Connecting…' : isDemo ? 'DEMO Mode' : 'FastAPI Synced'}</span>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={() => fetchAll(false)}
              disabled={refreshing}
              className="px-3.5 py-2 min-h-[38px] rounded-lg bg-[#ff3555]/15 hover:bg-[#ff3555]/25 border border-[#ff3555]/45 text-[#ffe3e5] text-xs font-medium flex items-center gap-1.5 whitespace-nowrap shrink-0 transition-colors"
            >
              <RefreshCw className={`w-3.5 h-3.5 text-[#ff6375] ${refreshing ? 'animate-spin' : ''}`} />
              <span>Sync Telemetry</span>
            </button>
          </div>
        </header>

        {/* IPAD COMPACT HORIZONTAL TAB BAR (md to lg) */}
        <div
          role="tablist"
          aria-label="Workspace Tabs"
          className="hidden md:flex lg:hidden border-b border-[#ff3555]/20 bg-[#120508] px-4 py-2 overflow-x-auto no-scrollbar gap-1.5"
        >
          {NAV_ITEMS.map((item, idx) => {
            const isActive = activeSection === item.id;
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => handleSelectSection(item.id)}
                onKeyDown={(e) => handleTabKeyDown(e, idx)}
                className={`px-3 py-1.5 min-h-[36px] rounded-lg text-xs font-medium whitespace-nowrap shrink-0 transition-colors ${
                  isActive
                    ? 'bg-[#ff3555]/25 text-[#ffe3e5] border border-[#ff3555]/45'
                    : 'text-[#d4a5ac] hover:text-[#ffe3e5] hover:bg-[#ff3555]/10 border border-transparent'
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
          {state && !authError && activeSection !== 'solana' && (
            <StaleWarningStrip
              ageSec={dataAgeSec}
              isDemo={isDemo}
              onRefresh={() => fetchAll(false)}
            />
          )}

          {/* Auth Error State */}
          {authError && activeSection !== 'solana' && (
            <AuthErrorBanner message={authError} onRetry={() => fetchAll(false)} />
          )}

          {/* Generic Network Error State */}
          {fetchError && !authError && !state && activeSection !== 'solana' && (
            <div className="nq-panel rounded-xl p-6 border border-[#ef4444]/45 text-xs space-y-3">
              <div className="text-sm font-semibold text-[#ff6375]">Backend Telemetry Unreachable</div>
              <p className="text-[#f3c6cc]">{fetchError}</p>
              <button
                type="button"
                onClick={() => fetchAll(false)}
                className="px-4 py-2 rounded-lg bg-[#ff3555]/20 border border-[#ff3555]/45 text-[#ffe3e5] font-medium"
              >
                Retry Connection
              </button>
            </div>
          )}

          {fetchError && !authError && state && activeSection !== 'solana' && (
            <div className="mb-4 px-4 py-2.5 rounded-xl bg-[#ffb16c]/10 border border-[#ffb16c]/35 text-xs text-[#ffd7b3] flex items-center justify-between gap-3">
              <span>Telemetry refresh warning: {fetchError}. Showing last synchronized snapshot.</span>
              <button
                type="button"
                onClick={() => fetchAll(false)}
                className="px-2.5 py-1 rounded bg-[#ffb16c]/20 hover:bg-[#ffb16c]/30 text-[#ffe3e5] whitespace-nowrap shrink-0"
              >
                Retry
              </button>
            </div>
          )}

          {/* Independent Solana Research Workspace */}
          {activeSection === 'solana' && (
            <Suspense fallback={<LoadingTerminalSkeleton />}>
              <SolanaResearchView initData={telegram.initData} />
              <ReadOnlySecurityFooterNotice />
            </Suspense>
          )}

          {/* Loading Skeleton */}
          {activeSection !== 'solana' && loading && !state && <LoadingTerminalSkeleton />}

          {/* Active Crypto Workspace Render */}
          {activeSection !== 'solana' && state && !authError && (
            <>
              {['system', 'orders', 'analytics', 'scanner', 'opportunities', 'risk'].includes(activeSection) && (
                <DiagnosticsPanel section={activeSection} initData={telegram.initData} />
              )}
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
                  <SystemHealthView health={health} isDemo={isDemo} initData={telegram.initData} onRefresh={() => fetchAll(false)} />
                )}

                <ReadOnlySecurityFooterNotice />
              </Suspense>
            </>
          )}
        </main>
      </div>

      {/* MOBILE / TELEGRAM MINI APP FIXED BOTTOM NAVIGATION (< md, <= 15% sticky cap, pb-safe) */}
      <nav
        aria-label="Mobile Bottom Navigation"
        className="md:hidden fixed bottom-0 left-0 right-0 z-40 bg-[#120508]/95 backdrop-blur-md border-t border-[#ff3555]/25 pb-safe"
      >
        <div className="grid grid-cols-5 h-14 items-center">
          {[
            { id: 'overview' as NavSectionId, label: 'Overview', icon: LayoutDashboard },
            { id: 'solana' as NavSectionId, label: 'Solana', icon: Cpu },
            { id: 'scanner' as NavSectionId, label: 'Scanner', icon: Radar },
            { id: 'positions' as NavSectionId, label: 'Positions', icon: Briefcase }
          ].map((tab) => {
            const Icon = tab.icon;
            const isActive = activeSection === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => handleSelectSection(tab.id)}
                aria-current={isActive ? 'page' : undefined}
                className={`min-h-[44px] flex flex-col items-center justify-center gap-0.5 text-[10px] font-medium transition-colors ${
                  isActive ? 'text-[#ff6375]' : 'text-[#d4a5ac]'
                }`}
              >
                <Icon className="w-4 h-4" />
                <span>{tab.label}</span>
              </button>
            );
          })}

          <button
            type="button"
            onClick={() => setMobileMenuOpen(true)}
            aria-expanded={mobileMenuOpen}
            className={`min-h-[44px] flex flex-col items-center justify-center gap-0.5 text-[10px] font-medium transition-colors ${
              mobileMenuOpen ? 'text-[#ff6375]' : 'text-[#d4a5ac]'
            }`}
          >
            <Menu className="w-4 h-4" />
            <span>More</span>
          </button>
        </div>
      </nav>

      {/* MOBILE ALL WORKSPACES BOTTOM SHEET */}
      {mobileMenuOpen && (
        <div
          className="md:hidden fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-end"
          onClick={() => setMobileMenuOpen(false)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label="All Terminal Workspaces"
            className="w-full bg-[#120508] border-t border-[#ff3555]/35 rounded-t-2xl p-4 pb-safe max-h-[82vh] overflow-y-auto space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between pb-2 border-b border-[#ff3555]/20">
              <span className="font-display font-semibold text-sm text-[#ffe3e5]">All Terminal Workspaces</span>
              <button
                type="button"
                onClick={() => setMobileMenuOpen(false)}
                className="p-2 min-h-[44px] min-w-[44px] flex items-center justify-center text-[#d4a5ac] hover:text-[#ffe3e5]"
                aria-label="Close workspace drawer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {NAV_GROUPS.map((group) => (
              <div key={group} className="space-y-1">
                <div className="text-[11px] font-mono uppercase text-[#b8868e] px-1">{group}</div>
                <div className="grid grid-cols-2 gap-2">
                  {NAV_ITEMS.filter((i) => i.group === group).map((item) => {
                    const Icon = item.icon;
                    const isActive = activeSection === item.id;
                    return (
                      <button
                        key={item.id}
                        type="button"
                        onClick={() => handleSelectSection(item.id)}
                        aria-current={isActive ? 'page' : undefined}
                        className={`min-h-[44px] px-3 py-2 rounded-xl border text-left text-xs font-medium flex items-center justify-between transition-colors ${
                          isActive
                            ? 'bg-[#ff3555]/20 border-[#ff3555]/50 text-[#ffe3e5]'
                            : 'bg-[#080305] border-[#ff3555]/20 text-[#d4a5ac]'
                        }`}
                      >
                        <span className="flex items-center gap-2 truncate">
                          <Icon className={`w-3.5 h-3.5 shrink-0 ${isActive ? 'text-[#ff6375]' : ''}`} />
                          <span className="truncate">{item.shortLabel}</span>
                        </span>
                        <ChevronRight className="w-3 h-3 text-[#b8868e] shrink-0" />
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
            role="dialog"
            aria-modal="true"
            aria-label="Market Chart Inspector"
            className="w-full max-w-5xl bg-[#120508] border border-[#ff3555]/35 rounded-2xl overflow-hidden shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-4 py-3 border-b border-[#ff3555]/20 flex items-center justify-between">
              <div className="text-sm font-display font-semibold text-[#ffe3e5]">
                {inspectedChartOpp.exchange.toUpperCase()} · {inspectedChartOpp.symbol} ({inspectedChartOpp.marketType.toUpperCase()})
              </div>
              <button
                type="button"
                onClick={() => setInspectedChartOpp(null)}
                className="p-2 min-h-[38px] min-w-[38px] flex items-center justify-center rounded-lg text-[#d4a5ac] hover:text-[#ffe3e5] hover:bg-[#ff3555]/10"
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
