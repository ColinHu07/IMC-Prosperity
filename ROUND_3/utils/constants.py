"""
Constants for the IMC Prosperity Round 3 trading system.
"""
import os as _os


DELTA1_PRODUCTS = ["HYDROGEL_PACK", "VELVETFRUIT_EXTRACT"]

VOUCHER_STRIKES = {
    "VEV_4000": 4000, "VEV_4500": 4500, "VEV_5000": 5000,
    "VEV_5100": 5100, "VEV_5200": 5200, "VEV_5300": 5300,
    "VEV_5400": 5400, "VEV_5500": 5500,
    "VEV_6000": 6000, "VEV_6500": 6500,
}

VOUCHER_PRODUCTS = list(VOUCHER_STRIKES.keys())

PRODUCTS = DELTA1_PRODUCTS + VOUCHER_PRODUCTS

POSITION_LIMITS = {
    "HYDROGEL_PACK": 200,
    "VELVETFRUIT_EXTRACT": 200,
    **{sym: 300 for sym in VOUCHER_PRODUCTS},
}

TIMESTAMP_STEP = 100
TIMESTAMPS_PER_DAY = 10000
MAX_TIMESTAMP = 999900

DAYS = [0, 1, 2]

_BASE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
DATA_DIR = _BASE
OUTPUT_DIR = _os.path.join(_BASE, "round3_results")
