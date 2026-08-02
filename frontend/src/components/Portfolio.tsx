import { useState, useEffect } from 'react';
import { getPortfolio, sellStock } from '../api.ts';
import type { PortfolioPosition } from '../api.ts';

/**
 * Portfolio Panel
 *
 * Shows all owned positions with:
 * - Signal indicator (green/yellow/red circle)
 * - Cost basis, current price, P&L %
 * - Signal reasons on hover
 */

export default function Portfolio() {
  const [positions, setPositions] = useState<PortfolioPosition[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const data = await getPortfolio();
        setPositions(data.positions || []);
      } catch (err) {
        console.error('Failed to load portfolio:', err);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) return <div className="portfolio-loading">Loading portfolio...</div>;
  if (positions.length === 0) return null; // Don't show section if no positions

  const totalInvested = positions.reduce((sum, p) => sum + (p.total_invested || 0), 0);
  const totalPnl = positions.reduce((sum, p) => sum + (p.unrealized_pnl || 0), 0);
  const totalPnlPct = totalInvested > 0 ? (totalPnl / totalInvested) * 100 : 0;

  return (
    <div className="portfolio-section">
      <div className="portfolio-header">
        <h3>My Portfolio</h3>
        <span className={`portfolio-total-pnl ${totalPnl >= 0 ? 'positive' : 'negative'}`}>
          {totalPnl >= 0 ? '+' : ''}{totalPnlPct.toFixed(1)}% (${totalPnl.toFixed(0)})
        </span>
      </div>
      <div className="portfolio-grid">
        {positions.map((pos) => (
          <div key={pos.symbol} className="portfolio-card">
            <div className="portfolio-card-header">
              <span className={`signal-dot signal-${pos.signal || 'green'}`} 
                    title={(pos.signal_reasons || []).join(', ')} />
              <span className="portfolio-symbol">{pos.symbol}</span>
              <span className={`portfolio-pnl ${(pos.unrealized_pnl_pct || 0) >= 0 ? 'positive' : 'negative'}`}>
                {(pos.unrealized_pnl_pct || 0) >= 0 ? '+' : ''}{(pos.unrealized_pnl_pct || 0).toFixed(1)}%
              </span>
            </div>
            <div className="portfolio-card-details">
              <span className="portfolio-detail">Cost: ${pos.avg_cost_basis?.toFixed(2)}</span>
              <span className="portfolio-detail">Now: ${pos.current_price?.toFixed(2) || '—'}</span>
              <span className="portfolio-detail">{pos.total_shares} shares</span>
            </div>
            {pos.signal === 'red' && (
              <div className="portfolio-signal-reasons red">
                {(pos.signal_reasons || []).map((r, i) => <span key={i}>{r}</span>)}
              </div>
            )}
            {pos.signal === 'yellow' && (
              <div className="portfolio-signal-reasons yellow">
                {(pos.signal_reasons || []).map((r, i) => <span key={i}>{r}</span>)}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
