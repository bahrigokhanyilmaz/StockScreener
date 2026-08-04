import { useState } from 'react';

/**
 * FilterSliders Component
 *
 * Two types of client-side filters:
 * 1. Sliders — always active, mirror pipeline hard filters, can only tighten
 * 2. Toggles — off by default, opt-in restrictions for analyst/growth data
 *
 * All filtering is client-side (no API calls). Stocks already passed the pipeline.
 */

// Slider filters: always active, match pipeline hard filter thresholds
const SLIDER_CONFIG = [
  { key: 'peg_ratio', label: 'PEG Ratio', type: 'max', default: 1.0, min: 0.1, max: 1.0, step: 0.1, format: 'ratio' },
  { key: 'price_to_fcf', label: 'Price / FCF', type: 'max', default: 20, min: 5, max: 20, step: 1, format: 'ratio' },
  { key: 'debt_to_equity', label: 'Debt / Equity', type: 'max', default: 1.0, min: 0.0, max: 1.0, step: 0.1, format: 'ratio' },
  { key: 'quick_ratio', label: 'Quick Ratio', type: 'min', default: 1.0, min: 1.0, max: 5.0, step: 0.1, format: 'ratio' },
  { key: 'revenue_growth_yoy', label: 'Revenue Growth %', type: 'min', default: 0, min: 0, max: 100, step: 1, format: 'percent' },
];

// Toggle filters: off by default, opt-in
const TOGGLE_CONFIG = [
  { key: 'toggle_analyst_rec', field: 'analyst_recommendation', type: 'max', threshold: 3.0, label: 'Analyst Rec ≤ 3.0 (Hold or better)' },
  { key: 'toggle_target_upside', field: 'target_price_upside', type: 'min', threshold: 0.20, label: 'Target Upside ≥ 20%' },
  { key: 'toggle_lt_growth', field: 'est_lt_growth', type: 'min', threshold: 0.0, label: 'EPS Growth 5Y > 0%' },
  { key: 'toggle_pos_margin', field: 'operating_margin', type: 'min', threshold: 0.0, label: 'Positive Operating Margin' },
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

export function applyFilters(stocks: Record<string, unknown>[], filters: FilterValues): Record<string, unknown>[] {
  return stocks.filter(stock => {
    // Apply slider filters
    for (const config of SLIDER_CONFIG) {
      const value = stock[config.key] as number | null | undefined;
      if (value === null || value === undefined) continue; // Skip if no data

      const threshold = filters[config.key] as number;
      const effectiveThreshold = config.format === 'percent' ? threshold / 100 : threshold;

      if (config.type === 'max' && value > effectiveThreshold) {
        // D/E override: skip if Interest Coverage Ratio > 3.0
        if (config.key === 'debt_to_equity') {
          const icr = stock['interest_coverage_ratio'] as number | null | undefined;
          if (icr !== null && icr !== undefined && icr > 3.0) continue;
        }
        return false;
      }
      if (config.type === 'min' && value < effectiveThreshold) {
        // Operating margin override: skip if revenue growth > 20%
        if (config.key === 'operating_margin') {
          const revG = stock['revenue_growth_yoy'] as number | null | undefined;
          if (revG !== null && revG !== undefined && revG > 0.20) continue;
        }
        return false;
      }
    }

    // Apply toggle filters (only when toggled ON)
    for (const toggle of TOGGLE_CONFIG) {
      if (!filters[toggle.key]) continue; // Toggle is off — don't filter

      const value = stock[toggle.field] as number | null | undefined;
      // Toggle filters EXCLUDE stocks with null data (that's the point —
      // "only show stocks WITH analyst coverage that meets threshold")
      if (value === null || value === undefined) return false;

      if (toggle.type === 'max' && value > toggle.threshold) return false;
      if (toggle.type === 'min' && value < toggle.threshold) return false;
    }

    return true;
  });
}

export default function FilterSliders({ filters, onChange, onReset, matchCount, totalCount }: Props) {
  const [collapsed, setCollapsed] = useState(true);

  function handleSliderChange(key: string, value: number) {
    onChange({ ...filters, [key]: value });
  }

  function handleToggleChange(key: string) {
    onChange({ ...filters, [key]: !filters[key] });
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
          {SLIDER_CONFIG.map(config => {
            const value = filters[config.key] as number;
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

          <div className="toggle-section">
            <h4>Optional Restrictions</h4>
            {TOGGLE_CONFIG.map(toggle => (
              <label key={toggle.key} className={`toggle-row ${filters[toggle.key] ? 'active' : ''}`}>
                <input
                  type="checkbox"
                  checked={!!filters[toggle.key]}
                  onChange={() => handleToggleChange(toggle.key)}
                />
                <span>{toggle.label}</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
