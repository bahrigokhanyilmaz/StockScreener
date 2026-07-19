import type { Stock } from '../api.ts';

/**
 * StockTable Component
 *
 * Shows ALL tracked stocks with ALL metrics. Horizontally scrollable.
 * Metric values are color-coded: green = passes filter, red = fails filter.
 */

interface Props {
  stocks: Stock[];
  selectedTicker: string | null;
  onSelectStock: (ticker: string) => void;
  onRelease: (ticker: string) => void;
}

// Filter thresholds (same as pipeline) for color-coding
const THRESHOLDS: Record<string, { type: 'max' | 'min'; value: number; percent?: boolean }> = {
  pe_ratio: { type: 'max', value: 50 },
  forward_pe: { type: 'max', value: 20 },
  peg_ratio: { type: 'max', value: 1.0 },
  price_to_fcf: { type: 'max', value: 20 },
  debt_to_equity: { type: 'max', value: 1.0 },
  quick_ratio: { type: 'min', value: 1.0 },
  operating_margin: { type: 'min', value: 0, percent: true },
  eps_growth_yoy: { type: 'min', value: 0, percent: true },
  revenue_growth_yoy: { type: 'min', value: 0, percent: true },
  est_lt_growth: { type: 'min', value: 0, percent: true },
  target_price_upside: { type: 'min', value: 0.20, percent: true },
};

function passesThreshold(key: string, value: number | null | undefined): boolean | null {
  if (value === null || value === undefined) return null; // No data
  const t = THRESHOLDS[key];
  if (!t) return null;
  const threshold = t.percent ? t.value : t.value;
  if (t.type === 'max') return value <= threshold;
  return value >= threshold;
}

function metricColor(key: string, value: number | null | undefined): string {
  const passes = passesThreshold(key, value);
  if (passes === null) return '#64748b'; // gray — no data
  return passes ? '#4ade80' : '#f87171'; // green or red
}

function icrColor(value: number | null | undefined): string {
  if (value === null || value === undefined) return '#64748b';
  if (value >= 5) return '#4ade80';   // strong
  if (value >= 3) return '#86efac';   // comfortable
  if (value >= 1) return '#fbbf24';   // tight
  return '#f87171';                    // can't cover interest
}

function deColor(de: number | null | undefined, icr: number | null | undefined): string {
  if (de === null || de === undefined) return '#64748b';
  if (de <= 1.0) return '#4ade80'; // passes threshold — green
  // Exceeds threshold — check ICR override
  if (icr !== null && icr !== undefined && icr > 3.0) return '#fbbf24'; // amber: overridden
  return '#f87171'; // red: fails with no override
}

function formatNum(value: number | null | undefined, decimals = 2): string {
  if (value === null || value === undefined) return '—';
  return value.toFixed(decimals);
}

function formatPct(value: number | null | undefined): string {
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
  if (score >= 70) return '#22c55e';
  if (score >= 40) return '#f59e0b';
  return '#ef4444';
}

function getDaysTracked(lastUpdated: string | null | undefined): string {
  if (!lastUpdated) return '—';
  const start = new Date(lastUpdated);
  const now = new Date();
  const days = Math.floor((now.getTime() - start.getTime()) / 86400000);
  return days <= 0 ? '<1' : String(days);
}

function getSellSignal(price: number | null, targetPrice: number | null): string {
  if (!price || !targetPrice || targetPrice <= 0) return '';
  const upside = (targetPrice - price) / price;
  if (upside <= 0) return 'SELL';
  if (upside <= 0.10) return 'NEAR';
  return '';
}

