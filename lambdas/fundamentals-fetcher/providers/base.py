"""
DataProvider Abstract Base Class
=================================
Defines the interface that ALL data providers must implement.

This is the core of the abstraction. Any provider — yfinance, FMP,
Polygon, Alpha Vantage, or a future one — must conform to this contract.

Key design decisions:
1. StockFundamentals is a typed dataclass — enforces consistent field names
   regardless of the upstream API's naming conventions.
2. Methods return Optional types — providers should never raise exceptions
   for missing data; they return None and let the caller decide.
3. get_stock_universe() returns the discoverable list of stocks — this means
   even stock discovery is provider-agnostic.
4. get_fundamentals_batch() exists for providers that support bulk requests
   (more efficient than N individual calls).

WHEN ADDING A NEW PROVIDER:
- Subclass DataProvider
- Implement all abstract methods
- Map the provider's field names to StockFundamentals fields
- Handle rate limiting inside the provider (not in the caller)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from typing import Optional


class ProviderError(Exception):
    """Raised when a provider encounters an unrecoverable error."""
    pass


@dataclass
class StockFundamentals:
    """
    Standardized fundamental data for a single stock.

    Every provider maps its raw API response into this structure.
    The screener Lambda works exclusively with this — it never sees
    provider-specific field names.

    Field categories:
    - Identity: symbol, name, sector, etc.
    - Valuation: PE, PEG, P/FCF — what you're paying
    - Balance Sheet: debt ratios, liquidity — financial strength
    - Profitability: margins, ROE — business quality
    - Growth: EPS growth, revenue growth — trajectory
    - Analyst: recommendations, target price — market consensus
    - Institutional: ownership changes — smart money signals
    """

    # === Identity ===
    symbol: str
    company_name: str = ""
    sector: str = ""
    industry: str = ""
    market_cap: Optional[float] = None
    price: Optional[float] = None
    exchange: str = ""

    # === Valuation (what you pay) ===
    pe_ratio: Optional[float] = None  # Price / Earnings
    forward_pe: Optional[float] = None  # Price / Forward Earnings
    peg_ratio: Optional[float] = None  # PE / Growth rate (<1 = undervalued)
    price_to_fcf: Optional[float] = None  # Price / Free Cash Flow
    price_to_book: Optional[float] = None  # Price / Book Value
    price_to_sales: Optional[float] = None  # Price / Revenue
    ev_to_ebitda: Optional[float] = None  # Enterprise Value / EBITDA

    # === Balance Sheet Health ===
    debt_to_equity: Optional[float] = None  # Total Debt / Shareholders Equity
    quick_ratio: Optional[float] = None  # (Cash + Receivables) / Current Liabilities
    current_ratio: Optional[float] = None  # Current Assets / Current Liabilities

    # === Profitability ===
    operating_margin: Optional[float] = None  # Operating Income / Revenue (as decimal)
    net_profit_margin: Optional[float] = None  # Net Income / Revenue (as decimal)
    gross_margin: Optional[float] = None  # Gross Profit / Revenue (as decimal)
    return_on_equity: Optional[float] = None  # Net Income / Shareholders Equity

    # === Growth ===
    eps_growth_yoy: Optional[float] = None  # EPS growth year-over-year (as decimal)
    revenue_growth_yoy: Optional[float] = None  # Revenue growth YoY (as decimal)
    revenue_growth_qoq: Optional[float] = None  # Revenue growth quarter-over-quarter
    earnings_growth_qoq: Optional[float] = None  # Earnings growth QoQ
    estimated_lt_growth: Optional[float] = None  # Analyst est. long-term growth rate

    # === Analyst Consensus ===
    analyst_recommendation: Optional[float] = None  # 1=Strong Buy, 5=Strong Sell
    analyst_target_price: Optional[float] = None  # Consensus target price
    target_price_upside: Optional[float] = None  # (Target - Current) / Current

    # === Institutional Activity ===
    institutional_ownership: Optional[float] = None  # % owned by institutions
    institutional_transactions: Optional[float] = None  # Net change in inst. ownership

    # === Per-Share Metrics ===
    eps: Optional[float] = None  # Earnings per share (TTM)
    revenue_per_share: Optional[float] = None
    fcf_per_share: Optional[float] = None  # Free cash flow per share

    # === Dividend ===
    dividend_yield: Optional[float] = None  # Annual dividend / price
    payout_ratio: Optional[float] = None  # Dividends / Net Income

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


class DataProvider(ABC):
    """
    Abstract base class for all financial data providers.

    Subclasses implement data fetching from specific sources
    (yfinance, FMP, Polygon, etc.) and normalize responses into
    the StockFundamentals schema.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of this provider (e.g., 'Yahoo Finance')."""
        ...

    @abstractmethod
    def get_stock_universe(self) -> list[str]:
        """
        Return a list of stock symbols to screen.

        This could be:
        - An index constituent list (S&P 500, Nasdaq 100)
        - Results from a screener endpoint
        - A curated watchlist

        The point: stock discovery is part of the provider's responsibility.
        Different providers may discover stocks differently.

        Returns:
            List of ticker symbols (e.g., ["AAPL", "MSFT", ...])
        """
        ...

    @abstractmethod
    def get_fundamentals(self, symbol: str) -> Optional[StockFundamentals]:
        """
        Fetch fundamental data for a single stock.

        Maps provider-specific fields to the StockFundamentals schema.
        Returns None if data is unavailable (stock delisted, API error, etc.)

        Args:
            symbol: Ticker symbol (e.g., "AAPL")

        Returns:
            StockFundamentals instance, or None if data unavailable
        """
        ...

    def get_fundamentals_batch(self, symbols: list[str]) -> list[StockFundamentals]:
        """
        Fetch fundamentals for multiple stocks.

        Default implementation calls get_fundamentals() in a loop.
        Providers with bulk/batch endpoints should override this
        for efficiency.

        Args:
            symbols: List of ticker symbols

        Returns:
            List of StockFundamentals (only successful fetches included)
        """
        results = []
        for symbol in symbols:
            try:
                data = self.get_fundamentals(symbol)
                if data:
                    results.append(data)
            except Exception as e:
                print(f"Warning: {self.name} failed for {symbol}: {e}")
                continue
        return results

    @abstractmethod
    def get_rate_limit_delay(self) -> float:
        """
        Return the recommended delay between API calls (in seconds).

        This is provider-specific:
        - yfinance: minimal (no strict rate limit, but be respectful)
        - FMP free: ~0.5s (250 req/day)
        - FMP paid: minimal
        - Polygon: depends on plan

        Returns:
            Delay in seconds between consecutive API calls
        """
        ...
