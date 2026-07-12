"""
Financial Modeling Prep (FMP) Data Provider
============================================
Works on both free and paid tiers.

Free tier strategy:
- Universe discovery: NASDAQ official traded symbols file (free, comprehensive)
- Market cap filter: FMP /stable/profile per-symbol (works on free)
- Fundamentals: FMP /stable/ratios-ttm per-symbol (works on free)
- Bandwidth budget: ~500MB/30 days → supports ~1,000 stocks daily

Paid tier upgrade:
- Universe discovery: FMP /stable/stock-screener (all-in-one)
- Batch endpoints become available
- Higher rate limits

To switch from free to paid behavior, no code changes needed — the paid
endpoints just stop returning 402 errors.

Bandwidth math (free tier):
- Profile call: ~2.7 KB per stock
- Ratios call:  ~3.0 KB per stock
- 1,000 stocks/day × 5.7KB × 30 days = ~162 MB/month
- Weekly universe refresh (5,500 profiles) = ~120 MB/month
- Total: ~282 MB/month (within 500 MB limit)
"""

import time
from typing import Optional

import requests as http_requests

from providers.base import DataProvider, StockFundamentals, ProviderError


class FMPProvider(DataProvider):
    """
    Fetches financial data from Financial Modeling Prep's stable API.

    Works on both free and paid tiers. On free tier, uses per-symbol
    endpoints. On paid tier, can use batch and screener endpoints.
    """

    FMP_BASE_URL = "https://financialmodelingprep.com/stable"
    NASDAQ_TRADED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"

    def __init__(self, api_key: str = "", min_market_cap: float = 300_000_000, **kwargs):
        """
        Initialize with FMP API key and universe configuration.

        Args:
            api_key: FMP API key (fetched from SSM by the handler)
            min_market_cap: Minimum market cap for universe inclusion (configurable)
        """
        if not api_key:
            raise ProviderError(
                "FMPProvider requires an api_key. "
                "Store it in SSM and set FMP_API_KEY_PARAM env var."
            )
        self._api_key = api_key
        self._min_market_cap = min_market_cap

    @property
    def name(self) -> str:
        return "Financial Modeling Prep"

    def get_stock_universe(self) -> list[str]:
        """
        Discover the stock universe from NASDAQ's official traded symbols file.

        This file is free, official, and comprehensive. It lists all
        NASDAQ-traded and NYSE-listed securities. We filter to get
        only common stocks (no ETFs, warrants, rights, units, preferred).

        Note: This returns ALL common stocks (~5,500). The market cap filter
        is applied separately via get_universe_with_market_cap_filter()
        which calls the profile endpoint for each.

        For daily runs, the handler should use a pre-cached universe
        (stored in S3) that was built by a weekly universe refresh job.
        """
        print("  Fetching NASDAQ traded symbols file...")
        response = http_requests.get(self.NASDAQ_TRADED_URL, timeout=30)
        response.raise_for_status()

        lines = response.text.strip().split("\n")
        stocks = []

        for line in lines[1:]:  # Skip header
            fields = line.split("|")
            if len(fields) < 8:
                continue

            traded = fields[0]
            symbol = fields[1]
            name = fields[2]
            exchange = fields[3]
            etf = fields[5]
            test_issue = fields[7]

            # Only actively traded, non-test securities
            if traded != "Y" or test_issue != "N":
                continue

            # Only stocks, not ETFs
            if etf != "N":
                continue

            # Major US exchanges only
            if exchange not in ("N", "Q", "P", "Z"):
                continue

            # Skip symbols with dots or dashes (classes, warrants encoded this way)
            if "." in symbol or "-" in symbol:
                continue

            # Skip warrants, rights, units, preferred by name
            name_lower = name.lower()
            if any(x in name_lower for x in [
                "warrant", "right", "unit", "preferred",
                "debenture", "note", "depositary"
            ]):
                continue

            stocks.append(symbol)

        print(f"  NASDAQ file: {len(stocks)} common stocks after filtering")
        return sorted(stocks)

    def get_universe_with_market_cap_filter(
        self, symbols: list[str], min_market_cap: Optional[float] = None
    ) -> list[str]:
        """
        Filter a symbol list by market cap using FMP profile endpoint.

        This is the expensive operation (one API call per symbol).
        Should be run weekly, with results cached in S3.

        Args:
            symbols: Full symbol list from get_stock_universe()
            min_market_cap: Override minimum market cap (defaults to constructor value)

        Returns:
            Symbols with market cap >= threshold
        """
        threshold = min_market_cap or self._min_market_cap
        qualifying = []

        print(f"  Filtering {len(symbols)} stocks by market cap >= ${threshold:,.0f}...")

        for i, symbol in enumerate(symbols):
            try:
                profile = self._fetch_profile(symbol)
                if profile:
                    mc = profile.get("marketCap", 0) or 0
                    if mc >= threshold:
                        qualifying.append(symbol)

                if (i + 1) % 100 == 0:
                    print(f"    Checked {i+1}/{len(symbols)}, "
                          f"{len(qualifying)} qualifying so far...")

            except Exception as e:
                # Don't let one failure stop the whole scan
                continue

            # Rate limiting
            time.sleep(self.get_rate_limit_delay())

        print(f"  Market cap filter: {len(qualifying)}/{len(symbols)} stocks qualify")
        return qualifying

    def get_fundamentals(self, symbol: str) -> Optional[StockFundamentals]:
        """
        Fetch fundamentals by combining multiple FMP endpoints.

        Endpoints used (all work on free tier):
        - /stable/profile: price, market cap, sector
        - /stable/ratios-ttm: valuation ratios, margins, balance sheet
        - /stable/financial-growth: EPS growth, revenue growth
        - /stable/price-target-consensus: analyst target prices

        Each endpoint is a separate API call. For ~1,000 stocks this is
        ~4,000 calls/day. Bandwidth: ~1,000 × (2.7+3.0+1.5+0.5)KB ≈ 7.7MB/day.
        """
        try:
            profile = self._fetch_profile(symbol)
            ratios = self._fetch_ratios(symbol)
            growth = self._fetch_growth(symbol)
            price_target = self._fetch_price_target(symbol)

            if not profile and not ratios:
                return None

            p = profile or {}
            r = ratios or {}
            g = growth or {}
            pt = price_target or {}

            # Calculate target price upside from consensus target
            current_price = p.get("price")
            target_consensus = pt.get("targetConsensus")
            target_upside = None
            if target_consensus and current_price and current_price > 0:
                target_upside = (target_consensus - current_price) / current_price

            return StockFundamentals(
                # Identity
                symbol=symbol,
                company_name=p.get("companyName") or "",
                sector=p.get("sector") or "",
                industry=p.get("industry") or "",
                market_cap=p.get("marketCap"),
                price=current_price,
                exchange=p.get("exchange") or "",

                # Valuation
                pe_ratio=r.get("priceToEarningsRatioTTM"),
                forward_pe=r.get("forwardPriceToEarningsGrowthRatioTTM"),
                peg_ratio=r.get("priceToEarningsGrowthRatioTTM"),
                price_to_fcf=r.get("priceToFreeCashFlowRatioTTM"),
                price_to_book=r.get("priceToBookRatioTTM"),
                price_to_sales=r.get("priceToSalesRatioTTM"),
                ev_to_ebitda=r.get("enterpriseValueMultipleTTM"),

                # Balance Sheet
                debt_to_equity=r.get("debtToEquityRatioTTM"),
                quick_ratio=r.get("quickRatioTTM"),
                current_ratio=r.get("currentRatioTTM"),

                # Profitability
                operating_margin=r.get("operatingProfitMarginTTM"),
                net_profit_margin=r.get("netProfitMarginTTM"),
                gross_margin=r.get("grossProfitMarginTTM"),
                return_on_equity=r.get("returnOnEquityTTM"),

                # Growth (from /stable/financial-growth)
                eps_growth_yoy=g.get("epsgrowth"),
                revenue_growth_yoy=g.get("revenueGrowth"),
                revenue_growth_qoq=None,  # Quarterly growth needs quarterly endpoint
                earnings_growth_qoq=None,
                estimated_lt_growth=None,  # Requires paid analyst-estimates endpoint

                # Analyst (from /stable/price-target-consensus)
                analyst_recommendation=None,  # Requires paid tier
                analyst_target_price=target_consensus,
                target_price_upside=target_upside,

                # Institutional
                institutional_ownership=None,
                institutional_transactions=None,

                # Per-share
                eps=r.get("netIncomePerShareTTM"),
                revenue_per_share=r.get("revenuePerShareTTM"),
                fcf_per_share=r.get("freeCashFlowPerShareTTM"),

                # Dividend
                dividend_yield=r.get("dividendYieldTTM"),
                payout_ratio=r.get("dividendPayoutRatioTTM"),
            )

        except http_requests.RequestException as e:
            print(f"  Warning: FMP API error for {symbol}: {e}")
            return None

    def get_fundamentals_batch(self, symbols: list[str]) -> list[StockFundamentals]:
        """
        Fetch fundamentals for multiple stocks with progress logging.
        """
        results = []
        total = len(symbols)

        for i, symbol in enumerate(symbols):
            try:
                data = self.get_fundamentals(symbol)
                if data:
                    results.append(data)
                    if (i + 1) % 25 == 0 or i == 0:
                        print(f"  [{i+1}/{total}] {symbol}: OK "
                              f"(P/E={data.pe_ratio}, D/E={data.debt_to_equity})")
                else:
                    if (i + 1) % 25 == 0:
                        print(f"  [{i+1}/{total}] Progress: {len(results)} enriched so far")
            except Exception as e:
                print(f"  [{i+1}/{total}] {symbol}: ERROR - {e}")

            # Rate limiting between requests
            if i < total - 1:
                time.sleep(self.get_rate_limit_delay())

        return results

    def get_rate_limit_delay(self) -> float:
        """
        FMP free tier: no published rate limit, but be respectful.
        0.15s = ~6-7 requests/second. Conservative enough to avoid throttling,
        fast enough to scan 1,000 stocks in ~5 minutes (2 calls each).
        """
        return 0.15

    def _fetch_profile(self, symbol: str) -> Optional[dict]:
        """Fetch company profile from /stable/profile."""
        url = f"{self.FMP_BASE_URL}/profile"
        response = http_requests.get(
            url, params={"symbol": symbol, "apikey": self._api_key}, timeout=15
        )
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and data:
                return data[0]
        return None

    def _fetch_ratios(self, symbol: str) -> Optional[dict]:
        """Fetch TTM ratios from /stable/ratios-ttm."""
        url = f"{self.FMP_BASE_URL}/ratios-ttm"
        response = http_requests.get(
            url, params={"symbol": symbol, "apikey": self._api_key}, timeout=15
        )
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and data:
                return data[0]
        return None

    def _fetch_growth(self, symbol: str) -> Optional[dict]:
        """
        Fetch financial growth data from /stable/financial-growth.

        Returns year-over-year growth rates:
        - epsgrowth: EPS growth
        - revenueGrowth: revenue growth
        - netIncomeGrowth: net income growth
        - operatingIncomeGrowth: operating income growth
        """
        url = f"{self.FMP_BASE_URL}/financial-growth"
        response = http_requests.get(
            url,
            params={"symbol": symbol, "period": "annual", "limit": 1, "apikey": self._api_key},
            timeout=15,
        )
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and data:
                return data[0]
        return None

    def _fetch_price_target(self, symbol: str) -> Optional[dict]:
        """
        Fetch analyst price target consensus from /stable/price-target-consensus.

        Returns:
        - targetHigh: highest analyst target
        - targetLow: lowest analyst target
        - targetConsensus: average target
        - targetMedian: median target
        """
        url = f"{self.FMP_BASE_URL}/price-target-consensus"
        response = http_requests.get(
            url, params={"symbol": symbol, "apikey": self._api_key}, timeout=15
        )
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and data:
                return data[0]
        return None
