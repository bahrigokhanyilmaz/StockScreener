"""
Full calculation of LRN's fundamental score with corrected percent handling.
"""

lrn = {
    "pe_ratio": 12.91,
    "forward_pe": 12.8959,
    "peg_ratio": 0.31,
    "price_to_fcf": 8.63,
    "debt_to_equity": 0.2695,
    "quick_ratio": 7.188,
    "operating_margin": 0.1497,
    "eps_growth_yoy": 0.4102,
    "revenue_growth_yoy": 0.1790,
    "est_lt_growth": 0.5811,
    "analyst_recommendation": 1.78,
}

filters = {
    "pe_ratio":              {"type": "max", "default": 50,  "min": 5,    "max": 100, "fmt": "ratio"},
    "forward_pe":            {"type": "max", "default": 20,  "min": 5,    "max": 50,  "fmt": "ratio"},
    "peg_ratio":             {"type": "max", "default": 1.0, "min": 0.1,  "max": 3.0, "fmt": "ratio"},
    "price_to_fcf":          {"type": "max", "default": 20,  "min": 5,    "max": 50,  "fmt": "ratio"},
    "debt_to_equity":        {"type": "max", "default": 1.0, "min": 0.0,  "max": 3.0, "fmt": "ratio"},
    "quick_ratio":           {"type": "min", "default": 1.0, "min": 0.5,  "max": 5.0, "fmt": "ratio"},
    "operating_margin":      {"type": "min", "default": 0,   "min": -20,  "max": 50,  "fmt": "pct"},
    "eps_growth_yoy":        {"type": "min", "default": 0,   "min": -50,  "max": 100, "fmt": "pct"},
    "revenue_growth_yoy":    {"type": "min", "default": 0,   "min": -50,  "max": 100, "fmt": "pct"},
    "est_lt_growth":         {"type": "min", "default": 0,   "min": -10,  "max": 50,  "fmt": "pct"},
    "analyst_recommendation":{"type": "max", "default": 3.0, "min": 1.0,  "max": 5.0, "fmt": "ratio"},
}

print("=== LRN Fundamental Score (Corrected) ===\n")
print(f"{'Filter':<25} {'Value':>10} {'Threshold':>10} {'Best':>6} {'Score':>6}")
print("-" * 65)

scores = []
for name, config in filters.items():
    raw_value = lrn.get(name)
    if raw_value is None:
        continue

    threshold = config["default"]
    filter_type = config["type"]
    filter_min = config["min"]
    filter_max = config["max"]
    fmt = config["fmt"]

    # Convert percent: data stores 0.15, config uses 15
    value = raw_value * 100 if fmt == "pct" else raw_value

    if filter_type == "max":
        if threshold == filter_min:
            score = 1.0 if value <= threshold else 0.0
        else:
            score = max(0.0, min(1.0, (threshold - value) / (threshold - filter_min)))
        best = filter_min
    else:
        if filter_max == threshold:
            score = 1.0 if value >= threshold else 0.0
        else:
            score = max(0.0, min(1.0, (value - threshold) / (filter_max - threshold)))
        best = filter_max

    scores.append(score)

    val_display = f"{value:.1f}%" if fmt == "pct" else f"{raw_value:.2f}"
    thresh_display = f"{threshold}%" if fmt == "pct" else f"{threshold}"
    print(f"{name:<25} {val_display:>10} {thresh_display:>10} {best:>6} {score:>6.3f}")

print("-" * 65)
avg = sum(scores) / len(scores)
final = round(avg * 100, 1)
print(f"\n  Filters scored:          {len(scores)}")
print(f"  Average per-filter:      {avg:.4f}")
print(f"  Fundamental Score:       {avg:.4f} × 100 = {final}")
