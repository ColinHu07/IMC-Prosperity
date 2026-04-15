"""
Strategy configuration: all tunable parameters in one place.
"""

BASELINE_ASH_PARAMS = {
    "ewma_alpha": 0.05,
    "take_threshold": 1.0,
    "make_width": 3,
    "inventory_skew_factor": 0.5,
    "max_passive_size": 25,
    "max_take_size": 15,
    "position_limit": 50,
    "imbalance_edge": 0.0,
}

OPTIMIZED_ASH_PARAMS = {
    "ewma_alpha": 0.06,
    "take_threshold": 0.5,
    "make_width": 7,
    "inventory_skew_factor": 0.02,
    "max_passive_size": 38,
    "max_take_size": 10,
    "position_limit": 50,
    "imbalance_edge": 1.0,
}

BASELINE_PEPPER_PARAMS = {
    "trend_rate": 0.1002,
    "ewma_alpha_base": 0.005,
    "take_threshold": 1.0,
    "make_width": 2,
    "inventory_skew_factor": 0.5,
    "max_passive_size": 20,
    "max_take_size": 15,
    "residual_zscore_threshold": 1.5,
    "position_limit": 50,
    "directional_skew": 0.0,
    "trend_ewma_alpha": 0.001,
}

OPTIMIZED_PEPPER_PARAMS = {
    "trend_rate": 0.1002,
    "ewma_alpha_base": 0.02,
    "take_threshold": 2.0,
    "make_width": 5,
    "inventory_skew_factor": 0.0,
    "max_passive_size": 20,
    "max_take_size": 15,
    "residual_zscore_threshold": 1.0,
    "position_limit": 50,
    "directional_skew": 1.5,
    "trend_ewma_alpha": 0.001,
}


def get_default_params():
    return {
        "ASH_COATED_OSMIUM": dict(BASELINE_ASH_PARAMS),
        "INTARIAN_PEPPER_ROOT": dict(BASELINE_PEPPER_PARAMS),
    }
