"""
Constants for the IMC Prosperity Round 1 trading system.
"""
import os

# ROUND1/ directory (parent of utils/)
_ROUND1_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PRODUCTS = ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]

POSITION_LIMITS = {
    "ASH_COATED_OSMIUM": 50,
    "INTARIAN_PEPPER_ROOT": 50,
}

TIMESTAMP_STEP = 100
TIMESTAMPS_PER_DAY = 10000
MAX_TIMESTAMP = 999900

DATA_DIR = _ROUND1_ROOT
OUTPUT_DIR = os.path.join(_ROUND1_ROOT, "output")

DAYS = [-2, -1, 0]
