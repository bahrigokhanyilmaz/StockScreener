import { useState, useEffect } from 'react';
import { getStockDetail, getStockHistory } from '../api.ts';
import type { Stock, ScoreHistoryPoint } from '../api.ts';

/**
 * StockDetail Component
 * 
 * Shows full detail for a selected stock:
 * - Company info (name, sector, price)
 * - Score breakdown (fundamental, sentiment, investability)
 * - Key financial metrics
 * - Score history over time (simple chart)
 * - Risk flags if any
 */

interface Props {
  ticker: string;
  onClose: () => void;
}

export default function StockDetail({ ticker, onClose }: Props) {
  const [stock, setStock] = useState<Stock | null>(null);
  const [history, setHistory] = useState<ScoreHistoryPoint[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const [detailData, historyData] = await Promise.all([
          getStockDetail(ticker),
          getStockHistory(ticker),
        ]);
        setStock(detailData.stock);
        setHistory(historyData.history);
      } catch (err) {
        console.error('Failed to load stock detail:', err);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [ticker]);

  if (loading) return <div className="detail-loading">Loading {ticker}...</div>;
  if (!stock) return <div className="detail-error">Stock not found</div>;

  return (
    <div className="stock-detail">
      <div className="detail-header">
        <div>
          <h2>{stock.symbol}</h2>
          <p className="company-name">{stock.company_name}</p>
          <p className="sector-info">{stock.sector} / {stock.industry}</p>
        </div>
        <button className="close-btn" onClick={onClose}>Close</button>
      </div>

      <div className="score-section">
        <ScoreCard
          label="Investability"
          value={stock.investability_score}
          max={100}
          color={scoreColor(stock.investability_score)}
        />
        <ScoreCard
          label="Fundamental"
          value={stock.fundamental_score}
          max={100}
          color="#3b82f6"
        />
        <ScoreCard
          label="Sentiment"
          value={stock.sentiment_score !== null ? stock.sentiment_score * 100 : null}
          max={100}
          min={-100}
          color={sentimentColor(stock.sentiment_score)}
        />
      </div>

      <div className="metrics-grid">
        <MetricItem label="Price" value={stock.price ? `$${stock.price.toFixed(2)}` : '—'} />
        <MetricItem label="P/E" value={stock.pe_ratio?.toFixed(1) ?? '—'} />
        <MetricItem label="PEG" value={stock.peg_ratio?.toFixed(2) ?? '—'} />
        <MetricItem label="Debt/Equity" value={stock.debt_to_equity?.toFixed(2) ?? '—'} />
        <MetricItem label="Quick Ratio" value={stock.quick_ratio?.toFixed(2) ?? '—'} />
        <MetricItem label="Op Margin" value={stock.operating_margin ? `${(stock.operating_margin * 100).toFixed(1)}%` : '—'} />
        <MetricItem label="Target Upside" value={stock.target_price_upside ? `${(stock.target_price_upside * 100).toFixed(1)}%` : '—'} />
        <MetricItem label="Status" value={stock.tracking_status} />
      </div>

      {stock.risk_flags && stock.risk_flags.length > 0 && (
        <div className="risk-flags">
          <h4>Risk Flags</h4>
          {stock.risk_flags.map((flag, i) => (
            <span key={i} className="risk-flag-badge">{flag}</span>
          ))}
        </div>
      )}

      {history.length > 0 && (
        <div className="history-section">
          <h4>Score History ({history.length} days)</h4>
          <div className="history-chart">
            {history.map((point, i) => (
              <div key={i} className="history-bar-wrapper" title={`${point.date}: ${point.investability_score}`}>
                <div
                  className="history-bar"
                  style={{
                    height: `${Math.max(5, (point.investability_score ?? 0))}%`,
                    backgroundColor: scoreColor(point.investability_score),
                  }}
                />
                <span className="history-date">{point.date?.slice(5)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="detail-footer">
        <small>Last updated: {stock.last_updated ? new Date(stock.last_updated).toLocaleString() : '—'}</small>
      </div>
    </div>
  );
}

function ScoreCard({ label, value, max, min = 0, color }: {
  label: string; value: number | null; max: number; min?: number; color: string;
}) {
  const display = value !== null ? value.toFixed(0) : '—';
  const percentage = value !== null ? ((value - min) / (max - min)) * 100 : 0;

  return (
    <div className="score-card">
      <div className="score-value" style={{ color }}>{display}</div>
      <div className="score-bar-bg">
        <div className="score-bar-fill" style={{ width: `${Math.max(0, Math.min(100, percentage))}%`, backgroundColor: color }} />
      </div>
      <div className="score-label">{label}</div>
    </div>
  );
}

function MetricItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric-item">
      <span className="metric-label">{label}</span>
      <span className="metric-value">{value}</span>
    </div>
  );
}

function scoreColor(score: number | null): string {
  if (score === null) return '#6b7280';
  if (score >= 70) return '#22c55e';
  if (score >= 40) return '#f59e0b';
  return '#ef4444';
}

function sentimentColor(score: number | null): string {
  if (score === null) return '#6b7280';
  if (score > 0.1) return '#22c55e';
  if (score < -0.1) return '#ef4444';
  return '#f59e0b';
}
