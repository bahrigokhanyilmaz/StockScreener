"""
Data Providers Package
======================
Implements the Strategy Pattern for financial data sourcing.

The key idea: your screening logic doesn't know or care WHERE the data
comes from. It works against a standard interface (DataProvider), and you
swap implementations via configuration.

Architecture:
    DataProvider (abstract interface)
    ├── FMPProvider       (current — works on free + paid tiers)
    └── [Future: PolygonProvider, AlphaVantageProvider, etc.]

Usage:
    from providers import get_provider
    provider = get_provider("fmp", api_key="...")
    stocks = provider.get_stock_universe()
    data = provider.get_fundamentals_batch(stocks)

To add a new provider:
    1. Create a new file in this package (e.g., polygon_provider.py)
    2. Implement the DataProvider abstract class
    3. Register it in the PROVIDERS dict below
"""

from providers.base import DataProvider, StockFundamentals, ProviderError
from providers.fmp_provider import FMPProvider

# Registry of available providers
# To add a new provider, import it and add to this dict
PROVIDERS = {
    "fmp": FMPProvider,
}


def get_provider(provider_name: str, **kwargs) -> DataProvider:
    """
    Factory function to instantiate a data provider by name.

    Called by the Lambda handler. The provider name comes from
    an environment variable — switching providers is a config change.

    Args:
        provider_name: Key from the PROVIDERS dict
        **kwargs: Provider-specific config (e.g., api_key for FMP)

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
