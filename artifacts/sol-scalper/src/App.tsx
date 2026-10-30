import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query';
import { ErrorBoundary } from '@/components/error-boundary';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import NotFound from '@/pages/not-found';
import {
  Route,
  Switch,
  useLocation,
  Router as WouterRouter,
} from 'wouter';
import {
  Activity, Check, Gauge, ShieldCheck, SlidersHorizontal, Waves,
} from 'lucide-react';
import {
  getGetBotDashboardQueryKey, getGetBotSettingsQueryKey,
  getGetBotSignalsQueryKey, getGetBotTradesQueryKey, getHealthCheckQueryKey,
  useGetBotDashboard, useGetBotSettings, useGetBotSignals, useGetBotTrades,
  useHealthCheck, useSetBotRunning, useUpdateBotSettings,
} from '@workspace/api-client-react';
import type { BotSettingsInput, IndicatorSnapshot, Signal, Trade } from '@workspace/api-client-react';

const queryClient = new QueryClient();

function Home() {
  const client = useQueryClient();
  const dashboard = useGetBotDashboard({ query: { queryKey: getGetBotDashboardQueryKey(), refetchInterval: 5000, retry: 1 } });
  const health = useHealthCheck({ query: { queryKey: getHealthCheckQueryKey(), refetchInterval: 30000 } });
  const signalsQuery = useGetBotSignals({ query: { queryKey: getGetBotSignalsQueryKey(), refetchInterval: 10000 } });
  const tradesQuery = useGetBotTrades({ query: { queryKey: getGetBotTradesQueryKey(), refetchInterval: 15000 } });
  const settingsQuery = useGetBotSettings({ query: { queryKey: getGetBotSettingsQueryKey(), refetchInterval: 30000 } });
  const updateSettings = useUpdateBotSettings();
  const setRunning = useSetBotRunning();
  const [tab, setTab] = useState<'signals' | 'trades'>('signals');
  const [saved, setSaved] = useState(false);
  const [form, setForm] = useState<BotSettingsInput | null>(null);
  const data = dashboard.data;
  const settings = settingsQuery.data ?? data?.settings;
  const signals = signalsQuery.data ?? [];
  const trades = tradesQuery.data ?? [];

  useEffect(() => {
    if (settings && !form) {
      setForm({
        startingBalanceUsdc: settings.startingBalanceUsdc,
        positionSizePercent: settings.positionSizePercent,
        stopLossPercent: settings.stopLossPercent,
        takeProfitPercent: settings.takeProfitPercent,
        maxDailyLossUsdc: settings.maxDailyLossUsdc,
        feePercent: settings.feePercent,
        slippagePercent: settings.slippagePercent,
      });
    }
  }, [settings, form]);

  const invalidateDashboard = () => {
    void client.invalidateQueries({ queryKey: getGetBotDashboardQueryKey() });
    void client.invalidateQueries({ queryKey: getGetBotSettingsQueryKey() });
    void client.invalidateQueries({ queryKey: getGetBotSignalsQueryKey() });
    void client.invalidateQueries({ queryKey: getGetBotTradesQueryKey() });
  };

  const patchSetting = (key: keyof BotSettingsInput, value: string) => {
    setSaved(false);
    setForm((current) => current ? { ...current, [key]: Number(value) } : current);
  };

  const saveSettings = () => {
    if (!form) return;
    updateSettings.mutate({ data: form }, {
      onSuccess: () => {
        setSaved(true);
        invalidateDashboard();
      },
    });
  };

  const toggleSimulator = () => {
    if (!data) return;
    setRunning.mutate({ data: { running: !data.status.running } }, { onSuccess: invalidateDashboard });
  };

  const lastUpdated = data?.market.updatedAt ? formatTime(data.market.updatedAt) : 'Waiting for feed';

  if (dashboard.isLoading) return <div className="workstation"><Sidebar /><main className="main"><Topbar health={health.data?.status === 'ok'} /><div className="page-head"><div><div className="eyebrow">Market operations / 01</div><h1>Signal desk</h1></div></div><div className="loading-skeleton" /><div style={{ height: 16 }} /><div className="loading-skeleton" /></main></div>;
  if (dashboard.isError || !data) return <div className="workstation"><Sidebar /><main className="main"><Topbar health={false} /><div className="load-error"><strong>Market feed unavailable</strong><p>Dashboard data could not be loaded. Check the API connection and retry.</p><button className="btn-primary" data-testid="button-retry-dashboard" onClick={() => void dashboard.refetch()}>Retry connection</button></div></main></div>;

  const currentSignal = data.signal;
  return (
    <div className="workstation" id="top">
      <Sidebar />
      <main className="main">
        <Topbar health={health.data?.status === 'ok'} />
        <div className="page-head">
          <div>
            <div className="eyebrow">Market operations / 01</div>
            <h1>Signal desk</h1>
            <div className="page-sub">SOL/USDC · short-timeframe read · updated {lastUpdated}</div>
          </div>
          <div className="paper-pill" data-testid="status-paper-only"><ShieldCheck size={13} /> PAPER SIMULATION ONLY</div>
        </div>
        <div className="layout">
          <div className="stack">
            <section className="panel quote-card" id="section-market-feed" data-testid="card-market-quote">
              <div className="quote-top">
                <div><div className="pair">{data.market.symbol}</div><div className="source">Public market data · {data.market.source}</div></div>
                <div className="quote-change"><span className="live-dot" />{data.market.stale ? 'STALE FEED' : 'LIVE QUOTE'}</div>
              </div>
              <div className="price mono" data-testid="text-market-price">{formatPrice(data.market.price)}</div>
              <div className="quote-caption">USDC per SOL <span style={{ margin: '0 6px', color: '#c1c5bc' }}>·</span> indicative market price</div>
              <PriceChart candles={data.market.candles1m} />
            </section>
            <div className="section-row">
              {(['1m', '5m'] as const).map((timeframe) => {
                const indicator = data.indicators.find((item) => item.timeframe === timeframe);
                return <IndicatorCard key={timeframe} timeframe={timeframe} indicator={indicator} />;
              })}
            </div>
            <section className="panel activity" id="section-paper-ledger" data-testid="section-recorded-activity">
              <div className="panel-head">
                <div className="activity-tabs">
                  <button className={`activity-tab ${tab === 'signals' ? 'selected' : ''}`} onClick={() => setTab('signals')} data-testid="tab-recorded-signals">Signals <span>{signals.length}</span></button>
                  <button className={`activity-tab ${tab === 'trades' ? 'selected' : ''}`} onClick={() => setTab('trades')} data-testid="tab-paper-trades">Paper trades <span>{trades.length}</span></button>
                </div>
                <span className="panel-title">Recent records</span>
              </div>
              {tab === 'signals'
                ? <SignalTable signals={signals} loading={signalsQuery.isLoading} error={signalsQuery.isError} retry={() => void signalsQuery.refetch()} />
                : <TradeTable trades={trades} loading={tradesQuery.isLoading} error={tradesQuery.isError} retry={() => void tradesQuery.refetch()} />}
            </section>
          </div>
          <div className="stack">
            <section className="panel signal-card" data-testid="card-current-signal">
              <div className="panel-head"><span className="panel-title">Current signal</span><span className="panel-title">{currentSignal.timeframe} / close</span></div>
              <div className="signal-main">
                <div className={`signal-banner ${currentSignal.direction}`}>
                  <div className="signal-word">{currentSignal.direction}</div>
                  <div className="confidence">{currentSignal.confidence.toFixed(1)}%<small>confidence</small></div>
                </div>
                <p className="signal-reason" data-testid="text-signal-reason">{currentSignal.reason}</p>
                <div className="signal-meta"><span>{formatPrice(currentSignal.price)} USDC</span><span>{formatTime(currentSignal.timestamp)}</span></div>
              </div>
            </section>
            <section className="panel status-panel" data-testid="card-paper-status">
              <div className="run-row">
                <div><div className="run-label">Paper simulator</div><div className="run-state"><span className={data.status.running ? 'live-dot' : ''} />{data.status.running ? 'RUNNING' : 'PAUSED'}</div></div>
                <button className={`switch ${data.status.running ? 'on' : ''}`} aria-label={data.status.running ? 'Pause paper simulator' : 'Start paper simulator'} data-testid="button-toggle-simulator" onClick={toggleSimulator} disabled={setRunning.isPending || data.status.dailyLossLimitReached}><span /></button>
              </div>
              <div className="status-grid">
                <StatusMetric label="Paper equity" value={formatMoney(data.status.equityUsdc)} />
                <StatusMetric label="Available cash" value={formatMoney(data.status.cashUsdc)} />
                <StatusMetric label="Realized P&L" value={formatMoney(data.status.realizedPnlUsdc)} pnl={data.status.realizedPnlUsdc} />
                <StatusMetric label="Open P&L" value={formatMoney(data.status.unrealizedPnlUsdc)} pnl={data.status.unrealizedPnlUsdc} />
                <StatusMetric label="Today" value={formatMoney(data.status.dailyPnlUsdc)} pnl={data.status.dailyPnlUsdc} />
                <StatusMetric label="Trades / win rate" value={`${data.todayTradeCount} / ${data.winRatePercent.toFixed(1)}%`} />
              </div>
              {data.status.dailyLossLimitReached
                ? <div className="risk-banner" data-testid="status-daily-loss-guard"><ShieldCheck size={14} />Daily loss guard reached. The simulator is blocked from starting.</div>
                : <div className="risk-banner safe" data-testid="status-daily-loss-guard"><ShieldCheck size={14} />Daily loss guard active · limit {formatMoney(settings?.maxDailyLossUsdc ?? 0)}</div>}
              {data.status.openPosition && <div className="position-box" data-testid="card-open-paper-position">
                <div className="position-head"><span>Open paper long</span><span className={data.status.openPosition.unrealizedPnlUsdc >= 0 ? 'open' : 'closed'}>{formatMoney(data.status.openPosition.unrealizedPnlUsdc)}</span></div>
                <div className="position-nums"><span data-testid="text-open-position-remaining">{data.status.openPosition.quantity.toFixed(4)} SOL remaining</span><span>Entry {formatPrice(data.status.openPosition.entryPrice)}</span></div>
                {data.status.openPosition.unfilledEntryQuantity > 0 && <div className="position-nums fill-detail"><span data-testid="text-open-position-unfilled">{data.status.openPosition.unfilledEntryQuantity.toFixed(4)} SOL unfilled</span><span>of {data.status.openPosition.requestedQuantity.toFixed(4)} SOL requested</span></div>}
                <div className="position-nums"><span>Stop {formatPrice(data.status.openPosition.stopLossPrice)}</span><span>Target {formatPrice(data.status.openPosition.takeProfitPrice)}</span></div>
              </div>}
              {setRunning.isError && <div className="risk-banner" role="alert">Control change failed. Try again.</div>}
            </section>
            <section className="panel settings" data-testid="card-risk-controls">
              <div className="panel-head" style={{ margin: '-16px -17px 13px' }}><span className="panel-title">Paper risk controls</span><SlidersHorizontal size={14} color="#7b8e85" /></div>
              <p className="settings-intro">Adjust the simulator assumptions. These limits apply to paper positions only.</p>
              {form && <div className="field-grid">
                <SettingField label="Starting balance" suffix="USDC" value={form.startingBalanceUsdc} min={100} max={10000000} step={100} onChange={(v) => patchSetting('startingBalanceUsdc', v)} testId="input-starting-balance" />
                <SettingField label="Position size" suffix="%" value={form.positionSizePercent} min={1} max={100} step={0.5} onChange={(v) => patchSetting('positionSizePercent', v)} testId="input-position-size" />
                <SettingField label="Stop loss" suffix="%" value={form.stopLossPercent} min={0.1} max={20} step={0.1} onChange={(v) => patchSetting('stopLossPercent', v)} testId="input-stop-loss" />
                <SettingField label="Take profit" suffix="%" value={form.takeProfitPercent} min={0.1} max={50} step={0.1} onChange={(v) => patchSetting('takeProfitPercent', v)} testId="input-take-profit" />
                <SettingField label="Max daily loss" suffix="USDC" value={form.maxDailyLossUsdc} min={1} max={10000000} step={10} onChange={(v) => patchSetting('maxDailyLossUsdc', v)} testId="input-max-daily-loss" />
                <SettingField label="Fee assumption" suffix="%" value={form.feePercent} min={0} max={5} step={0.01} onChange={(v) => patchSetting('feePercent', v)} testId="input-fee-percent" />
                <SettingField label="Slippage assumption" suffix="%" value={form.slippagePercent} min={0} max={5} step={0.01} onChange={(v) => patchSetting('slippagePercent', v)} testId="input-slippage-percent" />
              </div>}
              <div className="save-row">
                {saved ? <span className="toast-inline"><Check size={12} style={{ verticalAlign: 'middle' }} /> Saved</span> : <span className="save-note">Risk settings are simulator assumptions.</span>}
                <button className="btn-primary" data-testid="button-save-settings" onClick={saveSettings} disabled={!form || updateSettings.isPending}>{updateSettings.isPending ? 'Saving…' : 'Save controls'}</button>
              </div>
              {updateSettings.isError && <div className="risk-banner" role="alert">Could not save controls. Review values and retry.</div>}
            </section>
          </div>
        </div>
      </main>
    </div>
  );
}

