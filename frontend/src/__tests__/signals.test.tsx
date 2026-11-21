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
  const slots = (html: string) => (html.match(/class="signal-slot"/g) || []).length;
  it('always renders both slots in order, empty when no signal', () => {
    const html = renderToString(<SignalsCell stock={mk({})} />);
    expect(slots(html)).toBe(2);
    expect(html).not.toContain('divergence-badge');
    expect(html).not.toContain('guidance-marker');
    expect(html.indexOf('data-slot="div"')).toBeLessThan(html.indexOf('data-slot="guidance"'));
  });
  it('places each badge in its own slot', () => {
    const onlyG = renderToString(<SignalsCell stock={mk({ guidance_signal: neg })} />);
    expect(slots(onlyG)).toBe(2);
    expect(onlyG.indexOf('guidance-marker')).toBeGreaterThan(onlyG.indexOf('data-slot="guidance"'));
    expect(onlyG).not.toContain('divergence-badge');
    const both = renderToString(<SignalsCell stock={mk({ news_price_divergence: div('strong'), guidance_signal: neg })} />);
    expect(slots(both)).toBe(2);
    expect(both).toContain('divergence-strong');
    expect(both.indexOf('divergence-badge')).toBeLessThan(both.indexOf('data-slot="guidance"'));
  });
});
