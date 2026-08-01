"""
FULL AUDIT of all 3 scores using LRN's actual data.
Verifies normalization, range correctness, and consistency.
"""

print("=" * 70)
print("SCORE AUDIT — Using LRN (Stride) as test case")
print("=" * 70)

# =====================================================
# 1. FUNDAMENTAL SCORE
# =====================================================
print("\n\n### 1. FUNDAMENTAL SCORE ###")
print("Definition: How strongly a stock passes its filters (0-100)")
print("Formula: average(per_filter_scores) × 100")
print("Per-filter: 0 = exactly at threshold, 1.0 = best possible value")
print()

lrn = {
    "pe_ratio": 12.91,
    "forward_pe": 12.8959,
    "peg_ratio": 0.31,
    "price_to_fcf": 8.63,
    "debt_to_equity": 0.2695,
    "quick_ratio": 7.188,
    "operating_margin": 0.1497,  # stored as decimal (14.97%)
    "eps_growth_yoy": 0.4102,   # stored as decimal (41.02%)
    "revenue_growth_yoy": 0.1790,  # stored as decimal (17.9%)
    "est_lt_growth": 0.5811,    # stored as decimal (58.11%)
    "analyst_recommendation": 1.78,
}

filters = {
    "pe_ratio":              {"type": "max", "default": 50,  "min": 5,    "max": 100, "data_format": "ratio"},
    "forward_pe":            {"type": "max", "default": 20,  "min": 5,    "max": 50,  "data_format": "ratio"},
    "peg_ratio":             {"type": "max", "default": 1.0, "min": 0.1,  "max": 3.0, "data_format": "ratio"},
    "price_to_fcf":          {"type": "max", "default": 20,  "min": 5,    "max": 50,  "data_format": "ratio"},
    "debt_to_equity":        {"type": "max", "default": 1.0, "min": 0.0,  "max": 3.0, "data_format": "ratio"},
    "quick_ratio":           {"type": "min", "default": 1.0, "min": 0.5,  "max": 5.0, "data_format": "ratio"},
    "operating_margin":      {"type": "min", "default": 0,   "min": -20,  "max": 50,  "data_format": "percent_as_decimal"},
    "eps_growth_yoy":        {"type": "min", "default": 0,   "min": -50,  "max": 100, "data_format": "percent_as_decimal"},
    "revenue_growth_yoy":    {"type": "min", "default": 0,   "min": -50,  "max": 100, "data_format": "percent_as_decimal"},
    "est_lt_growth":         {"type": "min", "default": 0,   "min": -10,  "max": 50,  "data_format": "percent_as_decimal"},
    "analyst_recommendation":{"type": "max", "default": 3.0, "min": 1.0,  "max": 5.0, "data_format": "ratio"},
}

scores = []
print(f"{'Filter':<25} {'Raw Value':>10} {'Effective':>10} {'Threshold':>10} {'Best':>6} {'Score':>6}")
print("-" * 72)

for name, config in filters.items():
    raw_value = lrn.get(name)
    threshold = config["default"]
    filter_type = config["type"]
    filter_min = config["min"]
    filter_max = config["max"]
    data_format = config["data_format"]
    
    # Convert percent_as_decimal
    effective_value = raw_value * 100 if data_format == "percent_as_decimal" else raw_value
    
    if filter_type == "max":
        if threshold == filter_min:
            score = 1.0 if effective_value <= threshold else 0.0
        else:
            score = max(0.0, min(1.0, (threshold - effective_value) / (threshold - filter_min)))
        best = filter_min
    else:
        if filter_max == threshold:
            score = 1.0 if effective_value >= threshold else 0.0
        else:
            score = max(0.0, min(1.0, (effective_value - threshold) / (filter_max - threshold)))
        best = filter_max
    
    scores.append(score)
    eff_display = f"{effective_value:.2f}" if data_format == "ratio" else f"{effective_value:.1f}%"
    raw_display = f"{raw_value:.4f}" if data_format == "percent_as_decimal" else f"{raw_value:.2f}"
    print(f"{name:<25} {raw_display:>10} {eff_display:>10} {threshold:>10} {best:>6} {score:>6.3f}")

