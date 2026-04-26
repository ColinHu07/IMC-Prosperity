"""
Constants for the IMC Prosperity Round 1 trading system.
"""

PRODUCTS = ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]

POSITION_LIMITS = {
    "ASH_COATED_OSMIUM": 80,
    "INTARIAN_PEPPER_ROOT": 80,
}

TIMESTAMP_STEP = 100
TIMESTAMPS_PER_DAY = 10000
MAX_TIMESTAMP = 999900

import os as _os
_BASE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
DATA_DIR = _BASE
OUTPUT_DIR = _os.path.join(_BASE, "output")

DAYS = [-2, -1, 0]
