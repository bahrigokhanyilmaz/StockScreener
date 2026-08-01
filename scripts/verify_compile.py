import py_compile
py_compile.compile('lambdas/enrichment/handler.py', doraise=True)
py_compile.compile('lambdas/score-calculator/handler.py', doraise=True)
py_compile.compile('lambdas/fundamentals-fetcher/providers/edgar_provider.py', doraise=True)
py_compile.compile('lambdas/stock-screener/handler.py', doraise=True)
print("ALL OK")
