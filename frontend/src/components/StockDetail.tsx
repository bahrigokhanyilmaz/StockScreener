import { useState, useEffect } from 'react';
import { getStockDetail, getStockHistory, getStockNews } from '../api.ts';
import type { Stock, ScoreHistoryPoint, NewsArticle } from '../api.ts';
import MetricsGuide from './MetricsGuide.tsx';

/**
 * StockDetail Component
 *
 * Two-tab detail panel when a stock row is clicked:
 *
 * Tab 1 — Overview:
 *   - Company description (real business summary)
 *   - Three score cards (investability, fundamental, sentiment)
 *   - Recent news articles (expandable, fetched live)
 *   - Risk flags if any
 *
 * Tab 2 — Metrics Guide:
 *   - Industry average comparison for this stock
 *   - Definitions of all monitored metrics
 *   - Interpretation of movement in either direction
 */

interface Props {
  ticker: string;
  onClose: () => void;
}

export default function StockDetail({ ticker, onClose }: Props) {
  const [stock, setStock] = useState<Stock | null>(null);
  const [history, setHistory] = useState<ScoreHistoryPoint[]>([]);
  const [news, setNews] = useState<NewsArticle[]>([]);
  const [loading, setLoading] = useState(true);
  const [newsExpanded, setNewsExpanded] = useState(true);
  const [activeTab, setActiveTab] = useState<'overview' | 'metrics'>('overview');

  useEffect(() => {
    async function load() {
      setLoading(true);
      setNewsExpanded(true);
      setActiveTab('overview');
      try {
        const [detailData, historyData, newsData] = await Promise.all([
          getStockDetail(ticker),
          getStockHistory(ticker),
          getStockNews(ticker),
        ]);
        setStock(detailData.stock);
        setHistory(historyData.history);
        setNews(newsData.articles || []);
      } catch (err) {
        console.error('Failed to load stock detail:', err);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [ticker]);

  if (loading) return <div className="detail-loading">Loading {ticker}...</div>;
  if (!stock) return <div className="detail-error">Stock not found</div>;

  return (
    <div className="stock-detail">
      {/* Header */}
      <div className="detail-header">
        <div className="detail-header-left">
          {stock.logo && <img src={stock.logo} alt={stock.symbol} className="company-logo" />}
          <div>
            <h2>{stock.symbol}</h2>
            <p className="company-name">{stock.company_name}</p>
            <p className="sector-info">{stock.sector}{stock.industry ? ` / ${stock.industry}` : ''}</p>
            {stock.weburl && (
              <a href={stock.weburl} target="_blank" rel="noopener noreferrer" className="company-link">
                {stock.weburl.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')}
              </a>
            )}
          </div>
        </div>
        <button className="close-btn" onClick={onClose}>Close</button>
      </div>

      {/* Tabs */}
      <div className="detail-tabs">
        <button
          className={`detail-tab ${activeTab === 'overview' ? 'active' : ''}`}
          onClick={() => setActiveTab('overview')}
        >
          Overview
        </button>
        <button
          className={`detail-tab ${activeTab === 'metrics' ? 'active' : ''}`}
          onClick={() => setActiveTab('metrics')}
        >
          Metrics Guide
        </button>
      </div>

      {/* Tab Content */}
      {activeTab === 'overview' ? (
        <div className="tab-content">
          {/* Company Description / Business Model */}
          {stock.company_description && (
            <div className="company-profile">
              <h4>Company Profile</h4>
              <p className="company-description">{stock.company_description}</p>
            </div>
          )}

          {/* Score Cards */}
          <div className="score-section">
            <ScoreCard label="Investability" value={stock.investability_score} max={100} color={scoreColor(stock.investability_score)} />
            <ScoreCard label="Fundamental" value={stock.fundamental_score} max={100} color="#3b82f6" />
            <ScoreCard label="Sentiment" value={stock.sentiment_score !== null ? stock.sentiment_score * 100 : null} max={100} min={-100} color={sentimentColor(stock.sentiment_score)} />
          </div>

          {/* Risk Flags */}
          {stock.risk_flags && stock.risk_flags.length > 0 && (
            <div className="risk-flags">
              <h4>Risk Flags</h4>
              {stock.risk_flags.map((flag: string | Record<string, unknown>, i: number) => {
                const entry = typeof flag === 'string'
                  ? { flag, first_seen: '', last_seen: '', days_active: 0, status: '' }
                  : flag as Record<string, unknown>;
                const flagName = (entry.flag as string) || (typeof flag === 'string' ? flag : '');
                const firstSeen = entry.first_seen as string || '';
                const daysActive = entry.days_active as number || 0;
                const status = entry.status as string || '';
                return (
                  <div key={i} className="risk-flag-item">
                    <span className={`risk-flag-badge ${status === 'decayed' ? 'risk-decayed' : ''}`}>
                      {flagName.replace(/_/g, ' ')}
                    </span>
                    {firstSeen && (
                      <span className="risk-flag-meta">
                        since {firstSeen}{daysActive > 1 ? ` · ${daysActive}d confirmed` : ''}
                        {status === 'decayed' && ' · priced in'}
                        {status === 'decaying' && ' · decaying'}
                        {status === 'active' && ' · active'}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Score History */}
          {history.length > 1 && (
            <div className="history-section">
              <h4>Score History ({history.length} days)</h4>
              <div className="history-chart">
                {history.map((point, i) => (
                  <div key={i} className="history-bar-wrapper" title={`${point.date}: ${point.investability_score}`}>
                    <div
                      className="history-bar"
                      style={{
                        height: `${Math.max(5, (point.investability_score ?? 0))}%`,
                        backgroundColor: scoreColor(point.investability_score),
                      }}
                    />
                    <span className="history-date">{point.date?.slice(5)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Recent News */}
          <div className="news-section">
            <div className="news-header" onClick={() => setNewsExpanded(!newsExpanded)}>
              <h4>Recent News {news.length > 0 ? `(${news.length})` : ''}</h4>
              <span className="expand-icon">{newsExpanded ? '▼' : '▶'}</span>
            </div>
            {newsExpanded && (
              <div className="news-list">
                {news.length === 0 ? (
                  <p className="news-empty">Loading news...</p>
                ) : (
                  news.map((article, i) => (
                    <a key={i} href={article.url} target="_blank" rel="noopener noreferrer" className="news-item">
                      <span className="news-title">{article.title}</span>
                      <span className="news-meta">
                        {article.source}
                        {article.published_at ? ` · ${formatTimeAgo(article.published_at)}` : ''}
                      </span>
                      {article.description && (
                        <span className="news-desc">{article.description.slice(0, 120)}...</span>
                      )}
                    </a>
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="tab-content">
          <MetricsGuide stock={stock} />
        </div>
      )}

      <div className="detail-footer">
        <small>Last updated: {stock.last_updated ? new Date(stock.last_updated).toLocaleString() : '—'}</small>
      </div>
    </div>
  );
}

function ScoreCard({ label, value, max, min = 0, color }: {
  label: string; value: number | null; max: number; min?: number; color: string;
}) {
  const display = value !== null ? value.toFixed(0) : '—';
  const percentage = value !== null ? ((value - min) / (max - min)) * 100 : 0;
  return (
    <div className="score-card">
      <div className="score-value" style={{ color }}>{display}</div>
      <div className="score-bar-bg">
        <div className="score-bar-fill" style={{ width: `${Math.max(0, Math.min(100, percentage))}%`, backgroundColor: color }} />
      </div>
      <div className="score-label">{label}</div>
    </div>
  );
}

function scoreColor(score: number | null): string {
  if (score === null) return '#6b7280';
  if (score >= 70) return '#22c55e';
  if (score >= 40) return '#f59e0b';
  return '#ef4444';
}

function sentimentColor(score: number | null): string {
  if (score === null) return '#6b7280';
  if (score > 0.1) return '#22c55e';
  if (score < -0.1) return '#ef4444';
  return '#f59e0b';
}

function formatTimeAgo(timestamp: number): string {
  const now = Date.now();
  const ms = timestamp > 1e12 ? timestamp : timestamp * 1000;
  const diff = now - ms;
  const hours = Math.floor(diff / 3600000);
  if (hours < 1) return 'just now';
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}