function Sidebar() {
  return <aside className="sidebar">
    <div className="brand"><div className="brand-mark"><Activity size={17} /></div><div className="brand-copy"><div className="brand-name">SOL Scalper</div><div className="brand-sub">Paper desk</div></div></div>
    <div className="nav-label">Workspace</div>
    <nav aria-label="Workspace"><a href="#top" className="nav-item active" data-testid="nav-signal-desk"><Gauge size={15} /><span>Signal desk</span></a><a href="#section-paper-ledger" className="nav-item" data-testid="nav-paper-ledger"><Activity size={15} /><span>Paper ledger</span></a><a href="#section-market-feed" className="nav-item" data-testid="nav-market-feed"><Waves size={15} /><span>Market feed</span></a></nav>
    <div className="side-note"><div className="side-note-title"><ShieldCheck size={13} /> NO WALLET EXPOSURE</div><p>Signals and positions are simulated. No orders reach an exchange.</p></div>
    <div className="side-foot">BUILD 0.1.0 · SOL / USDC</div>
  </aside>;
}

function Topbar({ health }: { health: boolean }) {
  return <div className="topbar"><div className="crumb">SOL Scalper <span style={{ margin: '0 7px', color: '#b6bdb6' }}>/</span> <strong>Signal desk</strong></div><div className="top-right"><div className="health"><span className="health-dot" style={{ background: health ? '#5b9f7d' : '#c78a61' }} /><span>{health ? 'API operational' : 'API status unknown'}</span></div><span className="mono">PUBLIC DATA</span></div></div>;
}

