import type { Stock } from '../api.ts';
import { buildSignalSlots } from '../utils/signals.ts';

/** Advisory signals (DIV, G▼) in one left-aligned row: every badge starts at the column's left edge. */
export default function SignalsCell({ stock }: { stock: Stock }) {
  const [div, guidance] = buildSignalSlots(stock);
  return (
    <span className="signals-row">
      {div && div.key === 'div' && (
        <span className={`divergence-badge divergence-${div.tier}`} title={div.tooltip}>DIV</span>
      )}
      {guidance && <span className="guidance-marker" title={guidance.tooltip}>G▼</span>}
    </span>
  );
}
