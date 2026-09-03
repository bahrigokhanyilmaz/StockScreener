import { useState, useEffect, useMemo } from 'react';
import { getTrackHistory } from '../api.ts';
import type { TrackHistoryRecord } from '../api.ts';

/**
 * Tracking History Panel
 *
 * Shows CLOSED manual-tracking stints — each time a stock was marked to track
 * and later unmarked — GROUPED BY STOCK.
 *
 * Top level: one row per stock (symbol, number of stints, average % change).
 * Expand a stock to reveal a nested table with one entry per stint (each
 * mark → unmark cycle) showing its dates, prices, and % change.
 *
 * All % changes are based on the pipeline's daily closing prices.
 * The `refreshKey` prop lets the parent force a reload after a mark/unmark.
 */
interface Props {
  refreshKey: number;
}

interface StockGroup {
  symbol: string;
  stints: TrackHistoryRecord[];
  avgChange: number | null;
  latestUnmark: string;
}

function groupBySymbol(records: TrackHistoryRecord[]): StockGroup[] {
  const bySymbol = new Map<string, TrackHistoryRecord[]>();
  for (const r of records) {
    const arr = bySymbol.get(r.symbol) || [];
    arr.push(r);
    bySymbol.set(r.symbol, arr);
  }

  const groups: StockGroup[] = [];
  for (const [symbol, stints] of bySymbol) {
    // Newest stint first within each stock.
    stints.sort((a, b) => (b.unmark_date || '').localeCompare(a.unmark_date || ''));
    const changes = stints.map(s => s.change_pct).filter((c): c is number => c != null);
    const avgChange = changes.length
      ? changes.reduce((sum, c) => sum + c, 0) / changes.length
      : null;
    groups.push({
      symbol,
      stints,
      avgChange,
      latestUnmark: stints[0]?.unmark_date || '',
    });
  }

  // Most recently active stock first.
  groups.sort((a, b) => b.latestUnmark.localeCompare(a.latestUnmark));
  return groups;
}

export default function TrackHistory({ refreshKey }: Props) {
  const [records, setRecords] = useState<TrackHistoryRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(true);
  const [openSymbols, setOpenSymbols] = useState<Set<string>>(new Set());

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

  const groups = useMemo(() => groupBySymbol(records), [records]);

  function toggleSymbol(symbol: string) {
    setOpenSymbols(prev => {
      const next = new Set(prev);
      if (next.has(symbol)) next.delete(symbol);
      else next.add(symbol);
      return next;
    });
  }

  if (loading) return null;
  if (records.length === 0) return null; // Hide section when there's no history

  return (
    <div className="track-history-section">
      <div className="track-history-header" onClick={() => setExpanded(!expanded)}>
        <h3>Tracking History ({groups.length} {groups.length === 1 ? 'stock' : 'stocks'})</h3>
        <span className="expand-icon">{expanded ? '▼' : '▶'}</span>
      </div>
      {expanded && (
        <div className="track-history-table-wrap">
          <table className="track-history-table">
            <thead>
              <tr>
                <th></th>
                <th>Stock</th>
                <th>Stints</th>
                <th>Avg Change</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => {
                const isOpen = openSymbols.has(g.symbol);
                return [
                  <tr
                    key={g.symbol}
                    className="th-group-row"
                    onClick={() => toggleSymbol(g.symbol)}
                  >
                    <td className="th-caret">{isOpen ? '▼' : '▶'}</td>
                    <td className="th-symbol">{g.symbol}</td>
                    <td>{g.stints.length}</td>
                    <td className={g.avgChange == null ? '' : g.avgChange >= 0 ? 'positive' : 'negative'}>
                      {g.avgChange != null
                        ? `${g.avgChange >= 0 ? '+' : ''}${g.avgChange.toFixed(2)}%`
                        : '—'}
                    </td>
                  </tr>,
                  isOpen && (
                    <tr key={`${g.symbol}-detail`} className="th-nested-row">
                      <td></td>
                      <td colSpan={3}>
                        <table className="track-history-nested">
                          <thead>
                            <tr>
                              <th>Marked</th>
                              <th>Mark $</th>
                              <th>Unmarked</th>
                              <th>Unmark $</th>
                              <th>Change</th>
                            </tr>
                          </thead>
                          <tbody>
                            {g.stints.map((s, i) => (
                              <tr key={`${g.symbol}-${s.unmark_date}-${i}`}>
                                <td>{s.mark_date || '—'}</td>
                                <td>{s.mark_price != null ? `$${s.mark_price.toFixed(2)}` : '—'}</td>
                                <td>{s.unmark_date || '—'}</td>
                                <td>{s.unmark_price != null ? `$${s.unmark_price.toFixed(2)}` : '—'}</td>
                                <td className={s.change_pct == null ? '' : s.change_pct >= 0 ? 'positive' : 'negative'}>
                                  {s.change_pct != null
                                    ? `${s.change_pct >= 0 ? '+' : ''}${s.change_pct.toFixed(2)}%`
                                    : '—'}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </td>
                    </tr>
                  ),
                ];
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
