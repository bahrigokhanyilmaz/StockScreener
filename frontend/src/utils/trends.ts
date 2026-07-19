import type { PriceBar } from '../api.ts';

/**
 * Price trend analysis utilities.
 *
 * Uses 30-day daily price bars to compute:
 * - Overall trend (% change over period)
 * - Consecutive declining days
 * - Trend state: FALLING, STABILIZING, RECOVERING, or normal
 *
 * States:
 * - FALLING: 5+ consecutive down days or -15% in 10 days
 * - STABILIZING: Was falling (10d decline > 10%), but last 3 days flat or up
 * - RECOVERING: Was falling (10d decline > 10%), now 3+ consecutive up days
 */

export type TrendState = 'falling' | 'stabilizing' | 'recovering' | 'normal';

export interface TrendData {
  /** Percentage change over full period (e.g., -0.08 = -8%) */
  changePercent: number;
  /** Percentage change over last 5 trading days */
  change5d: number;
  /** Percentage change over last 10 trading days */
  change10d: number;
  /** Number of consecutive declining days (from most recent) */
  consecutiveDownDays: number;
  /** Number of consecutive up days (from most recent) */
  consecutiveUpDays: number;
  /** Current trend state */
  state: TrendState;
  /** Whether the stock is in a "falling" state */
  isFalling: boolean;
  /** Whether the stock was recently falling but has stabilized */
  isStabilizing: boolean;
  /** Whether the stock is recovering from a recent decline */
  isRecovering: boolean;
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

  // Consecutive up days from most recent
  let consecutiveUpDays = 0;
  for (let i = closes.length - 1; i > 0; i--) {
    if (closes[i] > closes[i - 1]) {
      consecutiveUpDays++;
    } else {
      break;
    }
  }

  // Check if there was a significant decline in the broader window (days 4-14 ago)
  // This determines if current stabilization/recovery is coming off a drop
  const hadRecentDecline = (() => {
    if (closes.length < 8) return false;
    // Look at the period from 5-14 days ago for a decline
    const lookbackEnd = closes.length - 4; // exclude last 3 days
    const lookbackStart = Math.max(0, closes.length - 15);
    if (lookbackStart >= lookbackEnd) return false;
    const periodStart = closes[lookbackStart];
    const periodEnd = closes[lookbackEnd];
    return (periodEnd - periodStart) / periodStart <= -0.10;
  })();

  // Determine state
  const isCurrentlyFalling = consecutiveDownDays >= 5 || change10d <= -0.15;
  const isStabilizing = !isCurrentlyFalling && hadRecentDecline && consecutiveUpDays >= 1 && consecutiveUpDays <= 2 && change5d >= -0.02;
  const isRecovering = !isCurrentlyFalling && hadRecentDecline && consecutiveUpDays >= 3;

  let state: TrendState = 'normal';
  if (isCurrentlyFalling) state = 'falling';
  else if (isRecovering) state = 'recovering';
  else if (isStabilizing) state = 'stabilizing';

  return {
    changePercent,
    change5d,
    change10d,
    consecutiveDownDays,
    consecutiveUpDays,
    state,
    isFalling: isCurrentlyFalling,
    isStabilizing,
    isRecovering,
    lastClose,
    closes,
  };
}
