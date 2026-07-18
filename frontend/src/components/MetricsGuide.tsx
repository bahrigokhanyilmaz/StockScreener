import type { Stock } from '../api.ts';

/**
 * MetricsGuide Component
 *
 * Two sections:
 * 1. Metric definitions with interpretation of movement in either direction
 * 2. Industry average comparison for the selected stock's industry
 *
 * Displayed as a tab in the detail panel when a stock is selected.
 */

interface Props {
  stock: Stock;
}

// ==========================================
// METRIC DEFINITIONS & INTERPRETATION
// ==========================================

interface MetricDefinition {
  key: string;
  name: string;
  shortName: string;
  definition: string;
  formula: string;
  threshold: string;
  increasing: string;
  decreasing: string;
  category: 'valuation' | 'financial_health' | 'growth' | 'sentiment';
}

const METRIC_DEFINITIONS: MetricDefinition[] = [
  {
    key: 'pe_ratio',
    name: 'Price-to-Earnings Ratio (P/E)',
    shortName: 'P/E',
    definition: 'How much investors pay per dollar of earnings. Measures whether a stock is overvalued or undervalued relative to its profits.',
    formula: 'Stock Price / Earnings Per Share (trailing 12 months)',
    threshold: '< 50 (our filter)',
    increasing: 'Stock becoming more expensive relative to earnings. Could signal overvaluation, or that investors expect accelerating future growth.',
    decreasing: 'Stock becoming cheaper relative to earnings. Could signal a buying opportunity (undervalued), or that the market expects declining earnings ahead.',
    category: 'valuation',
  },
  {
    key: 'forward_pe',
    name: 'Forward Price-to-Earnings (Fwd P/E)',
    shortName: 'Fwd P/E',
    definition: 'P/E calculated using analyst-estimated future earnings rather than trailing. Shows what the market expects the company to earn next year.',
    formula: 'Stock Price / Estimated Next-Year EPS',
    threshold: '< 20 (our filter)',
    increasing: 'Analysts are revising earnings estimates down, or the stock price is rising faster than expected earnings growth. Suggests overvaluation risk.',
    decreasing: 'Analysts expect stronger future earnings, or the stock price hasn\'t caught up to improving estimates. Signals potential undervaluation.',
    category: 'valuation',
  },
  {
    key: 'peg_ratio',
    name: 'Price/Earnings-to-Growth (PEG)',
    shortName: 'PEG',
    definition: 'Adjusts P/E for growth rate. A PEG of 1.0 means you\'re paying a "fair" price for the growth. Below 1.0 suggests you\'re getting growth at a discount.',
    formula: 'P/E Ratio / Annual EPS Growth Rate (%)',
    threshold: '< 1.0 (our filter)',
    increasing: 'Growth is slowing relative to valuation. Either P/E is expanding or EPS growth is decelerating. Less attractive for value investors.',
    decreasing: 'Growth is accelerating relative to valuation. The stock is offering more growth per dollar of price — classic value-growth sweet spot.',
    category: 'valuation',
  },
  {
    key: 'price_to_fcf',
    name: 'Price-to-Free Cash Flow (P/FCF)',
    shortName: 'P/FCF',
    definition: 'How much you pay per dollar of actual cash the business generates after capital expenditures. More reliable than P/E since cash flow is harder to manipulate than earnings.',
    formula: 'Stock Price / (Operating Cash Flow - Capital Expenditures) per Share',
    threshold: '< 20 (our filter)',
    increasing: 'Cash generation declining relative to price. Could mean heavy capex investment (good long-term) or deteriorating cash conversion (bad).',
    decreasing: 'Cash generation improving relative to price. The business is throwing off more cash per dollar of stock price — strong buy signal for value investors.',
    category: 'valuation',
  },
  {
    key: 'debt_to_equity',
    name: 'Debt-to-Equity Ratio (D/E)',
    shortName: 'D/E',
    definition: 'Total debt divided by shareholder equity. Measures financial leverage. Lower means the company relies less on borrowed money to fund operations.',
    formula: 'Total Liabilities / Shareholder Equity',
    threshold: '< 1.0 (our filter)',
    increasing: 'Company is taking on more debt relative to equity. Could signal aggressive expansion, share buybacks financed by debt, or deteriorating fundamentals forcing borrowing.',
    decreasing: 'Company is paying down debt or growing equity through retained earnings. Signals financial strengthening and lower bankruptcy risk.',
    category: 'financial_health',
  },
  {
    key: 'quick_ratio',
    name: 'Quick Ratio (Acid Test)',
    shortName: 'Quick Ratio',
    definition: 'Can the company pay all its short-term obligations with its most liquid assets (cash + receivables)? Excludes inventory which may be hard to sell quickly.',
    formula: '(Cash + Short-term Investments + Receivables) / Current Liabilities',
    threshold: '> 1.0 (our filter)',
    increasing: 'Liquidity improving. Company has more cash buffer to handle unexpected expenses or downturns. Reduces short-term risk.',
    decreasing: 'Liquidity tightening. Could signal heavy spending, slowing collections, or growing short-term debts. Below 1.0 is a red flag for potential cash crunch.',
    category: 'financial_health',
  },
  {
    key: 'operating_margin',
    name: 'Operating Margin',
    shortName: 'Op Margin',
    definition: 'What percentage of revenue becomes operating profit after paying for cost of goods and operating expenses (but before interest and taxes). Measures core business profitability.',
    formula: 'Operating Income / Revenue',
    threshold: '> 0% (our filter)',
    increasing: 'Business becoming more efficient. Could signal pricing power, cost reductions, or economies of scale. Sustainable margin expansion is very bullish.',
    decreasing: 'Profitability eroding. Could signal rising costs, competitive pressure, or heavy investment in growth. Temporary dips for expansion are OK; persistent declines are concerning.',
    category: 'financial_health',
  },
  {
    key: 'eps_growth_yoy',
    name: 'EPS Growth (Year-over-Year)',
    shortName: 'EPS Growth',
    definition: 'How much earnings per share grew compared to the same period last year. The fundamental driver of stock price appreciation over time.',
    formula: '(Current EPS - Prior Year EPS) / Prior Year EPS',
    threshold: '> 0% (our filter)',
    increasing: 'Accelerating earnings growth. Strongest signal for stock price appreciation. Market typically rewards accelerating earnings with multiple expansion.',
    decreasing: 'Earnings growth slowing or turning negative. Stock may face downward pressure. Persistent negative growth often precedes significant price declines.',
    category: 'growth',
  },
  {
    key: 'revenue_growth_yoy',
    name: 'Revenue Growth (Year-over-Year)',
    shortName: 'Rev Growth',
    definition: 'How much the company\'s top-line sales grew compared to last year. Revenue is harder to manipulate than earnings and shows true demand for products/services.',
    formula: '(Current Revenue - Prior Year Revenue) / Prior Year Revenue',
    threshold: '> 0% (our filter)',
    increasing: 'Demand accelerating. Company is gaining market share or expanding into new markets. Combined with stable margins, this drives sustainable growth.',
    decreasing: 'Demand slowing. Could signal market saturation, competitive losses, or macro headwinds. Negative revenue growth while costs are fixed compresses margins fast.',
    category: 'growth',
  },
  {
    key: 'est_lt_growth',
    name: 'Estimated Long-Term Growth',
    shortName: 'LT Growth',
    definition: 'Analyst consensus estimate of the company\'s earnings growth rate over the next 3-5 years. Forward-looking, based on industry analysis and company guidance.',
    formula: 'Analyst consensus 3-5 year EPS growth estimate',
    threshold: '> 0% (our filter)',
    increasing: 'Analysts becoming more optimistic about future growth. Positive revisions often precede stock price gains.',
    decreasing: 'Analysts cutting growth forecasts. Negative revisions signal deteriorating business outlook and often lead to stock price declines.',
    category: 'growth',
  },
  {
    key: 'analyst_recommendation',
    name: 'Analyst Recommendation',
    shortName: 'Analyst Rec',
    definition: 'Average analyst consensus rating. Scale: 1 = Strong Buy, 2 = Buy, 3 = Hold, 4 = Sell, 5 = Strong Sell. We require Hold or better (score <= 3.0).',
    formula: 'Weighted average of analyst ratings (Strong Buy × 1 + Buy × 2 + Hold × 3 + Sell × 4 + Strong Sell × 5) / Total Analysts',
    threshold: '<= 3.0 (Hold or better)',
    increasing: 'Analysts downgrading. Moving from Buy toward Hold/Sell. Suggests consensus is becoming less bullish on the stock.',
    decreasing: 'Analysts upgrading. Moving from Hold toward Buy/Strong Buy. Signals growing institutional conviction in the stock.',
    category: 'sentiment',
  },
  {
    key: 'sentiment_score',
    name: 'News Sentiment Score',
    shortName: 'Sentiment',
    definition: 'AI-analyzed sentiment from recent news articles. Range: -1.0 (extremely negative) to +1.0 (extremely positive). Captures market perception and potential catalysts not yet in the numbers.',
    formula: 'Confidence-weighted average of Claude AI sentiment scores across recent articles',
    threshold: '> -0.3 (our filter)',
    increasing: 'News coverage becoming more positive. Could signal improving perception, upcoming catalysts, or resolution of previous concerns.',
    decreasing: 'News coverage becoming more negative. May precede price declines. Watch for risk flags (lawsuits, SEC investigations, management changes).',
    category: 'sentiment',
  },
];

