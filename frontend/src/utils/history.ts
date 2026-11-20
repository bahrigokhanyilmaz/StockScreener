import type { ScoreHistoryPoint } from '../api.ts';

export type HistoryRange = '30D' | '90D' | '1Y' | 'All';

export const HISTORY_RANGES: HistoryRange[] = ['30D', '90D', '1Y', 'All'];

const RANGE_DAYS: Record<HistoryRange, number | null> = { '30D': 30, '90D': 90, '1Y': 365, All: null };

/** Above this many points a window is shown as one bar per ISO week. */
export const DOWNSAMPLE_THRESHOLD = 120;

const DAY_MS = 86_400_000;

function toUtcMs(date: string): number {
  const [y, m, d] = date.split('-').map(Number);
  return Date.UTC(y, m - 1, d);
}

export function scoreColor(score: number | null): string {
  if (score === null) return '#6b7280';
  if (score >= 70) return '#22c55e';
  if (score >= 40) return '#f59e0b';
  return '#ef4444';
}

/** Sort ascending by date; for duplicate dates the last one in input order wins. */
export function normalizeHistory(points: ScoreHistoryPoint[]): ScoreHistoryPoint[] {
  const byDate = new Map<string, ScoreHistoryPoint>();
  for (const p of points) if (p.date) byDate.set(p.date, p);
  return [...byDate.values()].sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
}

/** Snapshots dated within the last N calendar days of the newest snapshot (inclusive). */
export function windowHistory(points: ScoreHistoryPoint[], range: HistoryRange): ScoreHistoryPoint[] {
  const sorted = normalizeHistory(points);
  const days = RANGE_DAYS[range];
  if (days === null || sorted.length === 0) return sorted;
  const cutoff = toUtcMs(sorted[sorted.length - 1].date) - days * DAY_MS;
  return sorted.filter((p) => toUtcMs(p.date) >= cutoff);
}

/** ISO-8601 week key (the week's Thursday decides the year), e.g. "2026-W47". */
export function isoWeekKey(date: string): string {
  const ms = toUtcMs(date);
  const dow = new Date(ms).getUTCDay() || 7; // Mon=1..Sun=7
  const thursday = ms + (4 - dow) * DAY_MS;
  const year = new Date(thursday).getUTCFullYear();
  const week = Math.floor((thursday - Date.UTC(year, 0, 1)) / DAY_MS / 7) + 1;
  return `${year}-W${String(week).padStart(2, '0')}`;
}

/** Keep the last snapshot of each ISO week, ascending. */
export function weeklyDownsample(points: ScoreHistoryPoint[]): ScoreHistoryPoint[] {
  const byWeek = new Map<string, ScoreHistoryPoint>();
  for (const p of normalizeHistory(points)) byWeek.set(isoWeekKey(p.date), p);
  return [...byWeek.values()];
}

/** A range button is enabled only if it shows more snapshots than the next shorter one. 30D is always enabled. */
export function availableRanges(points: ScoreHistoryPoint[]): Record<HistoryRange, boolean> {
  const result = {} as Record<HistoryRange, boolean>;
  let prev = -1;
  for (const r of HISTORY_RANGES) {
    const n = windowHistory(points, r).length;
    result[r] = r === '30D' || n > prev;
    prev = n;
  }
  return result;
}

export interface HistoryView {
  window: ScoreHistoryPoint[]; // snapshots in the range
  bars: ScoreHistoryPoint[]; // what is drawn (weekly when window is large)
  weekly: boolean;
}

export function historyView(points: ScoreHistoryPoint[], range: HistoryRange): HistoryView {
  const window = windowHistory(points, range);
  const weekly = window.length > DOWNSAMPLE_THRESHOLD;
  return { window, bars: weekly ? weeklyDownsample(window) : window, weekly };
}

export function historyHeading(view: HistoryView): string {
  const since = view.window[0]?.date ?? '';
  const weekly = view.weekly ? ', weekly bars' : '';
  return `Score History (${view.window.length} snapshots, since ${since}${weekly})`;
}

/** Indexes of the first, middle and last bar (deduplicated). */
export function labelIndexes(n: number): number[] {
  if (n <= 0) return [];
  return [...new Set([0, Math.floor((n - 1) / 2), n - 1])];
}

export interface BarGeometry {
  slot: number;
  barWidth: number;
  gap: number;
}

/** Bars share the width evenly; the gap shrinks for very dense charts so bars never exceed the width. */
export function barGeometry(n: number, width: number): BarGeometry {
  const slot = width / Math.max(n, 1);
  const gap = Math.min(1, slot / 4);
  return { slot, gap, barWidth: slot - gap };
}
