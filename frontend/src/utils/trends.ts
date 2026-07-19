import type { PriceBar } from '../api.ts';

/**
 * Price trend analysis utilities.
 *
 * Uses 30-day daily price bars to compute:
 * - Overall trend (% change over period)
 * - Consecutive declining days
 * - Whether the stock is in a "falling" state
 */

export interface TrendData {
  /** Percentage change over full period (e.g., -0.08 = -8%) */
  changePercent: number;
  /** Percentage change over last 5 trading days */
  change5d: number;
  /** Percentage change over last 10 trading days */
  change10d: number;
  /** Number of consecutive declining days (from most recent) */
  consecutiveDownDays: number;
  /** Whether the stock is in a "falling" state (5+ down days or -15% in 10d) */
  isFalling: boolean;
  /** Most recent close price */
  lastClose: number;
  /** Array of close prices for sparkline rendering */
  closes: number[];
}

export function calculateTrend(bars: PriceBar[]): TrendData | null {
  if (!bars || bars.length < 2) return null;

  const closes = bars.map(b => b.c);
  const lastClose = closes[closes.length - 1];
  const firstClose = closes[0];

  // Overall change
  const changePercent = (lastClose - firstClose) / firstClose;

  // Last 5 days
  const close5dAgo = closes.length >= 6 ? closes[closes.length - 6] : closes[0];
  const change5d = (lastClose - close5dAgo) / close5dAgo;

  // Last 10 days
  const close10dAgo = closes.length >= 11 ? closes[closes.length - 11] : closes[0];
  const change10d = (lastClose - close10dAgo) / close10dAgo;

  // Consecutive declining days from most recent
  let consecutiveDownDays = 0;
  for (let i = closes.length - 1; i > 0; i--) {
    if (closes[i] < closes[i - 1]) {
      consecutiveDownDays++;
    } else {
      break;
    }
  }

  // Falling: 5+ consecutive down days OR -15% in last 10 trading days
  const isFalling = consecutiveDownDays >= 5 || change10d <= -0.15;

  return {
    changePercent,
    change5d,
    change10d,
    consecutiveDownDays,
    isFalling,
    lastClose,
    closes,
  };
}