// ==========================================
// INDUSTRY AVERAGES
// ==========================================

// Industry average data derived from broad market research.
// These represent typical values for healthy companies in each sector.
interface IndustryAverages {
  pe_ratio: number;
  forward_pe: number;
  peg_ratio: number;
  price_to_fcf: number;
  debt_to_equity: number;
  quick_ratio: number;
  operating_margin: number;
  eps_growth_yoy: number;
  revenue_growth_yoy: number;
}

const INDUSTRY_AVERAGES: Record<string, IndustryAverages> = {
  'Technology': {
    pe_ratio: 28, forward_pe: 24, peg_ratio: 1.5, price_to_fcf: 25,
    debt_to_equity: 0.45, quick_ratio: 2.5, operating_margin: 0.22,
    eps_growth_yoy: 0.15, revenue_growth_yoy: 0.12,
  },
  'Construction': {
    pe_ratio: 16, forward_pe: 14, peg_ratio: 1.1, price_to_fcf: 14,
    debt_to_equity: 0.7, quick_ratio: 1.3, operating_margin: 0.08,
    eps_growth_yoy: 0.10, revenue_growth_yoy: 0.08,
  },
  'Professional Services': {
    pe_ratio: 22, forward_pe: 19, peg_ratio: 1.3, price_to_fcf: 20,
    debt_to_equity: 0.5, quick_ratio: 1.8, operating_margin: 0.15,
    eps_growth_yoy: 0.12, revenue_growth_yoy: 0.10,
  },
  'Diversified Consumer Services': {
    pe_ratio: 20, forward_pe: 17, peg_ratio: 1.2, price_to_fcf: 16,
    debt_to_equity: 0.55, quick_ratio: 1.5, operating_margin: 0.12,
    eps_growth_yoy: 0.10, revenue_growth_yoy: 0.09,
  },
  'Packaging': {
    pe_ratio: 18, forward_pe: 15, peg_ratio: 1.4, price_to_fcf: 15,
    debt_to_equity: 0.8, quick_ratio: 1.2, operating_margin: 0.10,
    eps_growth_yoy: 0.07, revenue_growth_yoy: 0.05,
  },
  'Commercial Services & Supplies': {
    pe_ratio: 19, forward_pe: 16, peg_ratio: 1.3, price_to_fcf: 17,
    debt_to_equity: 0.6, quick_ratio: 1.4, operating_margin: 0.11,
    eps_growth_yoy: 0.09, revenue_growth_yoy: 0.07,
  },
  // Broad fallback for industries not explicitly listed
  'Healthcare': {
    pe_ratio: 24, forward_pe: 20, peg_ratio: 1.4, price_to_fcf: 22,
    debt_to_equity: 0.5, quick_ratio: 2.0, operating_margin: 0.18,
    eps_growth_yoy: 0.12, revenue_growth_yoy: 0.10,
  },
  'Financial Services': {
    pe_ratio: 14, forward_pe: 12, peg_ratio: 1.1, price_to_fcf: 12,
    debt_to_equity: 1.5, quick_ratio: 0.8, operating_margin: 0.30,
    eps_growth_yoy: 0.08, revenue_growth_yoy: 0.06,
  },
  'Consumer Cyclical': {
    pe_ratio: 20, forward_pe: 17, peg_ratio: 1.3, price_to_fcf: 18,
    debt_to_equity: 0.6, quick_ratio: 1.3, operating_margin: 0.10,
    eps_growth_yoy: 0.10, revenue_growth_yoy: 0.08,
  },
  'Industrials': {
    pe_ratio: 20, forward_pe: 17, peg_ratio: 1.4, price_to_fcf: 18,
    debt_to_equity: 0.65, quick_ratio: 1.4, operating_margin: 0.12,
    eps_growth_yoy: 0.09, revenue_growth_yoy: 0.07,
  },
  'Energy': {
    pe_ratio: 12, forward_pe: 10, peg_ratio: 0.9, price_to_fcf: 10,
    debt_to_equity: 0.45, quick_ratio: 1.2, operating_margin: 0.15,
    eps_growth_yoy: 0.05, revenue_growth_yoy: 0.04,
  },
  'Materials': {
    pe_ratio: 16, forward_pe: 14, peg_ratio: 1.2, price_to_fcf: 14,
    debt_to_equity: 0.55, quick_ratio: 1.5, operating_margin: 0.13,
    eps_growth_yoy: 0.08, revenue_growth_yoy: 0.06,
  },
  'Utilities': {
    pe_ratio: 18, forward_pe: 16, peg_ratio: 2.0, price_to_fcf: 20,
    debt_to_equity: 1.2, quick_ratio: 0.7, operating_margin: 0.25,
    eps_growth_yoy: 0.05, revenue_growth_yoy: 0.04,
  },
  'Real Estate': {
    pe_ratio: 30, forward_pe: 25, peg_ratio: 2.5, price_to_fcf: 28,
    debt_to_equity: 1.0, quick_ratio: 0.9, operating_margin: 0.30,
    eps_growth_yoy: 0.06, revenue_growth_yoy: 0.05,
  },
  'Communication Services': {
    pe_ratio: 22, forward_pe: 18, peg_ratio: 1.3, price_to_fcf: 20,
    debt_to_equity: 0.6, quick_ratio: 1.5, operating_margin: 0.20,
    eps_growth_yoy: 0.11, revenue_growth_yoy: 0.08,
  },
};

