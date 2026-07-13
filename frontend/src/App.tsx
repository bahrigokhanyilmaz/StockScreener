import { useState, useEffect } from 'react';
import { getStocks, getPipelineStatus } from './api';
import type { Stock, PipelineStatus } from './api';
import StockTable from './components/StockTable';
import StockDetail from './components/StockDetail';
import './App.css';

/**
 * Main App Component
 * 
 * Displays:
 * 1. Pipeline status header (active/grace counts)
 * 2. Stock table (all tracked stocks with key metrics)
 * 3. Stock detail panel (when a stock is selected)
 */
function App() {
  const [stocks, setStocks] = useState<Stock[]>([]);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [pipelineStatus, setPipelineStatus] = useState<PipelineStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch data on mount
  useEffect(() => {
    async function loadData() {
      try {
        setLoading(true);
        const [stocksData, statusData] = await Promise.all([
          getStocks(),
          getPipelineStatus(),
        ]);
        setStocks(stocksData.stocks);
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
            <div className="table-section">
              <StockTable
                stocks={stocks}
                selectedTicker={selectedTicker}
                onSelectStock={setSelectedTicker}
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
