/**
 * API Client
 * 
 * All HTTP calls to the backend live here. Components never call fetch directly —
 * they use these functions. This gives us one place to change if the API evolves.
 */

import { API_BASE_URL } from './config.ts';

export interface Stock {
  symbol: string;
  company_name: string;
  company_description: string;
  logo: string;
  weburl: string;
  sector: string;
  industry: string;
  sic_industry: string;
  price: number | null;
  market_cap: number | null;
  investability_score: number | null;
  fundamental_score: number | null;
  sentiment_score: number | null;
  sentiment_confidence: number | null;
  tracking_status: string;
  pe_ratio: number | null;
  forward_pe: number | null;
  peg_ratio: number | null;
  price_to_fcf: number | null;
  debt_to_equity: number | null;
  quick_ratio: number | null;
  operating_margin: number | null;
  eps_growth_yoy: number | null;
  operating_income_growth_yoy: number | null;
  revenue_growth_yoy: number | null;
  est_lt_growth: number | null;
  est_lt_revenue_growth: number | null;
  target_price_upside: number | null;
  analyst_target_price: number | null;
  interest_coverage_ratio: number | null;
  hhi_score: number | null;
  competition_score: number | null;
  competition_reasoning: string | null;
  risk_flags: (string | Record<string, unknown>)[];
  first_tracked: string;
  last_updated: string;
}

export interface ScoreHistoryPoint {
  date: string;
  investability_score: number | null;
  fundamental_score: number | null;
  sentiment_score: number | null;
  price: number | null;
}

export interface PipelineStatus {
  active_count: number;
  grace_count: number;
  total_tracked: number;
  active_stocks: string[];
  grace_stocks: string[];
}

async function fetchJson<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, options);
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

export async function getStocks(): Promise<{ stocks: Stock[]; count: number }> {
  return fetchJson('/stocks');
}

export async function getStockDetail(ticker: string): Promise<{ stock: Stock }> {
  return fetchJson(`/stocks/${ticker}`);
}

export async function getStockHistory(ticker: string): Promise<{ history: ScoreHistoryPoint[]; data_points: number }> {
  return fetchJson(`/stocks/${ticker}/history`);
}

export interface NewsArticle {
  title: string;
  description: string;
  url: string;
  source: string;
  published_at: number;
  sentiment?: number;
  confidence?: number;
  risk_flags?: string[];
  summary?: string;
}

export async function getStockNews(ticker: string): Promise<{ articles: NewsArticle[]; count: number }> {
  return fetchJson(`/stocks/${ticker}/news`);
}

export interface PriceBar {
  d: string;   // date YYYY-MM-DD
  o: number;   // open
  h: number;   // high
  l: number;   // low
  c: number;   // close
  v: number;   // volume
}

export async function getStockPrices(ticker: string): Promise<{ bars: PriceBar[]; bar_count: number }> {
  return fetchJson(`/stocks/${ticker}/prices`);
}

export async function trackStock(ticker: string): Promise<{ message: string }> {
  return fetchJson(`/stocks/${ticker}/track`, { method: 'POST' });
}

export async function untrackStock(ticker: string): Promise<{ message: string }> {
  return fetchJson(`/stocks/${ticker}/track`, { method: 'DELETE' });
}

export async function getPipelineStatus(): Promise<PipelineStatus> {
  return fetchJson('/pipeline/status');
}

export interface PortfolioPosition {
  symbol: string;
  total_shares: number;
  avg_cost_basis: number;
  total_invested: number;
  current_price: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  signal: string | null;
  signal_reasons: string[];
  peak_price: number | null;
}

export async function getPortfolio(): Promise<{ positions: PortfolioPosition[]; count: number }> {
  return fetchJson('/portfolio');
}

export async function getPortfolioDetail(ticker: string): Promise<{ ticker: string; summary: Record<string, unknown>; lots: Record<string, unknown>[]; current_price: number | null }> {
  return fetchJson(`/portfolio/${ticker}`);
}

export async function buyStock(ticker: string, price: number, shares: number, date: string): Promise<{ message: string }> {
  return fetchJson(`/portfolio/${ticker}/buy`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ price, shares, date }),
  });
}

export async function sellStock(ticker: string, price: number, lotId?: string): Promise<{ message: string }> {
  return fetchJson(`/portfolio/${ticker}/sell`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ price, lot_id: lotId }),
  });
}

export interface IndustryAverages {
  [industry: string]: {
    pe_ratio?: number;
    pe_median?: number;
    pe_lower_quartile?: number;
    debt_to_equity?: number;
    quick_ratio?: number;
    operating_margin?: number;
    eps_growth_yoy?: number;
    operating_income_growth_yoy?: number;
    revenue_growth_yoy?: number;
    sample_size?: number;
  };
}

export async function getIndustryAverages(): Promise<{ industries: IndustryAverages; count: number }> {
  return fetchJson('/industries');
}