const DEFAULT_AVERAGES: IndustryAverages = {
  pe_ratio: 20, forward_pe: 17, peg_ratio: 1.3, price_to_fcf: 18,
  debt_to_equity: 0.6, quick_ratio: 1.5, operating_margin: 0.12,
  eps_growth_yoy: 0.10, revenue_growth_yoy: 0.07,
};

function getIndustryAverages(industry: string): IndustryAverages {
  if (INDUSTRY_AVERAGES[industry]) return INDUSTRY_AVERAGES[industry];
  // Try partial matching
  const lower = industry.toLowerCase();
  for (const [key, val] of Object.entries(INDUSTRY_AVERAGES)) {
    if (lower.includes(key.toLowerCase()) || key.toLowerCase().includes(lower)) {
      return val;
    }
  }
  return DEFAULT_AVERAGES;
}

function comparisonLabel(stockValue: number | null, industryAvg: number, type: 'max' | 'min'): { label: string; color: string } {
  if (stockValue === null || stockValue === undefined) return { label: '—', color: '#64748b' };
  const diff = ((stockValue - industryAvg) / Math.abs(industryAvg || 1)) * 100;

  if (type === 'max') {
    // Lower is better for max filters (P/E, D/E)
    if (diff <= -20) return { label: 'Well Below Avg', color: '#4ade80' };
    if (diff <= -5) return { label: 'Below Avg', color: '#86efac' };
    if (diff <= 5) return { label: 'At Avg', color: '#fbbf24' };
    if (diff <= 20) return { label: 'Above Avg', color: '#fb923c' };
    return { label: 'Well Above Avg', color: '#f87171' };
  } else {
    // Higher is better for min filters (margins, growth)
    if (diff >= 20) return { label: 'Well Above Avg', color: '#4ade80' };
    if (diff >= 5) return { label: 'Above Avg', color: '#86efac' };
    if (diff >= -5) return { label: 'At Avg', color: '#fbbf24' };
    if (diff >= -20) return { label: 'Below Avg', color: '#fb923c' };
    return { label: 'Well Below Avg', color: '#f87171' };
  }
}

