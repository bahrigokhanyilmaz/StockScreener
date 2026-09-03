import { useState } from 'react';
import type { IndustryAverages } from '../api.ts';

/**
 * FilterSliders Component
 *
 * Collapsible filter bar with two sections:
 * 1. Sliders — tighten pipeline thresholds (2-column grid when expanded)
 * 2. Toggles — opt-in restrictions (horizontal pill buttons)
 */

const SLIDER_CONFIG = [
  { key: 'peg_ratio', label: 'PEG', type: 'max', default: 1.0, min: 0.1, max: 1.0, step: 0.1, format: 'ratio' },
  { key: 'price_to_fcf', label: 'P/FCF', type: 'max', default: 20, min: 5, max: 20, step: 1, format: 'ratio' },
  { key: 'debt_to_equity', label: 'D/E', type: 'max', default: 1.0, min: 0.0, max: 1.0, step: 0.1, format: 'ratio' },
  { key: 'quick_ratio', label: 'QR', type: 'min', default: 1.0, min: 1.0, max: 5.0, step: 0.1, format: 'ratio' },
  { key: 'est_lt_revenue_growth', label: 'Fwd Rev Growth', type: 'min', default: 0, min: 0, max: 100, step: 1, format: 'percent' },
];

const TOGGLE_CONFIG = [
  { key: 'toggle_pe_q1', field: 'pe_ratio', type: 'industry_q1', threshold: 0, label: 'P/E < Q1' },
  { key: 'toggle_analyst_rec', field: 'analyst_recommendation', type: 'max', threshold: 3.0, label: 'Hold+' },
  { key: 'toggle_target_upside', field: 'target_price_upside', type: 'min', threshold: 0.20, label: 'Target ≥20%' },
  { key: 'toggle_lt_growth', field: 'est_lt_growth', type: 'min', threshold: 0.0, label: 'EPS 5Y ↑' },
  { key: 'toggle_eps_growth', field: 'eps_growth_yoy', type: 'min', threshold: 0.0, label: 'EPS YoY ↑' },
  { key: 'toggle_pos_margin', field: 'operating_margin', type: 'min', threshold: 0.0, label: 'Op Margin +' },
];

export interface FilterValues {
  [key: string]: number | boolean;
}

interface Props {
  filters: FilterValues;
  onChange: (filters: FilterValues) => void;
  onReset: () => void;
  matchCount: number;
  totalCount: number;
}

export function getDefaultFilters(): FilterValues {
  const defaults: FilterValues = {};
  for (const f of SLIDER_CONFIG) {
    defaults[f.key] = f.default;
  }
  for (const t of TOGGLE_CONFIG) {
    defaults[t.key] = false;
  }
  return defaults;
}

export function applyFilters(stocks: Record<string, unknown>[], filters: FilterValues, industryAverages?: IndustryAverages): Record<string, unknown>[] {
  return stocks.filter(stock => {
    for (const config of SLIDER_CONFIG) {
      const value = stock[config.key] as number | null | undefined;
      if (value === null || value === undefined) continue;

      const threshold = filters[config.key] as number;
      const effectiveThreshold = config.format === 'percent' ? threshold / 100 : threshold;

      if (config.type === 'max' && value > effectiveThreshold) {
        if (config.key === 'debt_to_equity') {
          const icr = stock['interest_coverage_ratio'] as number | null | undefined;
          if (icr !== null && icr !== undefined && icr > 3.0) continue;
        }
        return false;
      }
      if (config.type === 'min' && value < effectiveThreshold) return false;
    }

    for (const toggle of TOGGLE_CONFIG) {
      if (!filters[toggle.key]) continue;

      // Special: P/E < industry 25th percentile
      if (toggle.type === 'industry_q1') {
        const pe = stock['pe_ratio'] as number | null | undefined;
        const sicIndustry = stock['sic_industry'] as string | undefined;
        if (!pe || !sicIndustry || !industryAverages) return false;
        const q1 = industryAverages[sicIndustry]?.pe_lower_quartile;
        if (q1 == null) return false;
        if (pe > q1) return false;
        continue;
      }

      const value = stock[toggle.field] as number | null | undefined;
      if (value === null || value === undefined) return false;
      if (toggle.type === 'max' && value > toggle.threshold) return false;
      if (toggle.type === 'min' && value < toggle.threshold) return false;
    }

    return true;
  });
}

export default function FilterSliders({ filters, onChange, onReset, matchCount, totalCount }: Props) {
  const [expanded, setExpanded] = useState(false);

  const activeToggles = TOGGLE_CONFIG.filter(t => filters[t.key]);
  const modifiedSliders = SLIDER_CONFIG.filter(s => filters[s.key] !== s.default);
  const hasModifications = activeToggles.length > 0 || modifiedSliders.length > 0;

  return (
    <div className="filter-panel">
      <div className="filter-bar">
        <div className="filter-bar-left">
          <button className="filter-expand-btn" onClick={() => setExpanded(!expanded)}>
            {expanded ? '▲' : '▼'} Filters
          </button>
          <span className="match-count">{matchCount} / {totalCount}</span>
          {activeToggles.map(t => (
            <span key={t.key} className="active-pill" onClick={() => onChange({ ...filters, [t.key]: false })}>
              {t.label} ✕
            </span>
          ))}
        </div>
        {hasModifications && (
          <button className="btn-reset-small" onClick={onReset}>Reset</button>
        )}
      </div>

      {expanded && (
        <div className="filter-expanded">
          <div className="filter-sliders-grid">
            {SLIDER_CONFIG.map(config => {
              const value = filters[config.key] as number;
              return (
                <div key={config.key} className="slider-compact">
                  <div className="slider-compact-header">
                    <span className="slider-compact-label">{config.label}</span>
                    <span className="slider-compact-value">
                      {config.type === 'max' ? '<' : '>'}{value}{config.format === 'percent' ? '%' : ''}
                    </span>
                  </div>
                  <input
                    type="range"
                    min={config.min}
                    max={config.max}
                    step={config.step}
                    value={value}
                    onChange={(e) => onChange({ ...filters, [config.key]: parseFloat(e.target.value) })}
                    className="slider-input-compact"
                  />
                </div>
              );
            })}
          </div>
          <div className="filter-toggles-row">
            {TOGGLE_CONFIG.map(toggle => (
              <button
                key={toggle.key}
                className={`toggle-pill ${filters[toggle.key] ? 'on' : ''}`}
                onClick={() => onChange({ ...filters, [toggle.key]: !filters[toggle.key] })}
              >
                {toggle.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
