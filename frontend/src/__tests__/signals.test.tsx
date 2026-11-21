import { describe, expect, it } from 'vitest';
import { renderToString } from 'react-dom/server';
import SignalsCell from '../components/SignalsCell.tsx';
import { activeSignalCount, buildSignalSlots } from '../utils/signals.ts';
import type { Stock } from '../api.ts';

const div = (tier: 'strong' | 'mild') => ({
  flag: true, tier, return_3d: -0.123, return_1d: null, volume_ratio: null, sentiment: 0.5, as_of: '2026-01-02', trigger_date: '2026-01-02',
});
const neg = { verdict: 'negative', event_date: '2026-01-01', guidance_vs_prior: 'lowered', guidance_vs_consensus: 'below',
  margin_outlook: 'down', demand_outlook: 'not_stated', one_time_or_pull_forward_flag: false } as never;
const mk = (extra: object) => extra as Stock;

describe('buildSignalSlots', () => {
  it('returns fixed [div, guidance] order with nulls for absent signals', () => {
    expect(buildSignalSlots(mk({}))).toEqual([null, null]);
    const [d, g] = buildSignalSlots(mk({ news_price_divergence: div('mild'), guidance_signal: neg }));
    expect(d).toMatchObject({ key: 'div', tier: 'mild' });
    expect(d && d.tooltip).toContain('12.3%');
    expect(g).toMatchObject({ key: 'guidance', label: 'G▼' });
  });
  it('ignores non-negative guidance and counts active signals', () => {
    const s = mk({ guidance_signal: { ...(neg as object), verdict: 'neutral' } });
    expect(buildSignalSlots(s)).toEqual([null, null]);
    expect(activeSignalCount(mk({ news_price_divergence: div('strong'), guidance_signal: neg }))).toBe(2);
    expect(activeSignalCount(mk({ guidance_signal: neg }))).toBe(1);
  });
});

describe('SignalsCell markup', () => {
  it('renders an empty left-aligned row when there is no signal', () => {
    const html = renderToString(<SignalsCell stock={mk({})} />);
    expect(html).toContain('signals-row');
    expect(html).not.toContain('divergence-badge');
    expect(html).not.toContain('guidance-marker');
  });
  it('renders each badge directly in the row, with no reserved slots', () => {
    const onlyG = renderToString(<SignalsCell stock={mk({ guidance_signal: neg })} />);
    expect(onlyG).toContain('guidance-marker');
    expect(onlyG).not.toContain('divergence-badge');
    expect(onlyG).not.toContain('signal-slot');
    const onlyD = renderToString(<SignalsCell stock={mk({ news_price_divergence: div('mild') })} />);
    expect(onlyD).toContain('divergence-mild');
    expect(onlyD).not.toContain('guidance-marker');
  });
  it('puts DIV before G▼ when both are present', () => {
    const both = renderToString(<SignalsCell stock={mk({ news_price_divergence: div('strong'), guidance_signal: neg })} />);
    expect(both.indexOf('divergence-badge')).toBeLessThan(both.indexOf('guidance-marker'));
  });
});
