import type { GuidanceSignal, NewsArticle, NewsPriceDivergence } from '../api.ts';

/** Tooltip/banner text for the price-vs-news divergence flag. */
export function divergenceMessage(d: NewsPriceDivergence): string {
  const drop = (Math.abs(d.return_3d) * 100).toFixed(1);
  return `Price fell ${drop}% in 3 days while news is positive: check why`;
}

export function isNegativeGuidance(g: GuidanceSignal | null | undefined): boolean {
  return g?.verdict === 'negative';
}

function words(value: string): string {
  return value.replace(/_/g, ' ');
}

/** One line per component that the text actually stated (plus the pull-forward flag when true). */
export function guidanceLines(g: GuidanceSignal): { label: string; value: string; bad: boolean }[] {
  const rows: { label: string; value: string; bad: boolean }[] = [
    { label: 'Guidance vs prior', value: g.guidance_vs_prior, bad: g.guidance_vs_prior === 'lowered' },
    { label: 'Guidance vs consensus', value: g.guidance_vs_consensus, bad: g.guidance_vs_consensus === 'below' },
    { label: 'Margin outlook', value: g.margin_outlook, bad: g.margin_outlook === 'down' },
    { label: 'Demand outlook', value: g.demand_outlook, bad: g.demand_outlook === 'down' },
  ];
  const lines = rows.map(r => ({ ...r, value: words(r.value) }));
  lines.push({
    label: 'Pull-forward / one-time',
    value: g.one_time_or_pull_forward_flag ? 'yes' : 'not stated',
    bad: g.one_time_or_pull_forward_flag,
  });
  return lines;
}

/** Compact table tooltip: the components that count against the stock. */
export function guidanceTooltip(g: GuidanceSignal): string {
  const bad = guidanceLines(g).filter(l => l.bad).map(l => `${l.label}: ${l.value}`);
  const head = `Earnings guidance ${g.event_date}: ${g.verdict}`;
  return bad.length ? `${head}\n${bad.join('\n')}` : head;
}

/** Whole days between an ISO date (YYYY-MM-DD) and today, never negative. */
export function daysSince(isoDate: string, now: number = Date.now()): number {
  const ms = new Date(`${isoDate}T00:00:00Z`).getTime();
  return Number.isNaN(ms) ? 0 : Math.max(0, Math.floor((now - ms) / 86400000));
}

/** Stored articles flagged scored=false (or, for legacy items, relevant=false) did not count toward sentiment. */
export function isArticleScored(a: NewsArticle): boolean {
  if (a.scored !== undefined) return a.scored;
  return a.relevant !== false;
}