const CATEGORY_LABELS: Record<string, string> = {
  valuation: 'Valuation Metrics',
  financial_health: 'Financial Health',
  growth: 'Growth Metrics',
  sentiment: 'Sentiment & Analyst',
};

const CATEGORY_ORDER = ['valuation', 'financial_health', 'growth', 'sentiment'];

export default function MetricsGuide({ stock }: Props) {
  const industryAvg = getIndustryAverages(stock.industry || '');
  const industryName = stock.industry || 'All Sectors';

  return (
    <div className="metrics-guide">
      {/* Industry Comparison Header */}
      <div className="industry-comparison-header">
        <h4>vs. Industry Average</h4>
        <span className="industry-name">{industryName}</span>
      </div>

      {/* Comparison Table */}
      <div className="comparison-table">
        <div className="comparison-row comparison-header-row">
          <span className="comp-metric">Metric</span>
          <span className="comp-stock">{stock.symbol}</span>
          <span className="comp-avg">Ind. Avg</span>
          <span className="comp-verdict">vs. Industry</span>
        </div>
        {renderComparisonRow('P/E', stock.pe_ratio, industryAvg.pe_ratio, 'max')}
        {renderComparisonRow('Fwd P/E', stock.forward_pe, industryAvg.forward_pe, 'max')}
        {renderComparisonRow('PEG', stock.peg_ratio, industryAvg.peg_ratio, 'max')}
        {renderComparisonRow('P/FCF', stock.price_to_fcf, industryAvg.price_to_fcf, 'max')}
        {renderComparisonRow('D/E', stock.debt_to_equity, industryAvg.debt_to_equity, 'max')}
        {renderComparisonRow('Quick Ratio', stock.quick_ratio, industryAvg.quick_ratio, 'min')}
        {renderComparisonRow('Op Margin', stock.operating_margin, industryAvg.operating_margin, 'min', true)}
        {renderComparisonRow('EPS Growth', stock.eps_growth_yoy, industryAvg.eps_growth_yoy, 'min', true)}
        {renderComparisonRow('Rev Growth', stock.revenue_growth_yoy, industryAvg.revenue_growth_yoy, 'min', true)}
      </div>

      {/* Metric Definitions by Category */}
      <div className="definitions-section">
        <h4>Metric Definitions & Interpretation</h4>
        {CATEGORY_ORDER.map(cat => {
          const metrics = METRIC_DEFINITIONS.filter(m => m.category === cat);
          return (
            <div key={cat} className="definition-category">
              <h5>{CATEGORY_LABELS[cat]}</h5>
              {metrics.map(metric => (
                <div key={metric.key} className="definition-card">
                  <div className="def-header">
                    <span className="def-name">{metric.name}</span>
                    <span className="def-threshold">{metric.threshold}</span>
                  </div>
                  <p className="def-text">{metric.definition}</p>
                  <div className="def-formula">
                    <span className="formula-label">Formula:</span> {metric.formula}
                  </div>
                  <div className="def-interpretations">
                    <div className="interp-item interp-up">
                      <span className="interp-arrow">↑</span>
                      <span>{metric.increasing}</span>
                    </div>
                    <div className="interp-item interp-down">
                      <span className="interp-arrow">↓</span>
                      <span>{metric.decreasing}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function renderComparisonRow(
  label: string,
  stockValue: number | null | undefined,
  avgValue: number,
  type: 'max' | 'min',
  isPercent = false,
) {
  const val = stockValue ?? null;
  const comparison = comparisonLabel(val, avgValue, type);
  const formatVal = (v: number | null) => {
    if (v === null || v === undefined) return '—';
    return isPercent ? `${(v * 100).toFixed(1)}%` : v.toFixed(2);
  };
  const formatAvg = (v: number) => {
    return isPercent ? `${(v * 100).toFixed(1)}%` : v.toFixed(2);
  };

  return (
    <div className="comparison-row">
      <span className="comp-metric">{label}</span>
      <span className="comp-stock">{formatVal(val)}</span>
      <span className="comp-avg">{formatAvg(avgValue)}</span>
      <span className="comp-verdict" style={{ color: comparison.color }}>{comparison.label}</span>
    </div>
  );
}
