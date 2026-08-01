"""Verify the new investability formula covers the full 0-100 range correctly."""

def investability(fundamental, raw_sentiment, confidence, penalty=0):
    sent_norm = 50 + (raw_sentiment * 50 * confidence)
    sent_norm = max(0, min(100, sent_norm))
    base = (0.7 * fundamental) + (0.3 * sent_norm)
    return max(0, min(100, base + penalty))

print("=== Investability Score Range Verification ===\n")
print(f"{'Case':<45} {'Fund':>5} {'Sent':>5} {'Conf':>5} {'Penalty':>8} {'Score':>6}")
print("-" * 80)

cases = [
    ("Perfect everything",                        100, +1.0, 1.0, 0),
    ("Perfect fundamentals, neutral sentiment",   100,  0.0, 0.0, 0),
    ("Perfect fundamentals, mild positive",       100, +0.3, 0.7, 0),
    ("Average fund, neutral sent",                 50,  0.0, 0.5, 0),
    ("Average fund, negative sent",                50, -0.5, 0.8, 0),
    ("Average fund, negative + fraud",             50, -0.5, 0.8, -35),
    ("LRN (actual)",                             64.1, -0.28, 0.55, -15),
    ("Barely passing, neutral",                     5,  0.0, 0.0, 0),
    ("Barely passing, very negative",               5, -1.0, 1.0, 0),
    ("Zero everything + fraud penalty",             0, -1.0, 1.0, -35),
]

for name, fund, sent, conf, pen in cases:
    score = investability(fund, sent, conf, pen)
    print(f"{name:<45} {fund:>5.1f} {sent:>+5.2f} {conf:>5.2f} {pen:>8} {score:>6.1f}")

print()
print("Range check:")
print(f"  Max achievable: {investability(100, 1.0, 1.0, 0):.1f} (should be 100)")
print(f"  Min achievable: {investability(0, -1.0, 1.0, -35):.1f} (should be 0, clamped)")
print(f"  Neutral midpoint: {investability(50, 0, 0, 0):.1f} (should be ~50)")
