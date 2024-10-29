import os.path
from datetime import timedelta
from decimal import Decimal
from typing import NamedTuple

import demeter
import numpy as np
import pandas as pd
from demeter import RowData, Strategy, ActionTypeEnum
from demeter.result import performance_metrics, MetricEnum

import config
from strategy_common import (
    generate_backtest,
    CommonStrategy,
    get_file_name,
    AAVE_POLYGON_USDC_ALPHA,
    Amounts,
    load_data,
)

pd.options.display.max_columns = None
pd.set_option("display.width", 5000)


class CurrentPosition(NamedTuple):
    h: Decimal
    l: Decimal
    sigma: Decimal
    h_price: Decimal
    l_price: Decimal
    amounts: Amounts


class DeltaHedgingStrategy(CommonStrategy):
    """

    tick range: (1-sigma*LOWER_AMP ,1+sigma*UPPER_AMP)
    """

    def __init__(self, broker, market_uni, market_aave, amp):
        super().__init__(broker, market_uni, market_aave)
        self.fee0 = []
        self.fee1 = []
        self.last_collect_fee0 = 0
        self.last_collect_fee1 = 0
        self.change_history = pd.Series()
        self.upper_amp, self.lower_amp = amp
        self.amount_change_lower = Decimal(1) - DeltaHedgingStrategy.NET_VALUE_REBALANCE_RESTHOLD
        self.amount_change_upper = Decimal(1) + DeltaHedgingStrategy.NET_VALUE_REBALANCE_RESTHOLD
        self.param = None

    # when net value has changed over this resthold, will adjust the portfolio
    NET_VALUE_REBALANCE_RESTHOLD = Decimal("0.02")

    def get_sigma(self, row_data: RowData):
        if row_data.timestamp - timedelta(days=1) >= self.prices.index[0]:
            prices = self.prices.loc[row_data.timestamp - timedelta(days=1): row_data.timestamp]
        else:
            prices = self.prices.loc[self.prices.index[0]: self.prices.index[0] + timedelta(days=1)]

        prices = pd.to_numeric(prices[self.base.name])
        r = prices / prices.shift(1)
        return Decimal(r.std() * np.sqrt(365 * 1440))

    def exit(self, cause):
        result = self.reset_funds()
        self.param = None
        if result:
            self.comment_last_action(f"exit because {cause}", ActionTypeEnum.uni_lp_remove_liquidity)

    def invest(self, row_data: RowData):
        if self.param:
            return
        sigma = self.get_sigma(row_data)
        if sigma > 0.9:
            # market is not stable
            return

        h = 1 + sigma * self.upper_amp
        l = 1 - sigma * self.lower_amp

        if h > 10:
            h = 10
        if l < 0.1:
            h = 0.1

        base_price = row_data.prices[self.base.name]
        amounts = self.calc_fund_param(float(h), float(l))
        self.param = CurrentPosition(h, l, sigma, base_price * h, base_price * l, amounts)

        total_cash = self.get_cash_net_value(row_data.prices)

        # work
        wanted_aave_supply_value = total_cash * self.param.amounts.usdc_aave_supply
        wanted_aave_borrow_value = wanted_aave_supply_value * AAVE_POLYGON_USDC_ALPHA

        market_aave.supply(self.quote, wanted_aave_supply_value)
        market_aave.borrow(self.base, wanted_aave_borrow_value / row_data.prices[self.base.name])

        self.last_net_value = total_cash
        # eth => usdc
        market_uni.sell(self.param.amounts.usdc_aave_borrow * total_cash / row_data.prices[self.base.name])

        market_uni.add_liquidity(self.param.l_price, self.param.h_price)
        self.comment_last_action(str(self.param))

        # result monitor
        self.last_reposition_time = row_data.timestamp
        pass

    def on_bar(self, row_data: RowData):
        if self.param:
            if (
                    not self.last_net_value * self.amount_change_lower
                        < self.get_net_value_without_fee(row_data.prices)
                        < self.last_net_value * self.amount_change_upper
            ):
                self.exit(f"Net value has changed over {DeltaHedgingStrategy.NET_VALUE_REBALANCE_RESTHOLD}")
            elif not self.param.l_price <= row_data.prices[config.weth.name] <= self.param.h_price:
                self.exit("Out of position price range")

        self.invest(row_data)


# ===========================================================================

def run_single(upper_amp, lower_amp):
    global actuator, broker, market_uni, market_aave
    actuator, broker, market_uni, market_aave = generate_backtest()
    actuator.strategy = DeltaHedgingStrategy(broker, market_uni, market_aave, (upper_amp, lower_amp))

    file_path = get_file_name(
        f"{upper_amp}-{lower_amp}",
        config.APP.start,
        config.APP.end,
    )
    print(file_path)
    if os.path.exists(os.path.join(config.APP.to_path, file_path + ".pkl")):
        return
    load_data(actuator, market_uni, market_aave, config.APP.start, config.APP.end)
    actuator.interval = "1h"
    actuator.run()
    # actuator.save_result(config.APP.to_path, file_path)


# ===========================================================================

if __name__ == "__main__":
    UPPER_AMP = Decimal("0.9")
    LOWER_AMP = Decimal("0.5")

    actuator = broker = market_uni = market_aave = None

    run_single(UPPER_AMP, LOWER_AMP)
