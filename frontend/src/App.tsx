import { useState, useEffect, useMemo } from 'react';
import { getStocks, getPipelineStatus, getStockPrices, getPortfolio, getIndustryAverages, untrackStock, trackStock } from './api.ts';
import type { Stock, PipelineStatus, IndustryAverages } from './api.ts';
import StockTable from './components/StockTable.tsx';
import StockDetail from './components/StockDetail.tsx';
import Portfolio from './components/Portfolio.tsx';
import TrackHistory from './components/TrackHistory.tsx';
import BuyModal from './components/BuyModal.tsx';
import FilterSliders, { getDefaultFilters, applyFilters } from './components/FilterSliders.tsx';
import type { FilterValues } from './components/FilterSliders.tsx';
import { calculateTrend } from './utils/trends.ts';
import type { TrendData } from './utils/trends.ts';
import './App.css';

/**
 * Main App Component
 *
 * Displays:
 * 1. Pipeline status header (active/grace counts)
 * 2. Filter slider panel (adjust thresholds, instantly re-filters table)
 * 3. Stock table (filtered stocks with key metrics)
 * 4. Stock detail panel (when a stock is selected)
 */
function App() {
  const [allStocks, setAllStocks] = useState<Stock[]>([]);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [pipelineStatus, setPipelineStatus] = useState<PipelineStatus | null>(null);
  const [trends, setTrends] = useState<Record<string, TrendData>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<FilterValues>(getDefaultFilters());
  const [buyingTicker, setBuyingTicker] = useState<string | null>(null);
  const [portfolioKey, setPortfolioKey] = useState(0); // increment to refresh portfolio
  const [ownedSymbols, setOwnedSymbols] = useState<Set<string>>(new Set());
  const [industryAverages, setIndustryAverages] = useState<IndustryAverages>({});
  const [detailCollapsed, setDetailCollapsed] = useState(false);
  const [trackHistoryKey, setTrackHistoryKey] = useState(0); // increment to refresh track history

  // Mark / unmark a stock for price-change tracking. Mark snapshots today's
  // closing price; unmark persists the stint to tracking history and reports
  // the final % change. The row stays on the dashboard either way.
  async function handleToggleMark(ticker: string, isMarked: boolean) {
    try {
      if (isMarked) {
        const res = await untrackStock(ticker);
        if (res.change_pct != null) {
          const sign = res.change_pct >= 0 ? '+' : '';
          alert(`${ticker} unmarked. Change while tracked: ${sign}${res.change_pct.toFixed(2)}% ` +
                `(${res.mark_date} → ${res.unmark_date})`);
        }
        setAllStocks(prev => prev.map(s => s.symbol === ticker
          ? { ...s, is_marked: false, mark_price: null, mark_date: null, mark_change_pct: null }
          : s));
        setTrackHistoryKey(k => k + 1);
      } else {
        const res = await trackStock(ticker);
        setAllStocks(prev => prev.map(s => s.symbol === ticker
          ? { ...s, is_marked: true, mark_price: res.mark_price ?? s.price, mark_date: res.mark_date ?? null, mark_change_pct: 0 }
          : s));
      }
    } catch (err) {
      console.error('Failed to toggle mark:', err);
      alert(`Failed to update tracking for ${ticker}.`);
    }
  }

  // Fetch data on mount
  useEffect(() => {
    async function loadData() {
      try {
        setLoading(true);
        const [stocksData, statusData, portfolioData, industryData] = await Promise.all([
          getStocks(),
          getPipelineStatus(),
          getPortfolio().catch(() => ({ positions: [] })),
          getIndustryAverages().catch(() => ({ industries: {}, count: 0 })),
        ]);
        setAllStocks(stocksData.stocks);
        setPipelineStatus(statusData);
        setOwnedSymbols(new Set(portfolioData.positions.map(p => p.symbol)));
        setIndustryAverages(industryData.industries);
        setError(null);

        // Fetch price trends for all stocks (batched to avoid API throttling)
        const symbols = stocksData.stocks.map((s: Stock) => s.symbol);
        const trendResults: Record<string, TrendData> = {};
        // Fetch in batches of 4 to avoid overwhelming API Gateway
        for (let i = 0; i < symbols.length; i += 4) {
          const batch = symbols.slice(i, i + 4);
          const batchPromises = batch.map(async (sym: string) => {
            try {
              const priceData = await getStockPrices(sym);
              const trend = calculateTrend(priceData.bars);
              if (trend) trendResults[sym] = trend;
            } catch { /* skip if no price data */ }
          });
          await Promise.all(batchPromises);
        }
        setTrends(trendResults);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load data');
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);

  // Apply filters client-side (instant, no API call), sort by score
  const filteredStocks = useMemo(() => {
    const filtered = applyFilters(allStocks as unknown as Record<string, unknown>[], filters, industryAverages) as unknown as Stock[];
    return filtered.sort((a, b) => (b.investability_score ?? 0) - (a.investability_score ?? 0));
  }, [allStocks, filters, industryAverages]);

  // Default to first stock when data loads or filters change
  useEffect(() => {
    if (filteredStocks.length > 0 && !selectedTicker) {
      setSelectedTicker(filteredStocks[0].symbol);
    }
  }, [filteredStocks, selectedTicker]);

  return (
    <div className="app">
      <header className="app-header">
        <h1>Stock Screener</h1>
        {pipelineStatus && (
          <div className="pipeline-status">
            <span className="status-badge active">
              {pipelineStatus.active_count} Active
            </span>
            <span className="status-badge grace">
              {pipelineStatus.grace_count} Grace
            </span>
            <span className="status-badge total">
              {pipelineStatus.total_tracked} Tracked
            </span>
            {allStocks.length > 0 && allStocks[0].last_updated && (
              <span className="prices-date">
                Prices as of: {allStocks[0].last_updated.slice(0, 10)}
              </span>
            )}
          </div>
        )}
      </header>

      <main className="app-main">
        {loading && <div className="loading">Loading stocks...</div>}
        {error && <div className="error">Error: {error}</div>}

        {!loading && !error && (
          <>
            <div className="top-filters">
              <FilterSliders
                filters={filters}
                onChange={setFilters}
                onReset={() => setFilters(getDefaultFilters())}
                matchCount={filteredStocks.length}
                totalCount={allStocks.length}
              />
            </div>

            <Portfolio key={portfolioKey} />

            <TrackHistory refreshKey={trackHistoryKey} />

            <div className={`content-layout ${detailCollapsed ? 'detail-hidden' : ''}`}>
              <div className="table-section">
                <StockTable
                  stocks={filteredStocks}
                  trends={trends}
                  ownedSymbols={ownedSymbols}
                  industryAverages={industryAverages}
                  selectedTicker={selectedTicker}
                  onSelectStock={setSelectedTicker}
                  onBuy={(ticker) => setBuyingTicker(ticker)}
                  onRelease={async (ticker) => {
                    await untrackStock(ticker);
                    setAllStocks(allStocks.filter(s => s.symbol !== ticker));
                    setTrackHistoryKey(k => k + 1);
                  }}
                  onToggleMark={handleToggleMark}
                />
              </div>

              {selectedTicker && !detailCollapsed && (
                <div className="detail-section">
                  <StockDetail
                    ticker={selectedTicker}
                    onClose={() => setSelectedTicker(null)}
                  />
                </div>
              )}

              <button
                className="panel-toggle"
                onClick={() => setDetailCollapsed(!detailCollapsed)}
                title={detailCollapsed ? 'Show detail panel' : 'Hide detail panel'}
              >
                {detailCollapsed ? '◀' : '▶'}
              </button>
            </div>
          </>
        )}

        {buyingTicker && (
          <BuyModal
            ticker={buyingTicker}
            currentPrice={allStocks.find(s => s.symbol === buyingTicker)?.price ?? null}
            onClose={() => setBuyingTicker(null)}
            onSuccess={() => {
              setPortfolioKey(k => k + 1);
              getPortfolio().then(d => setOwnedSymbols(new Set(d.positions.map(p => p.symbol)))).catch(() => {});
            }}
          />
        )}
      </main>
    </div>
  );
}

export default App;
