import { describe, expect, it } from 'vitest';
import type { ScoreHistoryPoint } from '../api.ts';
import {
  availableRanges, barGeometry, historyHeading, historyView, isoWeekKey, labelIndexes, normalizeHistory,
  weeklyDownsample, windowHistory,
} from '../utils/history.ts';

const pt = (date: string, score: number | null = 50): ScoreHistoryPoint => ({
  date, investability_score: score, fundamental_score: null, sentiment_score: null, price: null,
});

// n weekday snapshots ending on `end` (YYYY-MM-DD), ascending.
function weekdays(end: string, n: number): ScoreHistoryPoint[] {
  const out: ScoreHistoryPoint[] = [];
  const d = new Date(end + 'T00:00:00Z');
  while (out.length < n) {
    const dow = d.getUTCDay();
    if (dow !== 0 && dow !== 6) out.unshift(pt(d.toISOString().slice(0, 10), 40 + (out.length % 50)));
    d.setUTCDate(d.getUTCDate() - 1);
  }
  return out;
}

// Real shape: 52 snapshots 2026-09-09 .. 2026-11-19 (weekdays, minus holidays/missed runs).
const real52 = weekdays('2026-11-19', 52);

describe('windowHistory', () => {
  it('30D keeps snapshots within 30 calendar days of the newest', () => {
    const w = windowHistory(real52, '30D');
    expect(w[w.length - 1].date).toBe('2026-11-19');
    expect(w[0].date >= '2026-10-20').toBe(true);
    expect(w.every((p) => p.date >= '2026-10-20')).toBe(true);
    expect(real52.filter((p) => p.date >= '2026-10-20').length).toBe(w.length);
  });
  it('90D and 1Y cover all 52, All returns everything', () => {
    expect(windowHistory(real52, '90D')).toHaveLength(real52.length);
    expect(windowHistory(real52, '1Y')).toHaveLength(real52.length);
    expect(windowHistory(real52, 'All')).toHaveLength(real52.length);
  });
  it('is anchored on the newest snapshot, not today', () => {
    const w = windowHistory([pt('2020-01-01'), pt('2020-01-31'), pt('2020-02-15')], '30D');
    expect(w.map((p) => p.date)).toEqual(['2020-01-31', '2020-02-15']);
  });
  it('handles n = 0, 1, 2', () => {
    expect(windowHistory([], '30D')).toEqual([]);
    expect(windowHistory([], 'All')).toEqual([]);
    expect(windowHistory([pt('2026-01-05')], '30D')).toHaveLength(1);
    expect(windowHistory([pt('2026-01-05'), pt('2026-01-06')], '30D')).toHaveLength(2);
  });
  it('sorts unsorted input and keeps the last of duplicate dates', () => {
    const w = windowHistory([pt('2026-01-06', 10), pt('2026-01-05', 20), pt('2026-01-06', 99)], 'All');
    expect(w.map((p) => p.date)).toEqual(['2026-01-05', '2026-01-06']);
    expect(w[1].investability_score).toBe(99);
  });
  it('does not mutate its input', () => {
    const input = [pt('2026-01-06'), pt('2026-01-05')];
    normalizeHistory(input);
    expect(input[0].date).toBe('2026-01-06');
  });
});

