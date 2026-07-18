import { useState, useEffect, useMemo } from 'react';
import { getStocks, getPipelineStatus, untrackStock } from './api.ts';
import type { Stock, PipelineStatus } from './api.ts';
import StockTable from './components/StockTable.tsx';
import StockDetail from './components/StockDetail.tsx';
import FilterSliders, { getDefaultFilters, applyFilters } from './components/FilterSliders.tsx';
import type { FilterValues } from './components/FilterSliders.tsx';
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<FilterValues>(getDefaultFilters());

  // Fetch data on mount
  useEffect(() => {
    async function loadData() {
      try {
        setLoading(true);
        const [stocksData, statusData] = await Promise.all([
          getStocks(),
          getPipelineStatus(),
        ]);
        setAllStocks(stocksData.stocks);
        setPipelineStatus(statusData);
        setError(null);
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
    const filtered = applyFilters(allStocks as unknown as Record<string, unknown>[], filters) as unknown as Stock[];
    return filtered.sort((a, b) => (b.investability_score ?? 0) - (a.investability_score ?? 0));
  }, [allStocks, filters]);

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
          </div>
        )}
      </header>

      <main className="app-main">
        {loading && <div className="loading">Loading stocks...</div>}
        {error && <div className="error">Error: {error}</div>}

        {!loading && !error && (
          <div className="content-layout">
            <div className="sidebar">
              <FilterSliders
                filters={filters}
                onChange={setFilters}
                onReset={() => setFilters(getDefaultFilters())}
                matchCount={filteredStocks.length}
                totalCount={allStocks.length}
              />
            </div>

            <div className="table-section">
              <StockTable
                stocks={filteredStocks}
                selectedTicker={selectedTicker}
                onSelectStock={setSelectedTicker}
                onRelease={async (ticker) => {
                  await untrackStock(ticker);
                  setAllStocks(allStocks.filter(s => s.symbol !== ticker));
                }}
              />
            </div>

            {selectedTicker && (
              <div className="detail-section">
                <StockDetail
                  ticker={selectedTicker}
                  onClose={() => setSelectedTicker(null)}
                />
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