function PriceChart({ candles }: { candles: { timestamp: string; open: number; high: number; low: number; close: number; volume: number }[] }) {
  const chart = useMemo(() => {
    if (!candles.length) return { bars: [], min: 0, max: 1 };
    const min = Math.min(...candles.map((c) => c.low));
    const max = Math.max(...candles.map((c) => c.high));
    const span = max - min || max * 0.002 || 1;
    const bars = candles.slice(-48).map((c, index, arr) => {
      const x = 10 + (index / Math.max(arr.length - 1, 1)) * 92;
      const y = (v: number) => 154 - ((v - (min - span * 0.12)) / (span * 1.24)) * 137;
      return { x, openY: y(c.open), closeY: y(c.close), highY: y(c.high), lowY: y(c.low), rising: c.close >= c.open };
    });
    return { bars, min: min - span * .12, max: max + span * .12 };
  }, [candles]);
  const first = chart.bars[0];
  const last = chart.bars[chart.bars.length - 1];
  return <div className="chart-wrap" data-testid="chart-sol-candles">
    <svg viewBox="0 0 110 170" preserveAspectRatio="none" role="img" aria-label="Recent one minute SOL price candles">
      {[24, 64, 104, 144].map((y) => <line key={y} className="chart-grid" x1="1" x2="107" y1={y} y2={y} />)}
      {chart.bars.map((bar, i) => <g key={`${i}-${bar.x}`}>
        <line x1={bar.x} x2={bar.x} y1={bar.highY} y2={bar.lowY} stroke={bar.rising ? '#4d8e6b' : '#b86b5e'} strokeWidth=".65" />
        <rect x={bar.x - 0.55} y={Math.min(bar.openY, bar.closeY)} width="1.15" height={Math.max(Math.abs(bar.openY - bar.closeY), 1)} rx=".15" fill={bar.rising ? '#6aa782' : '#c47a69'} />
      </g>)}
      {first && last && <line x1={first.x} y1={first.closeY} x2={last.x} y2={last.closeY} stroke="#416f5a" strokeWidth=".7" strokeDasharray="1 1" opacity=".5" />}
    </svg>
    <div className="time-labels"><span>{candles.length ? formatTime(candles[Math.max(candles.length - 48, 0)].timestamp) : 'No candles yet'}</span><span>1m candles</span><span>{candles.length ? formatTime(candles[candles.length - 1].timestamp) : 'Waiting'}</span></div>
  </div>;
}