describe('weeklyDownsample', () => {
  it('keeps the last snapshot of each ISO week, ascending', () => {
    const out = weeklyDownsample([
      pt('2026-11-16'), pt('2026-11-17'), pt('2026-11-20'), // Mon-Fri week 47
      pt('2026-11-23'), pt('2026-11-25'),
    ]);
    expect(out.map((p) => p.date)).toEqual(['2026-11-20', '2026-11-25']);
  });
  it('is stable across year boundaries (ISO week-year)', () => {
    expect(isoWeekKey('2026-12-31')).toBe('2026-W53');
    expect(isoWeekKey('2027-01-01')).toBe('2026-W53'); // same ISO week as 2026-12-31
    expect(isoWeekKey('2027-01-04')).toBe('2027-W01');
    expect(isoWeekKey('2024-12-30')).toBe('2025-W01');
    const out = weeklyDownsample([pt('2026-12-30'), pt('2026-12-31'), pt('2027-01-01'), pt('2027-01-04')]);
    expect(out.map((p) => p.date)).toEqual(['2027-01-01', '2027-01-04']);
  });
  it('sorts unsorted input, dedupes dates, handles empty and single', () => {
    expect(weeklyDownsample([])).toEqual([]);
    expect(weeklyDownsample([pt('2026-01-05')])).toHaveLength(1);
    const out = weeklyDownsample([pt('2026-01-13'), pt('2026-01-05'), pt('2026-01-09'), pt('2026-01-09', 77)]);
    expect(out.map((p) => p.date)).toEqual(['2026-01-09', '2026-01-13']);
  });
  it('reduces ~2 years of weekday data to about 105 weekly bars', () => {
    const out = weeklyDownsample(weekdays('2026-11-19', 520));
    expect(out.length).toBeGreaterThanOrEqual(104);
    expect(out.length).toBeLessThanOrEqual(106);
  });
});

describe('historyView / heading', () => {
  it('uses daily bars up to 120 points and weekly beyond', () => {
    expect(historyView(weekdays('2026-11-19', 120), 'All').weekly).toBe(false);
    const big = historyView(weekdays('2026-11-19', 260), 'All');
    expect(big.weekly).toBe(true);
    expect(big.window).toHaveLength(260);
    expect(big.bars.length).toBeLessThan(60);
  });
  it('heading reflects the visible window', () => {
    const v = historyView(real52, '30D');
    expect(historyHeading(v)).toBe(`Score History (${v.window.length} snapshots, since ${v.window[0].date})`);
    expect(historyHeading(historyView(real52, 'All'))).toBe('Score History (52 snapshots, since 2026-09-09)');
  });
  it('heading marks weekly bars', () => {
    expect(historyHeading(historyView(weekdays('2026-11-19', 260), 'All'))).toContain('weekly bars');
  });
});

describe('availableRanges', () => {
  it('52 snapshots over ~10 weeks: only 30D and 90D (1Y and All repeat 90D)', () => {
    expect(availableRanges(real52)).toEqual({ '30D': true, '90D': true, '1Y': false, All: false });
  });
  it('short history: only 30D', () => {
    expect(availableRanges([pt('2026-11-18'), pt('2026-11-19')])).toEqual({ '30D': true, '90D': false, '1Y': false, All: false });
  });
  it('long history enables everything', () => {
    expect(availableRanges(weekdays('2026-11-19', 600))).toEqual({ '30D': true, '90D': true, '1Y': true, All: true });
  });
  it('empty history keeps 30D only', () => {
    expect(availableRanges([])).toEqual({ '30D': true, '90D': false, '1Y': false, All: false });
  });
});

describe('labelIndexes / barGeometry', () => {
  it('labels first, middle, last without duplicates', () => {
    expect(labelIndexes(0)).toEqual([]);
    expect(labelIndexes(1)).toEqual([0]);
    expect(labelIndexes(2)).toEqual([0, 1]);
    expect(labelIndexes(5)).toEqual([0, 2, 4]);
    expect(labelIndexes(52)).toEqual([0, 25, 51]);
  });
  it.each([1, 2, 30, 52, 260, 1000])('bars never exceed the viewBox width (n=%i)', (n) => {
    const { slot, gap, barWidth } = barGeometry(n, 340);
    expect(barWidth).toBeGreaterThan(0);
    expect(barWidth * n + gap * (n - 1)).toBeLessThanOrEqual(340 + 1e-9);
    expect((n - 1) * slot + barWidth).toBeLessThanOrEqual(340 + 1e-9);
  });
});
