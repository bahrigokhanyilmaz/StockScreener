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
            fiscal_year: Base year for balance sheet data (instant items use CY{year}Q4I)
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

        TTM (Trailing Twelve Months) for income metrics:
          Primary: CY2026Q1 + CY2025Q4 + CY2025Q3 + CY2025Q2 (direct sum of 4 quarters)
          Fallback: CY2025 (annual) + CY2026Q1 - CY2025Q1 (for companies without explicit Q4)
          Both produce the same result: net income for the most recent 4 quarters.

        Balance sheet (instant items): CY2026Q1I with CY2025Q4I fallback.
        Growth: TTM current vs TTM prior (rolling 4Q vs prior rolling 4Q).
        """
        print(f"  Fetching EDGAR frames for TTM calculation...")
        all_data = {}

        # =============================================
        # INCOME STATEMENT: Build TTM per company
        # =============================================
        # We need: CY2026Q1, CY2025Q3, CY2025Q2 (quarterly)
        #          CY2025Q4 (sparse) + CY2025 annual + CY2025Q1 (for derivation)
        income_tags = {
            "net_income": "NetIncomeLoss",
            "revenue": "RevenueFromContractWithCustomerExcludingAssessedTax",
            "operating_income": "OperatingIncomeLoss",
            "operating_cash_flow": "NetCashProvidedByUsedInOperatingActivities",
            "capex": "PaymentsToAcquirePropertyPlantAndEquipment",
            "interest_expense": "InterestExpense",
        }

        # Quarterly frames needed
        quarterly_periods = ["CY2026Q1", "CY2025Q4", "CY2025Q3", "CY2025Q2"]
        # For fallback derivation
        annual_period = "CY2025"
        q1_prior = "CY2025Q1"

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
            q1_data = self._fetch_frame(xbrl_tag, "USD", False, quarter=q1_prior)
            time.sleep(0.1)

            # Compute TTM for each company
            ttm_values = {}
            all_ciks = set()
            for q_data in quarterly_data.values():
                all_ciks.update(q_data.keys())
            all_ciks.update(annual_data.keys())

            for cik in all_ciks:
                q1_26 = quarterly_data["CY2026Q1"].get(cik)
                q4_25 = quarterly_data["CY2025Q4"].get(cik)
                q3_25 = quarterly_data["CY2025Q3"].get(cik)
                q2_25 = quarterly_data["CY2025Q2"].get(cik)

                if q1_26 is not None and q4_25 is not None and q3_25 is not None and q2_25 is not None:
                    # Direct: sum of 4 explicit quarters
                    ttm_values[cik] = q1_26 + q4_25 + q3_25 + q2_25
                elif q1_26 is not None and annual_data.get(cik) is not None and q1_data.get(cik) is not None:
                    # Fallback: annual_2025 + Q1_2026 - Q1_2025 = TTM ending Q1 2026
                    ttm_values[cik] = annual_data[cik] + q1_26 - q1_data[cik]
                # If neither works, company is excluded (missing data)

            all_data[metric_name] = ttm_values
            print(f"    {metric_name} (TTM): {len(ttm_values)} companies")

        # Also store Q1 values separately for YoY growth calculation
        # Growth = (TTM current - TTM prior) / TTM prior
        # TTM prior = CY2025Q1 + CY2024Q4 + CY2024Q3 + CY2024Q2
        #           = CY2024 (annual) + CY2025Q1 - CY2024Q1
        prior_annual = "CY2024"
        prior_q1 = "CY2024Q1"

        for metric_name, xbrl_tag in [("net_income", "NetIncomeLoss"),
                                       ("revenue", "RevenueFromContractWithCustomerExcludingAssessedTax")]:
            # Fetch prior TTM components
            prior_annual_data = self._fetch_frame(xbrl_tag, "USD", False, quarter=prior_annual)
            time.sleep(0.1)
            cy2025q1_data = self._fetch_frame(xbrl_tag, "USD", False, quarter="CY2025Q1")
            time.sleep(0.1)
            cy2024q1_data = self._fetch_frame(xbrl_tag, "USD", False, quarter=prior_q1)
            time.sleep(0.1)

            # Compute prior TTM
            prev_ttm = {}
            for cik in set(prior_annual_data.keys()) & set(cy2025q1_data.keys()) & set(cy2024q1_data.keys()):
                prev_ttm[cik] = prior_annual_data[cik] + cy2025q1_data[cik] - cy2024q1_data[cik]

            all_data[f"prev_{metric_name}"] = prev_ttm
            print(f"    prev_{metric_name} (prior TTM): {len(prev_ttm)} companies")

        # Try alternative revenue tags if primary coverage is low
        if len(all_data.get("revenue", {})) < 2000:
            for alt_tag in self.REVENUE_ALTERNATIVES[1:]:
                # Quick check on just one quarter to see if this tag helps
                alt_data = self._fetch_frame(alt_tag, "USD", False, quarter="CY2026Q1")
                if alt_data:
                    existing = all_data.get("revenue", {})
                    added = 0
                    for cik, val in alt_data.items():
                        if cik not in existing:
                            existing[cik] = val
                            added += 1
                    if added > 0:
                        all_data["revenue"] = existing
                        print(f"    revenue (alt: {alt_tag}): +{added} (total: {len(existing)})")
                time.sleep(0.1)

        # =============================================
        # BALANCE SHEET: CY2026Q1I with CY2025Q4I fallback
        # =============================================
        balance_metrics = {
            "stockholders_equity": ("StockholdersEquity", "USD"),
            "long_term_debt": ("LongTermDebt", "USD"),
            "assets_current": ("AssetsCurrent", "USD"),
            "liabilities_current": ("LiabilitiesCurrent", "USD"),
            "shares_outstanding": ("CommonStockSharesOutstanding", "shares"),
            "inventory": ("InventoryNet", "USD"),
            "cash": ("CashAndCashEquivalentsAtCarryingValue", "USD"),
        }

        for metric_name, (xbrl_tag, unit) in balance_metrics.items():
            # Primary: latest quarter
            frame_data = self._fetch_frame(xbrl_tag, unit, False, quarter="CY2026Q1I")
            # Fallback: fill gaps from prior quarter
            fallback_data = self._fetch_frame(xbrl_tag, unit, False, quarter="CY2025Q4I")
            for cik, val in fallback_data.items():
                if cik not in frame_data:
                    frame_data[cik] = val
            all_data[metric_name] = frame_data
            print(f"    {metric_name} (Q1I+Q4I fallback): {len(frame_data)} companies")
            time.sleep(0.1)

        return all_data

    def get_stock_universe(self) -> list[str]:
        """
        Return all tickers that have financial data in EDGAR.

        We get the universe from the companies that reported Net Income
        in the latest quarter (largest coverage: ~5,000 companies).
        """
        self._load_ticker_mapping()

        # Fetch net income frame for current quarter to identify which companies have recent filings
        net_income_data = self._fetch_frame("NetIncomeLoss", "USD", False, quarter="CY2026Q1")

        # Map CIKs back to tickers
        symbols = []
        for cik in net_income_data:
            ticker = self._cik_to_ticker.get(cik)
            if ticker:
                symbols.append(ticker)

        print(f"  Universe: {len(symbols)} stocks with CY2026Q1 data")
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
            debt = all_frames.get("long_term_debt", {}).get(cik)
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
            debt_to_equity = debt / equity if debt and equity and equity > 0 else None
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
