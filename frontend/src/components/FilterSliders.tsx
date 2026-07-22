import { useState } from 'react';

/**
 * FilterSliders Component
 *
 * Lets you adjust KPI thresholds with sliders and instantly re-filter
 * the stock table. This is client-side exploration — it re-filters the
 * data already loaded from the API, no additional API calls needed.
 *
 * The slider config comes from screener-filters.json (same source of truth
 * the backend uses). When you move a slider, the parent re-filters stocks.
 */

// Filter definitions matching shared/config/screener-filters.json
// Slider ranges capped at pipeline defaults — you can only tighten, not loosen
// (loosening would show no additional stocks since the pipeline already filtered them)
const FILTER_CONFIG = [
  { key: 'peg_ratio', label: 'PEG Ratio', type: 'max', default: 1.0, min: 0.1, max: 1.0, step: 0.1, format: 'ratio' },
  { key: 'price_to_fcf', label: 'Price / FCF', type: 'max', default: 20, min: 5, max: 20, step: 1, format: 'ratio' },
  { key: 'debt_to_equity', label: 'Debt / Equity', type: 'max', default: 1.0, min: 0.0, max: 1.0, step: 0.1, format: 'ratio' },
  { key: 'quick_ratio', label: 'Quick Ratio', type: 'min', default: 1.0, min: 1.0, max: 5.0, step: 0.1, format: 'ratio' },
  { key: 'operating_margin', label: 'Operating Margin %', type: 'min', default: 0, min: 0, max: 50, step: 1, format: 'percent' },
  { key: 'eps_growth_yoy', label: 'EPS Growth %', type: 'min', default: 0, min: 0, max: 100, step: 1, format: 'percent' },
  { key: 'revenue_growth_yoy', label: 'Revenue Growth %', type: 'min', default: 0, min: 0, max: 100, step: 1, format: 'percent' },
  { key: 'est_lt_growth', label: 'LT Growth %', type: 'min', default: 0, min: 0, max: 50, step: 1, format: 'percent' },
  { key: 'target_price_upside', label: 'Target Upside %', type: 'min', default: 20, min: 20, max: 100, step: 5, format: 'percent' },
];

export interface FilterValues {
  [key: string]: number;
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
  for (const f of FILTER_CONFIG) {
    defaults[f.key] = f.default;
  }
  return defaults;
}

export function applyFilters(stocks: Record<string, unknown>[], filters: FilterValues): Record<string, unknown>[] {
  return stocks.filter(stock => {
    for (const config of FILTER_CONFIG) {
      const value = stock[config.key] as number | null | undefined;
      if (value === null || value === undefined) continue; // Skip if no data

      const threshold = filters[config.key];
      // Convert percent filters: slider shows 20 (meaning 20%), data stores 0.20
      const effectiveThreshold = config.format === 'percent' ? threshold / 100 : threshold;

      if (config.type === 'max' && value > effectiveThreshold) {
        // D/E override: skip if Interest Coverage Ratio > 3.0 (debt is serviceable)
        if (config.key === 'debt_to_equity') {
          const icr = stock['interest_coverage_ratio'] as number | null | undefined;
          if (icr !== null && icr !== undefined && icr > 3.0) continue; // override
        }
        return false;
      }
      if (config.type === 'min' && value < effectiveThreshold) return false;
    }
    return true;
  });
}

export default function FilterSliders({ filters, onChange, onReset, matchCount, totalCount }: Props) {
  const [collapsed, setCollapsed] = useState(true);

  function handleSliderChange(key: string, value: number) {
    onChange({ ...filters, [key]: value });
  }

  return (
    <div className="filter-panel">
      <div className="filter-header">
        <div className="filter-title-row">
          <h3>Filters</h3>
          <span className="match-count">{matchCount} / {totalCount} match</span>
        </div>
        <div className="filter-actions">
          <button className="btn-reset" onClick={onReset}>Reset</button>
          <button className="btn-collapse" onClick={() => setCollapsed(!collapsed)}>
            {collapsed ? '▼ Expand' : '▲ Collapse'}
          </button>
        </div>
      </div>

      {!collapsed && (
        <div className="filter-sliders">
          {FILTER_CONFIG.map(config => {
            const value = filters[config.key];
            const isDefault = value === config.default;

            return (
              <div key={config.key} className={`slider-row ${isDefault ? '' : 'modified'}`}>
                <div className="slider-label-row">
                  <label>{config.label}</label>
                  <span className="slider-value">
                    {config.type === 'max' ? '< ' : '> '}
                    {value}{config.format === 'percent' ? '%' : ''}
                  </span>
                </div>
                <input
                  type="range"
                  min={config.min}
                  max={config.max}
                  step={config.step}
                  value={value}
                  onChange={(e) => handleSliderChange(config.key, parseFloat(e.target.value))}
                  className="slider-input"
                />
                {config.key === 'debt_to_equity' && (
                  <span className="override-note">or ICR &gt; 3.0x</span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
