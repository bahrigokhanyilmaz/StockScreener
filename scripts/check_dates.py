"""Check what day of week key dates are."""
from datetime import date
for d in [17, 18, 19, 20, 21]:
    dt = date(2026, 7, d)
    print(f"  July {d}: {dt.strftime('%A')} (weekday={dt.weekday()})")