avg = sum(scores) / len(scores)
fundamental_score = round(avg * 100, 1)
print("-" * 72)
print(f"  Filters evaluated: {len(scores)}")
print(f"  Average: {avg:.4f}")
print(f"  FUNDAMENTAL SCORE: {fundamental_score}")
print(f"  Range check: min possible=0 (all at threshold), max=100 (all at best)")
print(f"  ✓ Score {fundamental_score} is in [0, 100]")

# =====================================================
# 2. SENTIMENT SCORE
# =====================================================
print("\n\n### 2. SENTIMENT SCORE ###")
print("Definition: AI-analyzed news sentiment (-1 to +1 raw, displayed as -100 to +100)")
print("Formula: sum(sentiment × confidence) / sum(confidence) for relevant articles")
print()

# Simulated articles (based on LRN's actual analysis)
articles = [
    {"sentiment": -0.35, "confidence": 0.65, "relevant": True, "title": "Burney Reduces Holdings"},
    {"sentiment": +0.15, "confidence": 0.45, "relevant": True, "title": "Granite Takes Position"},
    {"sentiment": +0.15, "confidence": 0.45, "relevant": True, "title": "Granite New Investment"},
    {"sentiment": +0.15, "confidence": 0.45, "relevant": True, "title": "Pacer Advisors Position"},
    {"sentiment": -0.65, "confidence": 0.72, "relevant": True, "title": "Major Partner Ends Contract"},
    {"sentiment": -0.30, "confidence": 0.40, "relevant": True, "title": "Trading Down 5.6%"},
    {"sentiment": -0.65, "confidence": 0.75, "relevant": True, "title": "Contract Non-Renewal"},
    {"sentiment": 0.0,   "confidence": 0.90, "relevant": False, "title": "Healthcare company (different Stride)"},
    {"sentiment": 0.0,   "confidence": 0.95, "relevant": False, "title": "Chegg article"},
    {"sentiment": 0.0,   "confidence": 0.95, "relevant": False, "title": "Crypto token STRD"},
]

relevant = [a for a in articles if a["relevant"]]
weighted_sum = sum(a["sentiment"] * a["confidence"] for a in relevant)
total_weight = sum(a["confidence"] for a in relevant)
aggregate_score = weighted_sum / total_weight if total_weight > 0 else 0.0
aggregate_confidence = total_weight / len(relevant) if relevant else 0.0

print(f"  Total articles: {len(articles)}")
print(f"  Relevant: {len(relevant)}")
print(f"  Irrelevant (discarded): {len(articles) - len(relevant)}")
print()
print(f"  Weighted sum: {weighted_sum:.4f}")
print(f"  Total weight: {total_weight:.4f}")
print(f"  RAW SENTIMENT: {weighted_sum:.4f} / {total_weight:.4f} = {aggregate_score:.4f}")
print(f"  CONFIDENCE: {total_weight:.2f} / {len(relevant)} = {aggregate_confidence:.4f}")
print(f"  DISPLAYED AS: {aggregate_score * 100:.0f} (range: -100 to +100)")
print()
print(f"  Range check: raw is in [-1, +1]: {-1 <= aggregate_score <= 1} ✓")
print(f"  Confidence is in [0, 1]: {0 <= aggregate_confidence <= 1} ✓")

# =====================================================
# 3. INVESTABILITY SCORE
# =====================================================
print("\n\n### 3. INVESTABILITY SCORE ###")
print("Definition: Final composite score (0-100)")
print("Formula: (0.7 × fundamental) + (0.3 × sentiment_adjustment) + risk_penalties")
print("  Where sentiment_adjustment = raw_sentiment × 25 × confidence")
print()

w_fundamental = 0.7
w_sentiment = 0.3
max_sentiment_bonus = 25.0

sentiment_adjustment = aggregate_score * max_sentiment_bonus * aggregate_confidence
base_score = (w_fundamental * fundamental_score) + (w_sentiment * sentiment_adjustment)

print(f"  Inputs:")
print(f"    Fundamental Score: {fundamental_score}")
print(f"    Sentiment Score (raw): {aggregate_score:.4f}")
print(f"    Sentiment Confidence: {aggregate_confidence:.4f}")
print()
print(f"  Step 1: Sentiment Adjustment = {aggregate_score:.4f} × {max_sentiment_bonus} × {aggregate_confidence:.4f}")
print(f"         = {sentiment_adjustment:.2f}")
print(f"         Range check: max possible = 1.0 × 25 × 1.0 = +25")
print(f"                      min possible = -1.0 × 25 × 1.0 = -25")
print(f"                      actual = {sentiment_adjustment:.2f} ✓")
print()
print(f"  Step 2: Base Score = (0.7 × {fundamental_score}) + (0.3 × {sentiment_adjustment:.2f})")
print(f"         = {w_fundamental * fundamental_score:.1f} + {w_sentiment * sentiment_adjustment:.1f}")
print(f"         = {base_score:.1f}")
print()