export default function StockTable({ stocks, selectedTicker, onSelectStock, onRelease }: Props) {
  if (stocks.length === 0) {
    return (
      <div className="empty-state">
        <p>No tracked stocks yet. The pipeline runs daily at 4 PM ET.</p>
      </div>
    );
  }

  return (
    <div className="stock-table-container">
      <table className="stock-table">
        <thead>
          <tr>
            <th className="sticky-col">Stock</th>
            <th>Score</th>
            <th>Status</th>
            <th>Days</th>
            <th>Price</th>
            <th>Mkt Cap</th>
            <th>P/E</th>
            <th>Fwd P/E</th>
            <th>PEG</th>
            <th>P/FCF</th>
            <th>D/E</th>
            <th>ICR</th>
            <th>QR</th>
            <th>Op Margin</th>
            <th>EPS Gr</th>
            <th>Rev Gr</th>
            <th>LT Gr</th>
            <th>Target ↑</th>
            <th>Signal</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {stocks.map((stock) => {
            const signal = getSellSignal(stock.price, stock.analyst_target_price ?? null);
            return (
              <tr
                key={stock.symbol}
                className={`stock-row ${selectedTicker === stock.symbol ? 'selected' : ''} ${stock.tracking_status === 'GRACE' ? 'grace-row' : ''}`}
                onClick={() => onSelectStock(stock.symbol)}
              >
                <td className="stock-name-cell sticky-col">
                  <span className="stock-symbol">{stock.symbol}</span>
                  <span className="stock-company">{stock.company_name}</span>
                </td>
                <td>
                  <span className="score-badge" style={{ backgroundColor: getScoreColor(stock.investability_score) }}>
                    {stock.investability_score !== null ? stock.investability_score.toFixed(0) : '—'}
                  </span>
                </td>
                <td>
                  <span className={`status-pill ${stock.tracking_status === 'ACTIVE' ? 'status-active' : stock.tracking_status === 'GRACE' ? 'status-grace' : ''}`}>
                    {stock.tracking_status}
                  </span>
                </td>
                <td className="days-cell">{getDaysTracked(stock.last_updated)}</td>
                <td>${formatNum(stock.price)}</td>
                <td>{formatMarketCap(stock.market_cap)}</td>
                <td style={{ color: metricColor('pe_ratio', stock.pe_ratio) }}>{formatNum(stock.pe_ratio, 1)}</td>
                <td style={{ color: metricColor('forward_pe', stock.forward_pe as number | null) }}>{formatNum(stock.forward_pe as number | null, 1)}</td>
                <td style={{ color: metricColor('peg_ratio', stock.peg_ratio) }}>{formatNum(stock.peg_ratio)}</td>
                <td style={{ color: metricColor('price_to_fcf', stock.price_to_fcf as number | null) }}>{formatNum(stock.price_to_fcf as number | null, 1)}</td>
                <td style={{ color: deColor(stock.debt_to_equity, stock.interest_coverage_ratio) }}>
                  {formatNum(stock.debt_to_equity)}
                  {stock.debt_to_equity !== null && stock.debt_to_equity > 1.0 && stock.interest_coverage_ratio !== null && stock.interest_coverage_ratio > 3.0 && (
                    <span className="icr-override-badge">ICR✓</span>
                  )}
                </td>
                <td style={{ color: icrColor(stock.interest_coverage_ratio) }}>{formatNum(stock.interest_coverage_ratio, 1)}</td>
                <td style={{ color: metricColor('quick_ratio', stock.quick_ratio) }}>{formatNum(stock.quick_ratio)}</td>
                <td style={{ color: metricColor('operating_margin', stock.operating_margin) }}>{formatPct(stock.operating_margin)}</td>
                <td style={{ color: metricColor('eps_growth_yoy', stock.eps_growth_yoy as number | null) }}>{formatPct(stock.eps_growth_yoy as number | null)}</td>
                <td style={{ color: metricColor('revenue_growth_yoy', stock.revenue_growth_yoy as number | null) }}>{formatPct(stock.revenue_growth_yoy as number | null)}</td>
                <td style={{ color: metricColor('est_lt_growth', stock.est_lt_growth as number | null) }}>{formatPct(stock.est_lt_growth as number | null)}</td>
                <td style={{ color: metricColor('target_price_upside', stock.target_price_upside) }}>{formatPct(stock.target_price_upside)}</td>
                <td>
                  {signal && <span className={`sell-indicator ${signal === 'SELL' ? 'sell-now' : ''}`}>{signal}</span>}
                </td>
                <td>
                  <button
                    className="btn-release"
                    onClick={(e) => { e.stopPropagation(); onRelease(stock.symbol); }}
                    title="Remove (sold)"
                  >✕</button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