function IndicatorCard({ timeframe, indicator }: { timeframe: '1m' | '5m'; indicator?: IndicatorSnapshot }) {
  return <section className="panel indicator" data-testid={`card-indicator-${timeframe}`}>
    <div className="indicator-head"><span className="tf">{timeframe} indicators</span>{indicator && <span className={`trend ${indicator.trend}`} data-testid={`status-trend-${timeframe}`}>{indicator.trend}</span>}</div>
    {indicator ? <div className="metric-grid">
      <Metric label="RSI" value={indicator.rsi.toFixed(1)} />
      <Metric label="Volume ratio" value={`${indicator.volumeRatio.toFixed(2)}×`} />
      <Metric label="EMA 9" value={formatPrice(indicator.ema9)} />
      <Metric label="EMA 21" value={formatPrice(indicator.ema21)} />
      <Metric label="Close" value={formatPrice(indicator.close)} />
      <Metric label="Candle" value={formatTime(indicator.candleTime)} />
    </div> : <div className="empty">Indicator snapshot not available.</div>}
  </section>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><div className="metric-label">{label}</div><div className="metric-value mono" data-testid={`metric-${label.toLowerCase().replaceAll(' ', '-')}`}>{value}</div></div>;
}

function StatusMetric({ label, value, pnl }: { label: string; value: string; pnl?: number }) {
  const className = pnl === undefined ? '' : pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : '';
  return <div><div className="metric-label">{label}</div><div className={`status-value mono ${className}`} data-testid={`status-${label.toLowerCase().replaceAll(/[^a-z]+/g, '-')}`}>{value}</div></div>;
}

