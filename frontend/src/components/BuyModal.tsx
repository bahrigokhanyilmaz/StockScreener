import { useState } from 'react';
import { buyStock } from '../api.ts';

interface Props {
  ticker: string;
  currentPrice: number | null;
  onClose: () => void;
  onSuccess: () => void;
}

export default function BuyModal({ ticker, currentPrice, onClose, onSuccess }: Props) {
  const [price, setPrice] = useState(currentPrice?.toFixed(2) || '');
  const [shares, setShares] = useState('');
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!price || !shares) {
      setError('Price and shares are required');
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      await buyStock(ticker, parseFloat(price), parseFloat(shares), date);
      onSuccess();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to record purchase');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={e => e.stopPropagation()}>
        <h3>Buy {ticker}</h3>
        <form onSubmit={handleSubmit}>
          <div className="modal-field">
            <label>Price per share ($)</label>
            <input type="number" step="0.01" value={price} onChange={e => setPrice(e.target.value)} />
          </div>
          <div className="modal-field">
            <label>Number of shares</label>
            <input type="number" step="1" value={shares} onChange={e => setShares(e.target.value)} />
          </div>
          <div className="modal-field">
            <label>Purchase date</label>
            <input type="date" value={date} onChange={e => setDate(e.target.value)} />
          </div>
          {error && <p className="modal-error">{error}</p>}
          <div className="modal-actions">
            <button type="button" className="btn-cancel" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn-buy" disabled={submitting}>
              {submitting ? 'Recording...' : 'Record Purchase'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
