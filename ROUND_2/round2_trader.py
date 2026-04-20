from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ImportError:
    from prosperity4bt.datamodel import Order, OrderDepth, TradingState


ASH_COATED_OSMIUM = "ASH_COATED_OSMIUM"
INTARIAN_PEPPER_ROOT = "INTARIAN_PEPPER_ROOT"

POSITION_LIMITS = {
    ASH_COATED_OSMIUM: 80,
    INTARIAN_PEPPER_ROOT: 80,
}

MAX_TIMESTAMP = 999_900


@dataclass(frozen=True)
class ProductConfig:
    take_edge: float
    quote_edge: float
    skew: float
    quote_size: int
    fair_alpha: float


DEFAULT_CONFIGS = {
    ASH_COATED_OSMIUM: ProductConfig(
        take_edge=2.0,
        quote_edge=3.0,
        skew=0.12,
        quote_size=16,
        fair_alpha=0.03,
    ),
    INTARIAN_PEPPER_ROOT: ProductConfig(
        take_edge=2.0,
        quote_edge=3.0,
        skew=0.18,
        quote_size=12,
        fair_alpha=0.05,
    ),
}


class Trader:
    """
    Round 2 trader tuned for:
    - ASH_COATED_OSMIUM: slow mean reversion around a stable fair value.
    - INTARIAN_PEPPER_ROOT: near-deterministic upward drift of ~1 tick / 1000 ms.
    """

    # Conservative fee from local 75% vs. 100% quote-access simulations on the provided data.
    MAF_BID = 15_800

    def __init__(self) -> None:
        self._osmium_fair: Optional[float] = None
        self._pepper_anchor: Optional[float] = None
        self._last_timestamp: Optional[int] = None

    def bid(self, state: TradingState | None = None) -> int:
        return self.MAF_BID

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        self._maybe_reset_day(state.timestamp)

        orders: Dict[str, List[Order]] = {}
        for product, depth in state.order_depths.items():
            if product not in POSITION_LIMITS:
                continue

            best_bid, best_ask = self._best_bid_ask(depth)
            if best_bid is None and best_ask is None:
                continue

            fair_value = self._fair_value(product, best_bid, best_ask, state.timestamp)
            position = state.position.get(product, 0)
            product_orders = self._build_orders(
                product=product,
                depth=depth,
                fair_value=fair_value,
                position=position,
                timestamp=state.timestamp,
            )

            if product_orders:
                orders[product] = product_orders

        self._last_timestamp = state.timestamp
        return orders, 0, ""

    def _maybe_reset_day(self, timestamp: int) -> None:
        if self._last_timestamp is None:
            return
        if timestamp <= self._last_timestamp:
            self._osmium_fair = None
            self._pepper_anchor = None

    def _fair_value(self, product: str, best_bid: Optional[int], best_ask: Optional[int], timestamp: int) -> float:
        visible_mid = self._visible_mid(best_bid, best_ask)
        config = DEFAULT_CONFIGS[product]

        if product == INTARIAN_PEPPER_ROOT:
            raw_anchor = visible_mid - timestamp / 1000.0
            if self._pepper_anchor is None:
                # Rounding the first anchor removes most of the opening-print noise.
                self._pepper_anchor = round(raw_anchor)
            else:
                alpha = config.fair_alpha
                self._pepper_anchor = (1.0 - alpha) * self._pepper_anchor + alpha * raw_anchor
            return self._pepper_anchor + timestamp / 1000.0

        if self._osmium_fair is None:
            self._osmium_fair = visible_mid
        else:
            alpha = config.fair_alpha
            self._osmium_fair = (1.0 - alpha) * self._osmium_fair + alpha * visible_mid
        return self._osmium_fair

    def _build_orders(
        self,
        product: str,
        depth: OrderDepth,
        fair_value: float,
        position: int,
        timestamp: int,
    ) -> List[Order]:
        config = DEFAULT_CONFIGS[product]
        limit = POSITION_LIMITS[product]
        best_bid, best_ask = self._best_bid_ask(depth)

        orders: List[Order] = []
        buy_room = limit - position
        sell_room = limit + position

        if best_ask is not None and best_ask < fair_value - config.take_edge and buy_room > 0:
            tradeable = min(buy_room, abs(depth.sell_orders[best_ask]))
            if tradeable > 0:
                orders.append(Order(product, int(best_ask), int(tradeable)))
                position += tradeable
                buy_room -= tradeable

        if best_bid is not None and best_bid > fair_value + config.take_edge and sell_room > 0:
            tradeable = min(sell_room, depth.buy_orders[best_bid])
            if tradeable > 0:
                orders.append(Order(product, int(best_bid), int(-tradeable)))
                position -= tradeable
                sell_room -= tradeable

        liquidation_multiplier = 1.0
        if timestamp > int(0.95 * MAX_TIMESTAMP):
            liquidation_multiplier = 2.5
            orders.extend(
                self._flatten_inventory(
                    product=product,
                    depth=depth,
                    fair_value=fair_value,
                    position=position,
                )
            )

        inventory_adjustment = config.skew * liquidation_multiplier * position
        bid_price = math.floor(fair_value - config.quote_edge - inventory_adjustment)
        ask_price = math.ceil(fair_value + config.quote_edge - inventory_adjustment)

        if best_bid is not None:
            bid_price = min(bid_price, best_bid + 1)
        if best_ask is not None:
            ask_price = max(ask_price, best_ask - 1)
        if bid_price >= ask_price:
            bid_price = ask_price - 1

        quote_size = config.quote_size
        if timestamp > int(0.95 * MAX_TIMESTAMP):
            quote_size = max(4, quote_size // 2)

        if buy_room > 0:
            orders.append(Order(product, int(bid_price), int(min(quote_size, buy_room))))
        if sell_room > 0:
            orders.append(Order(product, int(ask_price), int(-min(quote_size, sell_room))))

        return orders

    def _flatten_inventory(
        self,
        product: str,
        depth: OrderDepth,
        fair_value: float,
        position: int,
    ) -> List[Order]:
        orders: List[Order] = []
        best_bid, best_ask = self._best_bid_ask(depth)

        if position > 0 and best_bid is not None and best_bid >= fair_value - 2:
            volume = min(position, depth.buy_orders[best_bid])
            if volume > 0:
                orders.append(Order(product, int(best_bid), int(-volume)))

        if position < 0 and best_ask is not None and best_ask <= fair_value + 2:
            volume = min(-position, abs(depth.sell_orders[best_ask]))
            if volume > 0:
                orders.append(Order(product, int(best_ask), int(volume)))

        return orders

    @staticmethod
    def _best_bid_ask(depth: OrderDepth) -> Tuple[Optional[int], Optional[int]]:
        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        return best_bid, best_ask

    @staticmethod
    def _visible_mid(best_bid: Optional[int], best_ask: Optional[int]) -> float:
        if best_bid is not None and best_ask is not None:
            return (best_bid + best_ask) / 2.0
        if best_bid is not None:
            return float(best_bid)
        if best_ask is not None:
            return float(best_ask)
        return 0.0
