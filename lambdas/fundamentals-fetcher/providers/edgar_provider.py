"""
SEC EDGAR Data Provider
========================
Free, unlimited, official US government financial data.

Single responsibility: Fetch bulk financial metrics for ALL companies
via the EDGAR Frames API (~10 requests for the entire US market).

Does NOT handle price data — that's the enrichment Lambda's job (Twelve Data).

EDGAR API docs: https://www.sec.gov/edgar/sec-api-documentation
"""

import time
from datetime import datetime, timezone
from typing import Optional

import requests as http_requests

from providers.base import DataProvider, StockFundamentals, ProviderError


class EdgarProvider(DataProvider):
    """
    Fetches bulk financial data from SEC EDGAR's Frames API.

    The Frames API returns a single metric for ALL reporting companies
    in one request. We fetch ~10 metrics and join them by CIK to build
    complete fundamental profiles.
    """

    EDGAR_BASE_URL = "https://data.sec.gov/api/xbrl/frames"
    TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

    # SEC requires a User-Agent header with contact info
    HEADERS = {
        "User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com",
        "Accept-Encoding": "gzip, deflate",
    }

    # Metrics we fetch from the Frames API
    # Format: (XBRL tag, unit, is_instant)
    # Duration (income statement): CY{year}
    # Instant (balance sheet): CY{year}Q4I
    FRAME_METRICS = {
        "net_income": ("NetIncomeLoss", "USD", False),
        "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "USD", False),
        "operating_income": ("OperatingIncomeLoss", "USD", False),
        "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities", "USD", False),
        "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "USD", False),
        "stockholders_equity": ("StockholdersEquity", "USD", True),
        "long_term_debt": ("LongTermDebt", "USD", True),
        "assets_current": ("AssetsCurrent", "USD", True),
        "liabilities_current": ("LiabilitiesCurrent", "USD", True),
        "shares_outstanding": ("CommonStockSharesOutstanding", "shares", True),
        "inventory": ("InventoryNet", "USD", True),
        "cash": ("CashAndCashEquivalentsAtCarryingValue", "USD", True),
        "interest_expense": ("InterestExpense", "USD", False),
    }

    # Alternative revenue tags (companies use different XBRL tags)
    REVENUE_ALTERNATIVES = [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ]

    def __init__(self, fiscal_year: int = 2025, min_market_cap: float = 300_000_000, **kwargs):
        """
        Initialize the EDGAR provider.

        Args:
            fiscal_year: Legacy fallback year (no longer used — quarters are computed dynamically)
            min_market_cap: Minimum market cap filter (applied after price enrichment)
        """
        self._fiscal_year = fiscal_year
        self._min_market_cap = min_market_cap
        self._ticker_to_cik: dict[str, int] = {}
        self._cik_to_ticker: dict[int, str] = {}
        self._cik_to_company: dict[int, str] = {}

    @property
    def name(self) -> str:
        return "SEC EDGAR"

    def _load_ticker_mapping(self):
        """
        Load the SEC's official ticker → CIK mapping file.

        This file maps all known tickers to their CIK (Central Index Key),
        which is the unique identifier SEC uses for each company.
        """
        if self._ticker_to_cik:
            return  # Already loaded

        print("  Loading SEC ticker → CIK mapping...")
        response = http_requests.get(self.TICKERS_URL, headers=self.HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()

        for entry in data.values():
            ticker = entry["ticker"]
            cik = entry["cik_str"]
            title = entry["title"]
            self._ticker_to_cik[ticker] = cik
            self._cik_to_ticker[cik] = ticker
            self._cik_to_company[cik] = title

        print(f"  Loaded {len(self._ticker_to_cik)} ticker mappings")

    def _fetch_frame(self, xbrl_tag: str, unit: str, is_instant: bool, year: int = None, quarter: str = None) -> dict[int, float]:
        """
        Fetch a single metric for all companies via the Frames API.

        Args:
            xbrl_tag: The XBRL taxonomy tag (e.g., "NetIncomeLoss")
            unit: The unit (e.g., "USD" or "shares")
            is_instant: True for balance sheet items (point-in-time),
                       False for income statement items (period/duration)
            year: Fiscal year (used when quarter is None)
            quarter: Specific frame string override (e.g., "CY2026Q1", "CY2025Q4I")
                    If provided, year and is_instant are ignored.

        Returns:
            Dict mapping CIK → value for all companies that reported this metric
        """
        if quarter:
            frame = quarter
        else:
            fiscal_year = year or self._fiscal_year
            if is_instant:
                frame = f"CY{fiscal_year}Q4I"
            else:
                frame = f"CY{fiscal_year}"

        url = f"{self.EDGAR_BASE_URL}/us-gaap/{xbrl_tag}/{unit}/{frame}.json"

        try:
            response = http_requests.get(url, headers=self.HEADERS, timeout=30)
            if response.status_code != 200:
                return {}

            data = response.json()
            companies = data.get("data", [])

            # Build CIK → value mapping
            result = {}
            for company in companies:
                cik = company.get("cik")
                val = company.get("val")
                if cik is not None and val is not None:
                    result[cik] = val

            return result

        except Exception as e:
            print(f"    Warning: Failed to fetch frame {xbrl_tag}/{frame}: {e}")
            return {}

    def _fetch_all_frames(self) -> dict[str, dict[int, float]]:
        """
        Fetch all metrics from the Frames API using proper TTM for income statement items.

        All periods are computed dynamically from today's date:
        - Discovers the latest quarter with >= 4000 companies reporting
        - TTM = sum of 4 most recent quarters (with annual+Q1 fallback for sparse Q4)
        - Prior TTM = same derivation, one year earlier (for YoY growth)
        - Balance sheet = latest quarter instant + prior quarter fallback
        - Shares = CommonStockSharesOutstanding instant + WeightedAverageDiluted fallback

        No hardcoded dates — adapts automatically as new quarters become available.
        """
        print(f"  Fetching EDGAR frames for TTM calculation...")
        all_data = {}

        # =============================================
        # INCOME STATEMENT: Build TTM per company
        # =============================================
        # Dynamically determine the 4 most recent quarters based on current date.
        # Then validate which ones have adequate EDGAR coverage.
        # TTM = sum of most recent 4 quarters with data.

        income_tags = {
            "net_income": "NetIncomeLoss",
            "operating_income": "OperatingIncomeLoss",
            "operating_cash_flow": "NetCashProvidedByUsedInOperatingActivities",
            "capex": "PaymentsToAcquirePropertyPlantAndEquipment",
            "interest_expense": "InterestExpense",
        }

        # Revenue needs multiple tags merged — handled separately below
        REVENUE_TAGS_FOR_TTM = [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
        ]

        # Determine latest available quarter with adequate coverage
        # by testing NetIncomeLoss frames from newest to oldest
        from datetime import date
        today = date.today()
        current_year = today.year
        current_month = today.month

        # Generate candidate quarters (most recent 6, newest first)
        candidate_quarters = []
        y, q = current_year, ((current_month - 1) // 3)  # q=0 means Q1 just ended
        if q == 0:
            y -= 1
            q = 4
        for _ in range(6):
            candidate_quarters.append(f"CY{y}Q{q}")
            q -= 1
            if q == 0:
                q = 4
                y -= 1

        # Find the latest quarter with >= 4000 companies (adequate coverage)
        print(f"  Finding latest quarter with coverage (candidates: {candidate_quarters[:4]})...")
        latest_quarter = None
        for cq in candidate_quarters:
            test_data = self._fetch_frame("NetIncomeLoss", "USD", False, quarter=cq)
            count = len(test_data)
            print(f"    {cq}: {count} companies")
            time.sleep(0.1)
            if count >= 4000:
                latest_quarter = cq
                break

        if not latest_quarter:
            # Fallback: use the first candidate with any data
            latest_quarter = candidate_quarters[0]
            print(f"    Warning: no quarter with 4000+ companies, using {latest_quarter}")

        # Build the 4 TTM quarters from the latest
        # Parse latest_quarter (e.g., "CY2026Q1") → year=2026, quarter=1
        lq_year = int(latest_quarter[2:6])
        lq_q = int(latest_quarter[7])

        quarterly_periods = []
        y, q = lq_year, lq_q
        for _ in range(4):
            quarterly_periods.append(f"CY{y}Q{q}")
            q -= 1
            if q == 0:
                q = 4
                y -= 1

        # Prior TTM: same 4 quarters, one year earlier
        prior_periods = [f"CY{int(p[2:6])-1}Q{p[7]}" for p in quarterly_periods]
        # Annual frames for fallback derivation
        annual_period = f"CY{lq_year - 1}" if lq_q < 4 else f"CY{lq_year}"
        prior_annual_period = f"CY{lq_year - 2}" if lq_q < 4 else f"CY{lq_year - 1}"
        q1_current_year = f"CY{lq_year}Q1"
        q1_prior_year = f"CY{lq_year - 1}Q1"

        print(f"  TTM periods: {quarterly_periods}")
        print(f"  Prior TTM derivation: {annual_period} + {q1_current_year} - {q1_prior_year}")

        for metric_name, xbrl_tag in income_tags.items():
            # Fetch all quarterly frames
            quarterly_data = {}
            for q in quarterly_periods:
                frame = self._fetch_frame(xbrl_tag, "USD", False, quarter=q)
                quarterly_data[q] = frame
                time.sleep(0.1)

            # Fetch annual + Q1 for fallback derivation
            annual_data = self._fetch_frame(xbrl_tag, "USD", False, quarter=annual_period)
            time.sleep(0.1)
            q1_data = self._fetch_frame(xbrl_tag, "USD", False, quarter=q1_current_year)
            time.sleep(0.1)
            q1_prior_data = self._fetch_frame(xbrl_tag, "USD", False, quarter=q1_prior_year)
            time.sleep(0.1)

            # Compute TTM for each company
            ttm_values = {}
            all_ciks = set()
            for q_data in quarterly_data.values():
                all_ciks.update(q_data.keys())
            all_ciks.update(annual_data.keys())

            for cik in all_ciks:
                # Try direct sum of 4 quarters
                q_vals = [quarterly_data[q].get(cik) for q in quarterly_periods]
                if all(v is not None for v in q_vals):
                    ttm_values[cik] = sum(q_vals)
                elif annual_data.get(cik) is not None and q1_data.get(cik) is not None and q1_prior_data.get(cik) is not None:
                    # Fallback: annual + latest_Q1 - prior_Q1
                    ttm_values[cik] = annual_data[cik] + q1_data[cik] - q1_prior_data[cik]

            all_data[metric_name] = ttm_values
            print(f"    {metric_name} (TTM): {len(ttm_values)} companies")

        # REVENUE TTM: merge multiple tags for maximum coverage
        # Companies use different tags — we fetch both and merge per-quarter
        # before computing TTM, ensuring we don't miss companies.
        revenue_ttm = {}
        for q in quarterly_periods:
            merged_quarter = {}
            for rev_tag in REVENUE_TAGS_FOR_TTM:
                frame = self._fetch_frame(rev_tag, "USD", False, quarter=q)
                for cik, val in frame.items():
                    if cik not in merged_quarter:
                        merged_quarter[cik] = val
                time.sleep(0.1)
            # Accumulate into TTM
            for cik, val in merged_quarter.items():
                if cik not in revenue_ttm:
                    revenue_ttm[cik] = 0
                revenue_ttm[cik] += val

        # Fallback: annual + Q1 derivation for companies missing quarterly data
        rev_annual = {}
        rev_q1_current = {}
        rev_q1_prior = {}
        for rev_tag in REVENUE_TAGS_FOR_TTM:
            frame = self._fetch_frame(rev_tag, "USD", False, quarter=annual_period)
            for cik, val in frame.items():
                if cik not in rev_annual:
                    rev_annual[cik] = val
            time.sleep(0.1)
            frame = self._fetch_frame(rev_tag, "USD", False, quarter=q1_current_year)
            for cik, val in frame.items():
                if cik not in rev_q1_current:
                    rev_q1_current[cik] = val
            time.sleep(0.1)
            frame = self._fetch_frame(rev_tag, "USD", False, quarter=q1_prior_year)
            for cik, val in frame.items():
                if cik not in rev_q1_prior:
                    rev_q1_prior[cik] = val
            time.sleep(0.1)

        for cik in set(rev_annual.keys()) & set(rev_q1_current.keys()) & set(rev_q1_prior.keys()):
            if cik not in revenue_ttm:
                revenue_ttm[cik] = rev_annual[cik] + rev_q1_current[cik] - rev_q1_prior[cik]

        all_data["revenue"] = revenue_ttm
        print(f"    revenue (TTM, multi-tag): {len(revenue_ttm)} companies")

        # Prior TTM for YoY growth
        # Prior TTM for YoY growth: same derivation, shifted one year back
        # prior_annual_period and q1_prior_year already computed above dynamically
        for metric_name, xbrl_tags in [("net_income", ["NetIncomeLoss"]),
                                        ("revenue", REVENUE_TAGS_FOR_TTM)]:
            # Merge all tags for this metric
            prior_ann_data = {}
            q1_prior_merged = {}
            q1_2yb_data = {}
            q1_2yb = f"CY{lq_year - 2}Q1"

            for tag in xbrl_tags:
                frame = self._fetch_frame(tag, "USD", False, quarter=prior_annual_period)
                for cik, val in frame.items():
                    if cik not in prior_ann_data:
                        prior_ann_data[cik] = val
                time.sleep(0.1)

                frame = self._fetch_frame(tag, "USD", False, quarter=q1_prior_year)
                for cik, val in frame.items():
                    if cik not in q1_prior_merged:
                        q1_prior_merged[cik] = val
                time.sleep(0.1)

                frame = self._fetch_frame(tag, "USD", False, quarter=q1_2yb)
                for cik, val in frame.items():
                    if cik not in q1_2yb_data:
                        q1_2yb_data[cik] = val
                time.sleep(0.1)

            # Prior TTM = prior_annual + q1_prior_year - q1_two_years_back
            prev_ttm = {}
            for cik in set(prior_ann_data.keys()) & set(q1_prior_merged.keys()) & set(q1_2yb_data.keys()):
                prev_ttm[cik] = prior_ann_data[cik] + q1_prior_merged[cik] - q1_2yb_data[cik]

            all_data[f"prev_{metric_name}"] = prev_ttm
            print(f"    prev_{metric_name} (prior TTM): {len(prev_ttm)} companies")

        # =============================================
        # BALANCE SHEET: latest quarter instant + prior quarter fallback
        # =============================================
        # Instant frames use "I" suffix (e.g., CY2026Q1I)
        balance_primary = f"{latest_quarter}I"
        # Previous quarter for fallback
        prev_q_year, prev_q_num = lq_year, lq_q - 1
        if prev_q_num == 0:
            prev_q_num = 4
            prev_q_year -= 1
        balance_fallback = f"CY{prev_q_year}Q{prev_q_num}I"

        balance_metrics = {
            "stockholders_equity": ("StockholdersEquity", "USD"),
            "liabilities": ("Liabilities", "USD"),
            "assets_current": ("AssetsCurrent", "USD"),
            "liabilities_current": ("LiabilitiesCurrent", "USD"),
            "shares_outstanding": ("CommonStockSharesOutstanding", "shares"),
            "inventory": ("InventoryNet", "USD"),
            "cash": ("CashAndCashEquivalentsAtCarryingValue", "USD"),
        }

        for metric_name, (xbrl_tag, unit) in balance_metrics.items():
            # Primary: latest quarter instant
            frame_data = self._fetch_frame(xbrl_tag, unit, False, quarter=balance_primary)
            # Fallback: fill gaps from prior quarter instant
            fallback_data = self._fetch_frame(xbrl_tag, unit, False, quarter=balance_fallback)
            for cik, val in fallback_data.items():
                if cik not in frame_data:
                    frame_data[cik] = val
            all_data[metric_name] = frame_data
            print(f"    {metric_name} ({balance_primary}+{balance_fallback}): {len(frame_data)} companies")
            time.sleep(0.1)

        # Shares fallback: many companies report diluted weighted average shares
        # (a duration metric) instead of CommonStockSharesOutstanding (instant).
        shares_data = all_data.get("shares_outstanding", {})
        diluted_shares = self._fetch_frame(
            "WeightedAverageNumberOfDilutedSharesOutstanding", "shares", False, quarter=latest_quarter
        )
        time.sleep(0.1)
        added_shares = 0
        for cik, val in diluted_shares.items():
            if cik not in shares_data:
                shares_data[cik] = val
                added_shares += 1
        all_data["shares_outstanding"] = shares_data
        print(f"    shares_outstanding (+ diluted fallback): +{added_shares} = {len(shares_data)} total")

        return all_data

    def get_stock_universe(self) -> list[str]:
        """
        Return all tickers that have financial data in EDGAR.

        Discovers the latest quarter with adequate coverage dynamically,
        then returns all companies that reported Net Income in that quarter.
        """
        self._load_ticker_mapping()

        # Determine latest quarter with coverage (same logic as _fetch_all_frames)
        from datetime import date
        today = date.today()
        y, q = today.year, ((today.month - 1) // 3)
        if q == 0:
            y -= 1
            q = 4
        # Try up to 4 candidates
        latest_q = None
        for _ in range(4):
            candidate = f"CY{y}Q{q}"
            test_data = self._fetch_frame("NetIncomeLoss", "USD", False, quarter=candidate)
            if len(test_data) >= 4000:
                latest_q = candidate
                break
            q -= 1
            if q == 0:
                q = 4
                y -= 1
            import time as _time
            _time.sleep(0.1)

        if not latest_q:
            latest_q = f"CY{today.year}Q{max(1, (today.month - 1) // 3)}"

        # Fetch net income frame to identify universe
        net_income_data = self._fetch_frame("NetIncomeLoss", "USD", False, quarter=latest_q)

        # Map CIKs back to tickers
        symbols = []
        for cik in net_income_data:
            ticker = self._cik_to_ticker.get(cik)
            if ticker:
                symbols.append(ticker)

        print(f"  Universe: {len(symbols)} stocks with {latest_q} data")
        return sorted(symbols)

    def get_fundamentals(self, symbol: str) -> Optional[StockFundamentals]:
        """
        Get fundamentals for a single stock.

        Note: This is less efficient than get_fundamentals_batch() because
        EDGAR's Frames API is designed for bulk retrieval. For single stocks,
        we'd need to use the per-company endpoint (companyfacts).

        For the pipeline, always prefer get_fundamentals_batch().
        """
        self._load_ticker_mapping()
        cik = self._ticker_to_cik.get(symbol)
        if not cik:
            return None

        # For single stock, fetch from companyfacts endpoint
        cik_padded = str(cik).zfill(10)
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_padded}.json"

        try:
            response = http_requests.get(url, headers=self.HEADERS, timeout=30)
            if response.status_code != 200:
                return None

            facts = response.json()
            us_gaap = facts.get("facts", {}).get("us-gaap", {})

            # Extract latest annual values
            def get_latest_annual(tag, unit="USD"):
                if tag not in us_gaap:
                    return None
                values = us_gaap[tag].get("units", {}).get(unit, [])
                annual = [v for v in values if v.get("form") == "10-K"]
                return annual[-1]["val"] if annual else None

            net_income = get_latest_annual("NetIncomeLoss")
            equity = get_latest_annual("StockholdersEquity")
            debt = get_latest_annual("LongTermDebt")
            assets_current = get_latest_annual("AssetsCurrent")
            liabilities_current = get_latest_annual("LiabilitiesCurrent")
            shares = get_latest_annual("CommonStockSharesOutstanding", "shares")
            operating_income = get_latest_annual("OperatingIncomeLoss")
            inventory = get_latest_annual("InventoryNet")

            # Try multiple revenue tags
            revenue = None
            for rev_tag in self.REVENUE_ALTERNATIVES:
                revenue = get_latest_annual(rev_tag)
                if revenue:
                    break

            # Calculate ratios
            eps = net_income / shares if net_income and shares and shares > 0 else None
            debt_to_equity = debt / equity if debt and equity and equity > 0 else None
            current_ratio = assets_current / liabilities_current if assets_current and liabilities_current and liabilities_current > 0 else None
            quick_ratio = ((assets_current or 0) - (inventory or 0)) / liabilities_current if assets_current and liabilities_current and liabilities_current > 0 else None
            operating_margin = operating_income / revenue if operating_income and revenue and revenue > 0 else None
            net_margin = net_income / revenue if net_income and revenue and revenue > 0 else None

            return StockFundamentals(
                symbol=symbol,
                company_name=self._cik_to_company.get(cik, ""),
                sector="",  # EDGAR doesn't provide sector (we'd add from NASDAQ file)
                industry="",
                market_cap=None,  # Needs price data (separate source)
                price=None,  # Needs price data
                exchange="",
                pe_ratio=None,  # Needs price / EPS
                forward_pe=None,
                peg_ratio=None,  # Needs growth + PE
                price_to_fcf=None,  # Needs price
                price_to_book=None,
                price_to_sales=None,
                ev_to_ebitda=None,
                debt_to_equity=debt_to_equity,
                quick_ratio=quick_ratio,
                current_ratio=current_ratio,
                operating_margin=operating_margin,
                net_profit_margin=net_margin,
                gross_margin=None,
                return_on_equity=net_income / equity if net_income and equity and equity > 0 else None,
                eps_growth_yoy=None,  # Needs prior year comparison
                revenue_growth_yoy=None,
                revenue_growth_qoq=None,
                earnings_growth_qoq=None,
                estimated_lt_growth=None,
                analyst_recommendation=None,
                analyst_target_price=None,
                target_price_upside=None,
                institutional_ownership=None,
                institutional_transactions=None,
                eps=eps,
                revenue_per_share=revenue / shares if revenue and shares and shares > 0 else None,
                fcf_per_share=None,
                dividend_yield=None,
                payout_ratio=None,
            )

        except Exception as e:
            print(f"  Warning: EDGAR error for {symbol}: {e}")
            return None

    def get_fundamentals_batch(self, symbols: list[str]) -> list[StockFundamentals]:
        """
        Fetch fundamentals for multiple stocks using the bulk Frames API.

        This is the efficient path — ~10 API calls regardless of how many
        stocks are in the list. We fetch all metrics in bulk, then filter
        to only the requested symbols.
        """
        self._load_ticker_mapping()

        # Fetch all metrics in bulk (~10 requests for entire market)
        all_frames = self._fetch_all_frames()

        # Build CIK set for requested symbols
        requested_ciks = {}
        for symbol in symbols:
            cik = self._ticker_to_cik.get(symbol)
            if cik:
                requested_ciks[cik] = symbol

        # Assemble per-company fundamentals
        results = []
        for cik, symbol in requested_ciks.items():
            net_income = all_frames.get("net_income", {}).get(cik)
            revenue = all_frames.get("revenue", {}).get(cik)
            operating_income = all_frames.get("operating_income", {}).get(cik)
            operating_cf = all_frames.get("operating_cash_flow", {}).get(cik)
            capex = all_frames.get("capex", {}).get(cik)
            equity = all_frames.get("stockholders_equity", {}).get(cik)
            liabilities = all_frames.get("liabilities", {}).get(cik)
            assets_current = all_frames.get("assets_current", {}).get(cik)
            liabilities_current = all_frames.get("liabilities_current", {}).get(cik)
            shares = all_frames.get("shares_outstanding", {}).get(cik)
            inventory = all_frames.get("inventory", {}).get(cik)
            interest_expense = all_frames.get("interest_expense", {}).get(cik)

            # Prior year data for growth calculations
            prev_net_income = all_frames.get("prev_net_income", {}).get(cik)
            prev_revenue = all_frames.get("prev_revenue", {}).get(cik)

            # Need at least net income and equity to be useful
            if net_income is None and equity is None:
                continue

            # Calculate ratios
            eps = net_income / shares if net_income and shares and shares > 0 else None
            # D/E: (Total Liabilities - Current Liabilities) / Equity
            # Uses broadest tags (4,800+ coverage) instead of specific LongTermDebt (1,600)
            noncurrent_liabilities = None
            if liabilities is not None and liabilities_current is not None:
                noncurrent_liabilities = liabilities - liabilities_current
            debt_to_equity = noncurrent_liabilities / equity if noncurrent_liabilities is not None and equity and equity > 0 else None
            current_ratio = assets_current / liabilities_current if assets_current and liabilities_current and liabilities_current > 0 else None
            quick_ratio = ((assets_current or 0) - (inventory or 0)) / liabilities_current if assets_current and liabilities_current and liabilities_current > 0 else None
            operating_margin = operating_income / revenue if operating_income and revenue and revenue > 0 else None
            net_margin = net_income / revenue if net_income and revenue and revenue > 0 else None
            roe = net_income / equity if net_income and equity and equity > 0 else None

            # Growth (calculated locally from prior year comparison)
            eps_growth = None
            if net_income and prev_net_income and shares and shares > 0 and prev_net_income != 0:
                prev_eps = prev_net_income / shares
                curr_eps = net_income / shares
                if prev_eps > 0:
                    eps_growth = (curr_eps - prev_eps) / prev_eps

            revenue_growth = None
            if revenue and prev_revenue and prev_revenue > 0:
                revenue_growth = (revenue - prev_revenue) / prev_revenue

            # Free cash flow per share (operating cash flow - capex) / shares
            fcf_per_share = None
            if operating_cf and shares and shares > 0:
                fcf = operating_cf - (capex or 0)  # capex is typically positive in EDGAR
                fcf_per_share = fcf / shares

            # Interest Coverage Ratio: Operating Income / Interest Expense
            # Measures how easily a company can pay interest on its debt.
            # > 3.0 = comfortable, > 5.0 = strong, < 1.0 = can't cover interest
            interest_coverage = None
            if operating_income and interest_expense and interest_expense > 0:
                interest_coverage = operating_income / interest_expense

            stock = StockFundamentals(
                symbol=symbol,
                company_name=self._cik_to_company.get(cik, ""),
                sector="",
                industry="",
                market_cap=None,
                price=None,
                exchange="",
                pe_ratio=None,  # Filled by enrichment (Price / EPS)
                forward_pe=None,  # Requires analyst estimates (Finnhub)
                peg_ratio=None,  # Filled by enrichment (P/E / growth)
                price_to_fcf=None,  # Filled by enrichment (Price / FCF per share)
                price_to_book=None,
                price_to_sales=None,
                ev_to_ebitda=None,
                debt_to_equity=debt_to_equity,
                quick_ratio=quick_ratio,
                current_ratio=current_ratio,
                interest_coverage_ratio=interest_coverage,
                operating_margin=operating_margin,
                net_profit_margin=net_margin,
                gross_margin=None,
                return_on_equity=roe,
                eps_growth_yoy=eps_growth,
                revenue_growth_yoy=revenue_growth,
                revenue_growth_qoq=None,
                earnings_growth_qoq=None,
                estimated_lt_growth=None,  # Requires analyst estimates (Finnhub)
                analyst_recommendation=None,
                analyst_target_price=None,
                target_price_upside=None,  # Requires analyst target (Finnhub)
                institutional_ownership=None,
                institutional_transactions=None,
                eps=eps,
                revenue_per_share=revenue / shares if revenue and shares and shares > 0 else None,
                fcf_per_share=fcf_per_share,
                dividend_yield=None,
                payout_ratio=None,
            )
            results.append(stock)

        print(f"  Built fundamentals for {len(results)}/{len(symbols)} stocks")
        return results

    def get_rate_limit_delay(self) -> float:
        """SEC asks for max 10 requests/second. 0.12s = ~8 req/sec (safe)."""
        return 0.12
