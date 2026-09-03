import { useState, useEffect } from 'react';
import { getTrackHistory } from '../api.ts';
import type { TrackHistoryRecord } from '../api.ts';

/**
 * Tracking History Panel
 *
 * Shows all CLOSED manual-tracking stints — each time a stock was marked
 * to track and later unmarked. For every stint we display the mark/unmark
 * dates and prices and the % change over the tracked period (based on the
 * pipeline's daily closing prices).
 *
 * The `refreshKey` prop lets the parent force a reload after a mark/unmark.
 */
interface Props {
  refreshKey: number;
}

export default function TrackHistory({ refreshKey }: Props) {
  const [records, setRecords] = useState<TrackHistoryRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const data = await getTrackHistory();
        if (!cancelled) setRecords(data.history || []);
      } catch (err) {
        console.error('Failed to load track history:', err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [refreshKey]);

  if (loading) return null;
  if (records.length === 0) return null; // Hide section when there's no history

  return (
    <div className="track-history-section">
      <div className="track-history-header" onClick={() => setExpanded(!expanded)}>
        <h3>Tracking History ({records.length})</h3>
        <span className="expand-icon">{expanded ? '▼' : '▶'}</span>
      </div>
      {expanded && (
        <div className="track-history-table-wrap">
          <table className="track-history-table">
            <thead>
              <tr>
                <th>Stock</th>
                <th>Marked</th>
                <th>Mark $</th>
                <th>Unmarked</th>
                <th>Unmark $</th>
                <th>Change</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r, i) => (
                <tr key={`${r.symbol}-${r.unmark_date}-${i}`}>
                  <td className="th-symbol">{r.symbol}</td>
                  <td>{r.mark_date || '—'}</td>
                  <td>{r.mark_price != null ? `$${r.mark_price.toFixed(2)}` : '—'}</td>
                  <td>{r.unmark_date || '—'}</td>
                  <td>{r.unmark_price != null ? `$${r.unmark_price.toFixed(2)}` : '—'}</td>
                  <td className={r.change_pct == null ? '' : r.change_pct >= 0 ? 'positive' : 'negative'}>
                    {r.change_pct != null
                      ? `${r.change_pct >= 0 ? '+' : ''}${r.change_pct.toFixed(2)}%`
                      : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
