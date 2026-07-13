"""
Data Providers Package
======================
Implements the Strategy Pattern for financial data sourcing.

Architecture:
    DataProvider (abstract interface)
    ├── EdgarProvider    (current — SEC EDGAR, free, unlimited, bulk data)
    ├── FMPProvider      (previous — FMP free tier, bandwidth-limited)
    └── [Future: PolygonProvider, AlphaVantageProvider, etc.]

Usage:
    from providers import get_provider
    provider = get_provider("edgar")
    stocks = provider.get_stock_universe()
    data = provider.get_fundamentals_batch(stocks)

To add a new provider:
    1. Create a new file in this package (e.g., polygon_provider.py)
    2. Implement the DataProvider abstract class
    3. Register it in the PROVIDERS dict below
"""

from providers.base import DataProvider, StockFundamentals, ProviderError
from providers.edgar_provider import EdgarProvider
from providers.fmp_provider import FMPProvider

# Registry of available providers
PROVIDERS = {
    "edgar": EdgarProvider,
    "fmp": FMPProvider,
}


def get_provider(provider_name: str, **kwargs) -> DataProvider:
    """
    Factory function to instantiate a data provider by name.

    Args:
        provider_name: Key from the PROVIDERS dict
        **kwargs: Provider-specific config

    Returns:
        An initialized DataProvider instance

    Raises:
        ValueError: If the provider name is not registered
    """
    if provider_name not in PROVIDERS:
        available = ", ".join(PROVIDERS.keys())
        raise ValueError(
            f"Unknown provider '{provider_name}'. Available: {available}"
        )

    provider_class = PROVIDERS[provider_name]
    return provider_class(**kwargs)