function SettingField({ label, suffix, value, min, max, step, onChange, testId }: { label: string; suffix: string; value: number; min: number; max: number; step: number; onChange: (value: string) => void; testId: string }) {
  return <div className="field"><label htmlFor={testId}>{label}</label><div className="field-wrap"><input id={testId} data-testid={testId} type="number" min={min} max={max} step={step} value={value} onChange={(event) => onChange(event.target.value)} /><span className="field-suffix">{suffix}</span></div></div>;
}

function SignalTable({ signals, loading, error, retry }: { signals: Signal[]; loading: boolean; error: boolean; retry: () => void }) {
  if (loading) return <div className="panel-body"><div className="loading-skeleton" /></div>;
  if (error) return <div className="load-error">Signals could not be loaded.<br /><button className="btn-quiet" data-testid="button-retry-signals" onClick={retry}>Retry</button></div>;
  if (!signals.length) return <div className="empty"><strong>No signals recorded yet</strong>New evaluations will appear here as the feed updates.</div>;
  return <div className="table-wrap"><table className="activity-table"><thead><tr><th>Direction</th><th>Timeframe</th><th>Price</th><th>Confidence</th><th>Reason</th><th>Time</th></tr></thead><tbody>{signals.slice(0, 8).map((signal) => <tr key={signal.id} data-testid={`row-signal-${signal.id}`}><td><span className={`direction ${signal.direction}`}>{signal.direction}</span></td><td>{signal.timeframe}</td><td>{formatPrice(signal.price)}</td><td>{signal.confidence.toFixed(1)}%</td><td title={signal.reason}>{truncate(signal.reason, 34)}</td><td>{formatTime(signal.timestamp)}</td></tr>)}</tbody></table></div>;
}

