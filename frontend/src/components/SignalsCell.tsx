import type { Stock } from '../api.ts';
import { buildSignalSlots } from '../utils/signals.ts';

/** Advisory signals in fixed-width slots (DIV, G▼) so badges align across rows. */
export default function SignalsCell({ stock }: { stock: Stock }) {
  const [div, guidance] = buildSignalSlots(stock);
  return (
    <span className="signals-slots">
      <span className="signal-slot" data-slot="div">
        {div && div.key === 'div' && (
          <span className={`divergence-badge divergence-${div.tier}`} title={div.tooltip}>DIV</span>
        )}
      </span>
      <span className="signal-slot" data-slot="guidance">
        {guidance && <span className="guidance-marker" title={guidance.tooltip}>G▼</span>}
      </span>
    </span>
  );
}