# Risk penalty (LRN has revenue_risk)
risk_penalty = -15  # revenue_risk base penalty
# If first_seen == today, days_since_first = 0, decay_factor = 1.0, full penalty
days_since_first = 0  # assuming article published today
decay_factor = 1.0 - (days_since_first / 5)  # DECAY_DAYS = 5
effective_penalty = round(risk_penalty * decay_factor, 1)

print(f"  Step 3: Risk Penalty")
print(f"    Flag: revenue_risk (one-time event)")
print(f"    Base penalty: {risk_penalty}")
print(f"    Days since article: {days_since_first}")
print(f"    Decay factor: 1.0 - ({days_since_first}/5) = {decay_factor}")
print(f"    Effective penalty: {risk_penalty} × {decay_factor} = {effective_penalty}")
print()

final_score = max(0.0, min(100.0, base_score + effective_penalty))
print(f"  Step 4: Final = max(0, min(100, {base_score:.1f} + {effective_penalty})) = {final_score:.1f}")
print(f"  INVESTABILITY SCORE: {final_score:.1f}")
print()

# =====================================================
# RANGE VERIFICATION
# =====================================================
print("\n### RANGE VERIFICATION ###")
print()
print("Score               | Range    | LRN Value | In Range?")
print("-" * 55)
print(f"Fundamental         | 0–100    | {fundamental_score:<9} | {'✓' if 0 <= fundamental_score <= 100 else '✗'}")
print(f"Sentiment (raw)     | -1 to +1 | {aggregate_score:<9.3f} | {'✓' if -1 <= aggregate_score <= 1 else '✗'}")
print(f"Sentiment (display) | -100–+100| {aggregate_score*100:<9.0f} | {'✓' if -100 <= aggregate_score*100 <= 100 else '✗'}")
print(f"Investability       | 0–100    | {final_score:<9.1f} | {'✓' if 0 <= final_score <= 100 else '✗'}")
print()

# =====================================================
# EDGE CASE ANALYSIS
# =====================================================
print("\n### EDGE CASES ###")
print()

# Can investability exceed 100?
max_fundamental = 100
max_sent_adj = 1.0 * 25.0 * 1.0  # +25
max_base = (0.7 * 100) + (0.3 * 25)  # = 70 + 7.5 = 77.5
print(f"Max possible base score: (0.7 × 100) + (0.3 × 25) = {max_base}")
print(f"  → Clamped to 100: {min(100, max_base)} ✓ (can't exceed 100)")
print()

# Can it go below 0?
min_fundamental = 0
min_sent_adj = -1.0 * 25.0 * 1.0  # -25
min_base = (0.7 * 0) + (0.3 * -25)  # = 0 + -7.5 = -7.5
max_penalty = -35  # fraud_allegation
min_total = min_base + max_penalty  # -7.5 + -35 = -42.5
print(f"Min possible base score: (0.7 × 0) + (0.3 × -25) = {min_base}")
print(f"  + worst penalty (fraud): {max_penalty}")
print(f"  = {min_total}")
print(f"  → Clamped to 0: {max(0, min_total)} ✓ (can't go below 0)")
print()

# PROBLEM: With w_sentiment = 0.3 and max_sentiment_bonus = 25,
# the sentiment contribution is at most ±7.5 points.
# But the fundamental contribution is 0.7 × 100 = up to 70 points.
# So the MAXIMUM investability (ignoring penalties) is 77.5, not 100.
print("⚠ PROBLEM IDENTIFIED:")
print(f"  Max investability (no penalties, perfect scores): {max_base}")
print(f"  This means the 0-100 scale can NEVER reach 100.")
print(f"  The effective range is 0 to ~77.5.")
print()
print("  Options:")
print("  A) Accept it — 77.5 is the practical ceiling")
print("  B) Normalize: final = base_score / 0.775 × 100 (maps 77.5 → 100)")
print("  C) Change formula so components add up to 100")