function TradeTable({ trades, loading, error, retry }: { trades: Trade[]; loading: boolean; error: boolean; retry: () => void }) {
  if (loading) return <div className="panel-body"><div className="loading-skeleton" /></div>;
  if (error) return <div className="load-error">Paper trades could not be loaded.<br /><button className="btn-quiet" data-testid="button-retry-trades" onClick={retry}>Retry</button></div>;
  if (!trades.length) return <div className="empty"><strong>No paper trades recorded</strong>Trade records appear when the simulator has a position to evaluate.</div>;
  return <div className="table-wrap"><table className="activity-table"><thead><tr><th>State</th><th>Side</th><th>Entry</th><th>Exit</th><th>Quantity</th><th>Fees / slip</th><th>Net P&amp;L</th></tr></thead><tbody>{trades.slice(0, 8).map((trade) => <tr key={trade.id} data-testid={`row-trade-${trade.id}`}><td className={trade.status === 'OPEN' ? 'open' : 'closed'}>{trade.status}</td><td>{trade.side}</td><td>{formatPrice(trade.entryPrice)}</td><td>{trade.exitPrice === null ? '—' : formatPrice(trade.exitPrice)}</td><td>{trade.quantity.toFixed(4)}{trade.unfilledEntryQuantity > 0 && <small className="quantity-detail" data-testid={`text-trade-unfilled-${trade.id}`}>{trade.unfilledEntryQuantity.toFixed(4)} SOL unfilled</small>}{trade.status === 'OPEN' && trade.exitFilledQuantity > 0 && <small className="quantity-detail" data-testid={`text-trade-remaining-${trade.id}`}>{trade.remainingQuantity.toFixed(4)} SOL remaining</small>}</td><td>{formatMoney(trade.feesUsdc + trade.slippageUsdc)}</td><td className={trade.realizedPnlUsdc !== null && trade.realizedPnlUsdc > 0 ? 'open' : ''}>{trade.realizedPnlUsdc === null ? '—' : formatMoney(trade.realizedPnlUsdc)}</td></tr>)}</tbody></table></div>;
}

function formatPrice(value: number) {
  return new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 }).format(value);
}
function formatMoney(value: number) {
  return `${value < 0 ? '−' : ''}$${Math.abs(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-US', { hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
}
function truncate(value: string, length: number) {
  return value.length > length ? `${value.slice(0, length - 1)}…` : value;
}

function Router() {
  return (
    // Keep a shared shell (sidebar, navbar) outside the boundary so it
    // survives a page crash.
    <RoutedErrorBoundary>
      <Switch>
        <Route path="/" component={Home} />
        <Route component={NotFound} />
      </Switch>
    </RoutedErrorBoundary>
  );
}

function RoutedErrorBoundary({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  return <ErrorBoundary resetKey={location}>{children}</ErrorBoundary>;
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, '')}>
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
