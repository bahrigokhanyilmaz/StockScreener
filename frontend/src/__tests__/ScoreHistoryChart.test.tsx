import { describe, expect, it } from 'vitest';
import { renderToString } from 'react-dom/server';
import ScoreHistoryChart, { CHART_WIDTH } from '../components/ScoreHistoryChart.tsx';
import type { ScoreHistoryPoint } from '../api.ts';

function series(n: number): ScoreHistoryPoint[] {
  const out: ScoreHistoryPoint[] = [];
  const d = new Date('2026-11-19T00:00:00Z');
  while (out.length < n) {
    if (d.getUTCDay() % 6 !== 0) {
      out.unshift({ date: d.toISOString().slice(0, 10), investability_score: 30 + (out.length * 7) % 60, fundamental_score: null, sentiment_score: null, price: null });
    }
    d.setUTCDate(d.getUTCDate() - 1);
  }
  return out;
}

const rects = (html: string) => [...html.matchAll(/<rect [^>]*>/g)].map((m) => m[0]);
const attr = (tag: string, name: string) => Number(new RegExp(`${name}="([^"]+)"`).exec(tag)![1]);

describe('ScoreHistoryChart render', () => {
  it('renders one rect per visible point with a viewBox, no pixel min-width, and title text', () => {
    const html = renderToString(<ScoreHistoryChart history={series(52)} initialRange="All" />);
    expect(rects(html)).toHaveLength(52);
    expect(html).toContain(`viewBox="0 0 ${CHART_WIDTH} 80"`);
    expect(html).not.toMatch(/min-width/);
    expect(html).toMatch(/<title>\d{4}-\d{2}-\d{2}: \d+<\/title>/);
    expect(html).toContain('Score History (52 snapshots, since ');
  });
  it('default range is 30D and shows fewer bars than All', () => {
    const html = renderToString(<ScoreHistoryChart history={series(52)} />);
    expect(rects(html).length).toBeLessThan(30);
    expect(rects(html).length).toBeGreaterThan(15);
  });
  it.each([2, 52, 260, 1000])('every bar stays inside the viewBox (n=%i)', (n) => {
    const html = renderToString(<ScoreHistoryChart history={series(n)} initialRange="All" />);
    const rs = rects(html);
    expect(rs.length).toBeGreaterThan(0);
    for (const r of rs) expect(attr(r, 'x') + attr(r, 'width')).toBeLessThanOrEqual(CHART_WIDTH + 1e-6);
  });
  it('renders three date labels at most', () => {
    const html = renderToString(<ScoreHistoryChart history={series(52)} initialRange="All" />);
    const labels = html.match(/<div class="history-labels">(.*?)<\/div>/)![1];
    expect(labels.match(/<span>/g)).toHaveLength(3);
  });
});
