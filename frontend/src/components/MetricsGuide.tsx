import { useState, useEffect } from 'react';
import { getIndustryAverages } from '../api.ts';
import type { Stock, IndustryAverages } from '../api.ts';

/**
 * MetricsGuide Component
 *
 * Two sections:
 * 1. Metric definitions with interpretation of movement in either direction
 * 2. Industry average comparison for the selected stock's industry
 *    (real medians computed from ~5,097 stocks, persisted daily by pipeline)
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
    threshold: '< industry lower quartile',
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
    threshold: '< 20 (soft — applied if data available)',
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
    threshold: '> 0% (soft — applied if data available)',
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
    threshold: '<= 3.0 (soft — applied if data available)',
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
// INDUSTRY AVERAGES (fetched from API — real medians from 5,097 stocks)
// ==========================================

interface IndustryMetrics {
  pe_ratio?: number;
  debt_to_equity?: number;
  quick_ratio?: number;
  operating_margin?: number;
  eps_growth_yoy?: number;
  revenue_growth_yoy?: number;
  sample_size?: number;
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
  const [industryData, setIndustryData] = useState<IndustryMetrics | null>(null);
  const [industryName, setIndustryName] = useState<string>('');
  const [sampleSize, setSampleSize] = useState<number>(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadAverages() {
      setLoading(true);
      try {
        const data = await getIndustryAverages();
        const industries = data.industries || {};

        // Match using SEC SIC industry (exact match against DynamoDB keys)
        const sicIndustry = stock.sic_industry || '';
        let matched: IndustryMetrics | null = null;
        let matchedName = '';

        if (sicIndustry && industries[sicIndustry]) {
          matched = industries[sicIndustry];
          matchedName = sicIndustry;
        } else {
          // Fallback: try Finnhub industry with partial matching
          const stockIndustry = stock.industry || '';
          for (const [key, val] of Object.entries(industries)) {
            const lower = stockIndustry.toLowerCase();
            if (key.toLowerCase().includes(lower) || lower.includes(key.toLowerCase())) {
              matched = val as IndustryMetrics;
              matchedName = key;
              break;
            }
          }
        }

        setIndustryData(matched);
        setIndustryName(matchedName || sicIndustry || stock.industry || '');
        setSampleSize((matched as IndustryMetrics)?.sample_size || 0);
      } catch (err) {
        console.error('Failed to load industry averages:', err);
      } finally {
        setLoading(false);
      }
    }
    loadAverages();
  }, [stock.sic_industry, stock.industry]);

  return (
    <div className="metrics-guide">
      {/* Industry Comparison Header */}
      <div className="industry-comparison-header">
        <h4>vs. Industry Median</h4>
        <span className="industry-name">
          {industryName || 'Unknown'}
          {sampleSize > 0 && <span className="sample-size"> ({sampleSize} companies)</span>}
        </span>
      </div>

      {/* Comparison Table */}
      {loading ? (
        <p style={{ color: '#64748b', fontSize: '0.8rem', padding: '0.5rem' }}>Loading industry data...</p>
      ) : !industryData ? (
        <p style={{ color: '#64748b', fontSize: '0.8rem', padding: '0.5rem' }}>
          No industry average data available for "{stock.industry || 'unknown'}".
          Industry medians are computed on each pipeline run.
        </p>
      ) : (
        <div className="comparison-table">
          <div className="comparison-row comparison-header-row">
            <span className="comp-metric">Metric</span>
            <span className="comp-stock">{stock.symbol}</span>
            <span className="comp-avg">Ind. Median</span>
            <span className="comp-verdict">vs. Industry</span>
          </div>
          {industryData.pe_ratio != null && renderComparisonRow('P/E', stock.pe_ratio, industryData.pe_ratio, 'max')}
          {industryData.debt_to_equity != null && renderComparisonRow('D/E', stock.debt_to_equity, industryData.debt_to_equity, 'max')}
          {industryData.quick_ratio != null && renderComparisonRow('Quick Ratio', stock.quick_ratio, industryData.quick_ratio, 'min')}
          {industryData.operating_margin != null && renderComparisonRow('Op Margin', stock.operating_margin, industryData.operating_margin, 'min', true)}
          {industryData.eps_growth_yoy != null && renderComparisonRow('EPS Growth', stock.eps_growth_yoy, industryData.eps_growth_yoy, 'min', true)}
          {industryData.revenue_growth_yoy != null && renderComparisonRow('Rev Growth', stock.revenue_growth_yoy, industryData.revenue_growth_yoy, 'min', true)}
        </div>
      )}

      {/* Metric Definitions by Category */}
      <div className="definitions-section">
        <h4>How Scores Are Calculated</h4>

        <div className="definition-card">
          <div className="def-header">
            <span className="def-name">Investability Score (0–100)</span>
          </div>
          <p className="def-text">
            The final ranking score that combines fundamentals and market sentiment. Both components are on a 0–100 scale, so the result properly fills 0–100.
          </p>
          <div className="def-formula">
            <span className="formula-label">Formula:</span> (0.7 × Fundamental Score) + (0.3 × Sentiment Normalized) + Risk Penalties
          </div>
          <div className="score-breakdown-details">
            <p><strong>Sentiment Normalized (0–100):</strong> 50 + (raw_sentiment × 50 × confidence)</p>
            <p>• Neutral news or low confidence → 50 (no impact)</p>
            <p>• Very positive, high confidence → approaches 100 (boosts score)</p>
            <p>• Very negative, high confidence → approaches 0 (drags score down)</p>
            <p><strong>Risk Penalties:</strong> Fraud (-35), SEC investigation (-30), accounting irregularity (-25), revenue risk (-15), regulatory risk (-15), lawsuit/management/recall (-10)</p>
            <p><strong>Example:</strong> Fundamental 64, Sentiment raw -0.28 with confidence 0.55 → Sentiment normalized = 50 + (-0.28 × 50 × 0.55) = 42.3 → Investability = (0.7 × 64) + (0.3 × 42.3) = 44.8 + 12.7 = <strong>57.5</strong> before penalties.</p>
          </div>
        </div>

        <div className="definition-card">
          <div className="def-header">
            <span className="def-name">Fundamental Score (0–100)</span>
          </div>
          <p className="def-text">
            Measures how strongly a stock passes each value filter. A stock that barely passes every metric scores low; one that crushes every threshold scores high.
          </p>
          <div className="def-formula">
            <span className="formula-label">Method:</span> Each filter is scored 0–1 individually, then all scores are averaged and multiplied by 100.
          </div>
          <div className="score-breakdown-details">
            <p><strong>Per-filter scoring (0 to 1):</strong></p>
            <p>• At the threshold = 0 (you just barely passed)</p>
            <p>• At the best end of the range = 1.0 (you crushed it)</p>
            <p><strong>Final score:</strong> average of all per-filter scores × 100</p>
            <p><strong>Example:</strong> PEG threshold is 1.0 (best possible is 0.1). Stock with PEG 0.3: score = (1.0 - 0.3) / (1.0 - 0.1) = 0.78. Stock at PEG 0.95: score = (1.0 - 0.95) / (1.0 - 0.1) = 0.06 (barely passed). If all filters average 0.60, the Fundamental Score = <strong>60</strong>.</p>
          </div>
        </div>

        <div className="definition-card">
          <div className="def-header">
            <span className="def-name">Sentiment Score (-100 to +100)</span>
          </div>
          <p className="def-text">
            AI-analyzed news sentiment from the past 7 days. Claude AI reads each article about the stock, rates it from -1.0 (extremely negative) to +1.0 (extremely positive), and assigns a confidence level (0–1). The final score is displayed scaled to -100 to +100 in the UI.
          </p>
          <div className="def-formula">
            <span className="formula-label">Method:</span> For each relevant article: Claude assigns (sentiment, confidence). Raw score = sum(sentiment × confidence) / sum(confidence). Displayed as raw score × 100.
          </div>
          <div className="score-breakdown-details">
            <p><strong>Step 1:</strong> Fetch up to 10 recent articles per stock from 10,000+ news sources.</p>
            <p><strong>Step 2:</strong> Claude AI reads each article and rates: Is it relevant to this stock? What's the sentiment (-1 to +1)? How confident is it (0 to 1)?</p>
            <p><strong>Step 3:</strong> Irrelevant articles are discarded. Remaining articles are averaged, weighted by confidence (a definitive article with confidence 0.9 counts more than a vague one at 0.3).</p>
            <p><strong>Step 4:</strong> Risk flags extracted (fraud allegations, SEC investigations, lawsuits, etc.) — these become hard penalties on the Investability Score.</p>
            <p><strong>Example:</strong> 3 relevant articles with (sentiment=+0.6, conf=0.9), (sentiment=+0.2, conf=0.5), (sentiment=-0.3, conf=0.7). Raw score = (0.6×0.9 + 0.2×0.5 + -0.3×0.7) / (0.9 + 0.5 + 0.7) = 0.44 / 2.1 = <strong>+0.21</strong> → displayed as <strong>+21</strong> in the UI.</p>
          </div>
        </div>
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
