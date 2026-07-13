import type { Stock } from '../api';

/**
 * StockTable Component
 * 
 * Displays all tracked stocks in a sortable table.
 * Each row shows key metrics at a glance. Click a row to see detail.
 * 
 * Columns:
 * - Symbol + Company name
 * - Investability Score (the combined score)
 * - Status (Active/Grace — color coded)
 * - Key metrics (P/E, D/E, Operating Margin)
 * - Sentiment indicator
 */

interface Props {
  stocks: Stock[];
  selectedTicker: string | null;
  onSelectStock: (ticker: string) => void;
}

function formatNumber(value: number | null | undefined, decimals = 2): string {
  if (value === null || value === undefined) return '—';
  return value.toFixed(decimals);
}

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return `${(value * 100).toFixed(1)}%`;
}

function formatMarketCap(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  if (value >= 1e12) return `$${(value / 1e12).toFixed(1)}T`;
  if (value >= 1e9) return `$${(value / 1e9).toFixed(1)}B`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(0)}M`;
  return `$${value.toLocaleString()}`;
}

function getScoreColor(score: number | null): string {
  if (score === null) return 'gray';
  if (score >= 70) return '#22c55e'; // green
  if (score >= 40) return '#f59e0b'; // amber
  return '#ef4444'; // red
}

function getStatusClass(status: string): string {
  switch (status) {
    case 'ACTIVE': return 'status-active';
    case 'GRACE': return 'status-grace';
    case 'MANUAL': return 'status-manual';
    default: return '';
  }
}

export default function StockTable({ stocks, selectedTicker, onSelectStock }: Props) {
  if (stocks.length === 0) {
    return (
      <div className="empty-state">
        <p>No tracked stocks yet. The pipeline runs daily at 4 PM ET.</p>
        <p>Stocks that pass your value filters will appear here.</p>
      </div>
    );
  }

  return (
    <div className="stock-table-container">
      <table className="stock-table">
        <thead>
          <tr>
            <th>Stock</th>
            <th>Score</th>
            <th>Status</th>
            <th>Price</th>
            <th>Market Cap</th>
            <th>P/E</th>
            <th>D/E</th>
            <th>Op Margin</th>
            <th>Sentiment</th>
          </tr>
        </thead>
        <tbody>
          {stocks.map((stock) => (
            <tr
              key={stock.symbol}
              className={`stock-row ${selectedTicker === stock.symbol ? 'selected' : ''}`}
              onClick={() => onSelectStock(stock.symbol)}
            >
              <td className="stock-name-cell">
                <span className="stock-symbol">{stock.symbol}</span>
                <span className="stock-company">{stock.company_name}</span>
              </td>
              <td>
                <span
                  className="score-badge"
                  style={{ backgroundColor: getScoreColor(stock.investability_score) }}
                >
                  {stock.investability_score !== null ? stock.investability_score.toFixed(0) : '—'}
                </span>
              </td>
              <td>
                <span className={`status-pill ${getStatusClass(stock.tracking_status)}`}>
                  {stock.tracking_status}
                </span>
              </td>
              <td>${formatNumber(stock.price)}</td>
              <td>{formatMarketCap(stock.market_cap)}</td>
              <td>{formatNumber(stock.pe_ratio)}</td>
              <td>{formatNumber(stock.debt_to_equity)}</td>
              <td>{formatPercent(stock.operating_margin)}</td>
              <td>
                <SentimentIndicator score={stock.sentiment_score} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SentimentIndicator({ score }: { score: number | null }) {
  if (score === null || score === undefined) return <span className="sentiment-na">—</span>;

  let emoji = '😐';
  let color = '#6b7280';
  if (score > 0.3) { emoji = '😀'; color = '#22c55e'; }
  else if (score > 0.1) { emoji = '🙂'; color = '#84cc16'; }
  else if (score < -0.3) { emoji = '😟'; color = '#ef4444'; }
  else if (score < -0.1) { emoji = '😐'; color = '#f59e0b'; }

  return (
    <span className="sentiment-indicator" style={{ color }} title={`Sentiment: ${score.toFixed(2)}`}>
      {emoji} {score.toFixed(2)}
    </span>
  );
}
