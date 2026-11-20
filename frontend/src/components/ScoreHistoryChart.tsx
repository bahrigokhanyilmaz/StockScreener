import { useState } from 'react';
import type { ScoreHistoryPoint } from '../api.ts';
import {
  HISTORY_RANGES, availableRanges, barGeometry, historyHeading, historyView, labelIndexes, scoreColor,
} from '../utils/history.ts';
import type { HistoryRange } from '../utils/history.ts';

export const CHART_WIDTH = 340;
export const CHART_HEIGHT = 80;

export default function ScoreHistoryChart({ history, initialRange = '30D' }: { history: ScoreHistoryPoint[]; initialRange?: HistoryRange }) {
  const [range, setRange] = useState<HistoryRange>(initialRange);
  const enabled = availableRanges(history);
  const view = historyView(history, range);
  const { slot, barWidth } = barGeometry(view.bars.length, CHART_WIDTH);

  return (
    <div className="history-section">
      <h4>{historyHeading(view)}</h4>
      <div className="history-ranges">
        {HISTORY_RANGES.filter((r) => enabled[r]).map((r) => (
          <button key={r} className={r === range ? 'active' : ''} onClick={() => setRange(r)}>{r}</button>
        ))}
      </div>
      <svg
        className="history-svg"
        viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
        preserveAspectRatio="none"
        role="img"
        aria-label="Investability score history"
      >
        {view.bars.map((p, i) => {
          const h = (Math.max(5, p.investability_score ?? 0) / 100) * CHART_HEIGHT;
          return (
            <rect key={p.date} x={i * slot} y={CHART_HEIGHT - h} width={barWidth} height={h} fill={scoreColor(p.investability_score)}>
              <title>{`${p.date}: ${p.investability_score ?? 'n/a'}`}</title>
            </rect>
          );
        })}
      </svg>
      <div className="history-labels">
        {labelIndexes(view.bars.length).map((i) => (
          <span key={i}>{view.bars[i].date.slice(5)}</span>
        ))}
      </div>
    </div>
  );
}
