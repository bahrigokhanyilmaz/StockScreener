import { useState } from 'react';
import type { Stock, IndustryAverages } from '../api.ts';
import type { TrendData } from '../utils/trends.ts';

/**
 * StockTable Component
 *
 * Shows ALL tracked stocks with ALL metrics. Horizontally scrollable.
 * Metric values are color-coded: green = passes filter, red = fails filter.
 */

interface Props {
  stocks: Stock[];
  trends: Record<string, TrendData>;
  ownedSymbols: Set<string>;
  industryAverages: IndustryAverages;
  selectedTicker: string | null;
  onSelectStock: (ticker: string) => void;
  onBuy: (ticker: string) => void;
  onRelease: (ticker: string) => void;
}

// Filter thresholds (same as pipeline) for color-coding
const THRESHOLDS: Record<string, { type: 'max' | 'min'; value: number; percent?: boolean }> = {
  peg_ratio: { type: 'max', value: 1.0 },
  price_to_fcf: { type: 'max', value: 20 },
  debt_to_equity: { type: 'max', value: 1.0 },
  quick_ratio: { type: 'min', value: 1.0 },
  operating_margin: { type: 'min', value: 0, percent: true },
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

function targetUpsideColor(value: number | null | undefined): string {
  if (value === null || value === undefined) return '#64748b'; // gray — no data
  if (value < 0) return '#f87171';    // red — above target (no upside)
  if (value < 0.20) return '#fbbf24'; // yellow — some upside but <20%
  return '#4ade80';                    // green — 20%+ upside
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

function getDaysTracked(firstTracked: string | null | undefined): string {
  if (!firstTracked) return '—';
  const start = new Date(firstTracked);
  const now = new Date();
  const days = Math.floor((now.getTime() - start.getTime()) / 86400000);
  return days <= 0 ? '<1' : String(days);
}

function formatRiskFlag(flag: string | Record<string, unknown>): { label: string; status: string; tooltip: string } {
  // Handle both old format (string) and new ledger format (object)
  let flagName: string;
  let status = '';
  let daysActive = 0;
  let firstSeen = '';

  if (typeof flag === 'string') {
    flagName = flag;
  } else {
    flagName = (flag.flag as string) || '';
    status = (flag.status as string) || '';
    daysActive = (flag.days_active as number) || 0;
    firstSeen = (flag.first_seen as string) || '';
  }

  const labelMap: Record<string, string> = {
    'fraud_allegation': 'FRAUD',
    'SEC_investigation': 'SEC',
    'securities_fraud_investigation': 'SEC FRAUD',
    'accounting_irregularity': 'ACCT',
    'insider_selling': 'INSIDER',
    'lawsuit': 'LAWSUIT',
    'potential_class_action_lawsuit': 'LAWSUIT',
    'regulatory_risk': 'REG RISK',
    'management_departure': 'MGMT',
    'product_recall': 'RECALL',
    'revenue_risk': 'REV RISK',
  };

  const label = labelMap[flagName] || flagName.replace(/_/g, ' ').toUpperCase().slice(0, 8);

  let tooltip = flagName.replace(/_/g, ' ');
  if (firstSeen) tooltip += ` | since ${firstSeen}`;
  if (daysActive > 1) tooltip += ` | ${daysActive} days confirmed`;
  if (status === 'decayed') tooltip += ' | (priced in)';
  if (status === 'decaying') tooltip += ' | (decaying)';

  return { label, status, tooltip };
}

function getSellSignal(price: number | null, targetPrice: number | null): string {
  if (!price || !targetPrice || targetPrice <= 0) return '';
  const upside = (targetPrice - price) / price;
  if (upside <= 0) return 'SELL';
  if (upside <= 0.10) return 'NEAR';
  return '';
}

function renderTrendCell(trend: TrendData | undefined) {
  if (!trend) return <span style={{ color: '#64748b' }}>—</span>;

  // Show daily change (last bar vs previous bar)
  const closes = trend.closes;
  if (closes.length < 2) return <span style={{ color: '#64748b' }}>—</span>;

  const today = closes[closes.length - 1];
  const yesterday = closes[closes.length - 2];
  const dailyChange = (today - yesterday) / yesterday;

  const arrow = dailyChange >= 0 ? '↑' : '↓';
  const color = dailyChange >= 0.02 ? '#4ade80'
    : dailyChange <= -0.02 ? '#f87171'
    : dailyChange >= 0 ? '#86efac'
    : '#fb923c';
  const label = `${arrow} ${(Math.abs(dailyChange) * 100).toFixed(2)}%`;

  return (
    <span className="trend-cell" style={{ color }}>
      {label}
      {trend.isFalling && <span className="falling-badge">FALLING</span>}
      {trend.isStabilizing && <span className="stabilizing-badge">STABILIZING</span>}
      {trend.isRecovering && <span className="recovering-badge">RECOVERING</span>}
    </span>
  );
}

export default function StockTable({ stocks, trends, ownedSymbols, industryAverages, selectedTicker, onSelectStock, onBuy, onRelease }: Props) {
  const [sortCol, setSortCol] = useState<string>('investability_score');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  if (stocks.length === 0) {
    return (
      <div className="empty-state">
        <p>No tracked stocks yet. The pipeline runs daily at 4 PM ET.</p>
      </div>
    );
  }

  const columns: { key: string; label: string; className?: string }[] = [
    { key: 'symbol', label: 'Stock', className: 'sticky-col' },
    { key: 'investability_score', label: 'Score', className: 'sticky-col-2' },
    { key: 'risk_flags', label: 'Risk' },
    { key: 'competition_score', label: 'Comp' },
    { key: 'tracking_status', label: 'Status' },
    { key: 'first_tracked', label: 'Days' },
    { key: 'price', label: 'Price' },
    { key: 'market_cap', label: 'Mkt Cap' },
    { key: '_daily', label: 'Daily' },
    { key: 'pe_ratio', label: 'P/E' },
    { key: '_pe_50th', label: 'P/E 50th' },
    { key: 'forward_pe', label: 'Fwd P/E' },
    { key: 'peg_ratio', label: 'PEG' },
    { key: 'price_to_fcf', label: 'P/FCF' },
    { key: 'debt_to_equity', label: 'D/E' },
    { key: 'interest_coverage_ratio', label: 'ICR' },
    { key: 'quick_ratio', label: 'QR' },
    { key: 'operating_margin', label: 'Op Margin' },
    { key: 'revenue_growth_yoy', label: 'Rev Gr' },
    { key: 'est_lt_growth', label: 'LT Gr' },
    { key: 'target_price_upside', label: 'Target ↑' },
    { key: '_signal', label: 'Signal' },
    { key: '_buy', label: '' },
    { key: '_release', label: '' },
  ];

  function handleSort(key: string) {
    if (key.startsWith('_')) return; // Non-sortable columns
    if (sortCol === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortCol(key);
      setSortDir(key === 'symbol' || key === 'tracking_status' ? 'asc' : 'desc');
    }
  }

  const sortedStocks = [...stocks].sort((a, b) => {
    let aVal: unknown = (a as unknown as Record<string, unknown>)[sortCol];
    let bVal: unknown = (b as unknown as Record<string, unknown>)[sortCol];

    // Special cases
    if (sortCol === 'risk_flags') {
      aVal = (a.risk_flags || []).length;
      bVal = (b.risk_flags || []).length;
    } else if (sortCol === 'first_tracked') {
      aVal = a.first_tracked ? new Date(a.first_tracked).getTime() : 0;
      bVal = b.first_tracked ? new Date(b.first_tracked).getTime() : 0;
    }

    // Nulls always sort last
    if (aVal == null && bVal == null) return 0;
    if (aVal == null) return 1;
    if (bVal == null) return -1;

    let cmp = 0;
    if (typeof aVal === 'string' && typeof bVal === 'string') {
      cmp = aVal.localeCompare(bVal);
    } else {
      cmp = (aVal as number) - (bVal as number);
    }
    return sortDir === 'asc' ? cmp : -cmp;
  });

  return (
    <div className="stock-table-container">
      <table className="stock-table">
        <thead>
          <tr>
            {columns.map(col => (
              <th
                key={col.key}
                className={`${col.className || ''} ${col.key.startsWith('_') ? '' : 'sortable-th'}`}
                onClick={() => handleSort(col.key)}
              >
                {col.label}
                {sortCol === col.key && <span className="sort-arrow">{sortDir === 'asc' ? ' ▲' : ' ▼'}</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortedStocks.map((stock) => {
            const isOwned = ownedSymbols.has(stock.symbol);
            const signal = isOwned ? getSellSignal(stock.price, stock.analyst_target_price ?? null) : '';
            return (
              <tr
                key={stock.symbol}
                className={`stock-row ${selectedTicker === stock.symbol ? 'selected' : ''} ${stock.tracking_status === 'GRACE' ? 'grace-row' : ''}`}
                onClick={() => onSelectStock(stock.symbol)}
              >
                <td className="sticky-col">
                  <div className="stock-name-cell">
                    <span className="stock-symbol">{stock.symbol}</span>
                    <span className="stock-company">{stock.company_name}</span>
                  </div>
                </td>
                <td className="score-cell sticky-col-2">
                  <span className="score-badge" style={{ backgroundColor: getScoreColor(stock.investability_score) }}>
                    {stock.investability_score !== null ? stock.investability_score.toFixed(0) : '—'}
                  </span>
                </td>
                <td className="risk-cell">
                  {stock.risk_flags && stock.risk_flags.length > 0 && (() => {
                    const count = stock.risk_flags.length;
                    const color = count >= 4 ? '#ef4444' : count >= 3 ? '#f97316' : count >= 2 ? '#eab308' : '#facc15';
                    const bg = count >= 4 ? '#7f1d1d44' : count >= 3 ? '#7c2d1244' : count >= 2 ? '#71380044' : '#71380033';
                    return (
                      <span className="risk-indicator" style={{ background: bg }}>
                        <span className="risk-dot" style={{ color }}>⚠</span>
                        <span className="risk-count" style={{ color }}>{count}</span>
                        <div className="risk-tooltip">
                          {stock.risk_flags.map((flag: string | Record<string, unknown>, i: number) => {
                            const { label, status, tooltip } = formatRiskFlag(flag);
                            return (
                              <span
                                key={i}
                                className={`table-risk-badge ${status === 'decayed' ? 'risk-decayed' : status === 'decaying' ? 'risk-decaying' : ''}`}
                                title={tooltip}
                              >
                                {label}
                              </span>
                            );
                          })}
                        </div>
                      </span>
                    );
                  })()}
                </td>
                <td className="comp-cell" title={stock.competition_reasoning || ''}>
                  {stock.competition_score != null ? (
                    <span className={`comp-badge comp-${stock.competition_score}`}>
                      {stock.hhi_score != null && stock.hhi_score !== stock.competition_score
                        ? `${stock.hhi_score}→${stock.competition_score}`
                        : stock.competition_score}
                    </span>
                  ) : '—'}
                </td>
                <td>
                  <span className={`status-pill ${stock.tracking_status === 'ACTIVE' ? 'status-active' : stock.tracking_status === 'GRACE' ? 'status-grace' : ''}`}>
                    {stock.tracking_status}
                  </span>
                </td>
                <td className="days-cell">{getDaysTracked(stock.first_tracked)}</td>
                <td>${formatNum(stock.price)}</td>
                <td>{formatMarketCap(stock.market_cap)}</td>
                <td>{renderTrendCell(trends[stock.symbol])}</td>
                <td style={{ color: metricColor('pe_ratio', stock.pe_ratio) }}>{formatNum(stock.pe_ratio, 1)}</td>
                <td style={{ color: '#64748b' }}>{formatNum(((industryAverages[stock.sic_industry] || {}).pe_median ?? (industryAverages[stock.sic_industry] || {}).pe_lower_quartile) as number | undefined, 1)}</td>
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
                <td style={{ color: metricColor('revenue_growth_yoy', stock.revenue_growth_yoy as number | null) }}>{formatPct(stock.revenue_growth_yoy as number | null)}</td>
                <td style={{ color: metricColor('est_lt_growth', stock.est_lt_growth as number | null) }}>{formatPct(stock.est_lt_growth as number | null)}</td>
                <td style={{ color: targetUpsideColor(stock.target_price_upside) }}>{formatPct(stock.target_price_upside)}</td>
                <td>
                  {signal && <span className={`sell-indicator ${signal === 'SELL' ? 'sell-now' : ''}`}>{signal}</span>}
                </td>
                <td>
                  <button
                    className="btn-buy-small"
                    onClick={(e) => { e.stopPropagation(); onBuy(stock.symbol); }}
                    title="Record purchase"
                  >Buy</button>
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
