"""
EDGAR Monthly Maintenance Lambda
==================================
Monthly scheduled Lambda that:
1. Discovers alternative XBRL tags for companies with missing metrics
2. Generates the foreign filer fundamentals reference file

Triggered by: EventBridge monthly rule (1st of each month, 6 AM UTC)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from discover_edgar_tags import handler as discovery_handler
from generate_foreign_filers import handler as foreign_filer_handler


def handler(event, context):
    """Lambda entry point — runs both monthly maintenance tasks."""
    print("=== Monthly EDGAR Maintenance ===")

    # Task 1: Tag discovery
    print("\n--- Task 1: Tag Discovery ---")
    try:
        discovery_result = discovery_handler(event, context)
    except Exception as e:
        print(f"  Tag discovery failed: {e}")
        discovery_result = {"error": str(e)}

    # Task 2: Foreign filer fundamentals
    print("\n--- Task 2: Foreign Filer Fundamentals ---")
    try:
        foreign_result = foreign_filer_handler(event, context)
    except Exception as e:
        print(f"  Foreign filer generation failed: {e}")
        foreign_result = {"error": str(e)}

    return {
        "tag_discovery": discovery_result,
        "foreign_filers": foreign_result,
    }
