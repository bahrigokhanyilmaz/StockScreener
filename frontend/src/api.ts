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
  revenue_growth_yoy: number | null;
  est_lt_growth: number | null;
  target_price_upside: number | null;
  analyst_target_price: number | null;
  risk_flags: string[];
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
}

export async function getStockNews(ticker: string): Promise<{ articles: NewsArticle[]; count: number }> {
  return fetchJson(`/stocks/${ticker}/news`);
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

export interface IndustryAverages {
  [industry: string]: {
    pe_ratio?: number;
    debt_to_equity?: number;
    quick_ratio?: number;
    operating_margin?: number;
    eps_growth_yoy?: number;
    revenue_growth_yoy?: number;
    sample_size?: number;
  };
}

export async function getIndustryAverages(): Promise<{ industries: IndustryAverages; count: number }> {
  return fetchJson('/industries');
}
