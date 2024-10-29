import logging
import os.path
from datetime import timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
from demeter import ActionTypeEnum
from demeter import Strategy, Actuator, MarketInfo, RowData, MarketTypeEnum, PeriodTrigger
from demeter.aave import AaveV3Market
from demeter.uniswap import UniV3Pool, UniLpMarket, V3CoreLib, base_unit_price_to_sqrt_price_x96

import config
from _typing import Amounts, CurrentPosition
from math_lib_v1 import optimize_delta_neutral
from utils import load_data, get_file_name

pd.options.display.max_columns = None
pd.set_option("display.width", 5000)

# The rate of borrow eth and supply usdc
AAVE_POLYGON_USDC_ALPHA = Decimal("0.7")
# when net value has changed over this resthold, will adjust the portfolio
NET_VALUE_REBALANCE_RESTHOLD = Decimal("0.02")

class DeltaHedgingStrategy(Strategy):
    """

    tick range: (1-sigma*LOWER_AMP ,1+sigma*UPPER_AMP)
    """

    def __init__(self, amp):
        super().__init__()
        self.fee0 = []
        self.fee1 = []
        self.last_collect_fee0 = 0
        self.last_collect_fee1 = 0
        self.last_reposition_time = None
        self.pause_investment = False
        self.net_value_without_fee_list = []
        self.base = market_uni.pool_info.base_token
        self.quote = market_uni.pool_info.quote_token
        self.change_history = pd.Series()
        self.upper_amp, self.lower_amp = amp
        self.amount_change_lower = Decimal(1) - NET_VALUE_REBALANCE_RESTHOLD
        self.amount_change_upper = Decimal(1) + NET_VALUE_REBALANCE_RESTHOLD
        self.param = None



    def initialize(self):
        work_trigger = PeriodTrigger(time_delta=timedelta(hours=1), trigger_immediately=True, do=self.work_on_the_hour)
        self.triggers.append(work_trigger)

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

    def work_on_the_hour(self, row_data: RowData):
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

    def calc_fund_param(self, h: float, l: float):
        optimize_res = optimize_delta_neutral(h, l, float(AAVE_POLYGON_USDC_ALPHA))
        V_U_A, V_U_init, V_U_uni, V_E_uni, V_E_lend, V_U_lend = optimize_res[1]
        amounts = Amounts(
            usdc_aave_supply=Decimal(V_U_A),
            usdc_uni_init=Decimal(V_U_init),
            eth_uni_lp=Decimal(V_E_uni),
            usdc_uni_lp=Decimal(V_U_uni),
            eth_aave_borrow=Decimal(V_E_lend),
            usdc_aave_borrow=Decimal(V_U_lend),
        )
        logging.debug(amounts)
        return amounts

    def reset_funds(self):
        # withdraw all positions
        if len(market_uni.positions) < 1:
            return False

        mb = market_uni.get_market_balance()

        self.last_collect_fee0 += mb.base_uncollected
        self.last_collect_fee1 += mb.quote_uncollected
        market_uni.remove_all_liquidity()
        for b_key in market_aave.borrow_keys:
            swap_amount = market_aave.get_borrow(b_key).amount - broker.assets[config.weth].balance
            if swap_amount > 0:
                market_uni.buy(swap_amount * (1 + market_uni.pool_info.fee_rate))
            market_aave.repay(b_key)
        for s_key in market_aave.supply_keys:
            market_aave.withdraw(s_key)
        market_uni.sell(broker.assets[config.weth].balance)
        return True

    def get_cash_net_value(self, price: pd.Series):
        return Decimal(sum([asset.balance * price[asset.name] for asset in broker.assets.values()]))

    def get_net_value_without_fee(self, price):
        cash = self.get_cash_net_value(price)
        lp_value = 0
        sqrt_price = base_unit_price_to_sqrt_price_x96(
            price[config.weth.name],
            market_uni.pool_info.token0.decimal,
            market_uni.pool_info.token1.decimal,
            market_uni.pool_info.is_token0_quote,
        )
        for pos_key, pos in market_uni.positions.items():
            amount0, amount1 = V3CoreLib.get_token_amounts(
                market_uni.pool_info, pos_key, sqrt_price, pos.liquidity
            )
            lp_value += amount0 * price[config.usdc.name] + amount1 * price[config.weth.name]

        aave_status = market_aave.get_market_balance()
        all_value = cash + aave_status.net_value + lp_value
        # account_status = broker.get_account_status(price)
        return all_value

    def after_bar(self, row_data: RowData):
        mb = market_uni.get_market_balance()
        self.fee0.append(mb.base_uncollected + self.last_collect_fee0)
        self.fee1.append(mb.quote_uncollected + self.last_collect_fee1)
        value = self.get_net_value_without_fee(row_data.prices)
        self.net_value_without_fee_list.append(value)

        pass


if __name__ == "__main__":
    UPPER_AMP = Decimal("0.9")
    LOWER_AMP = Decimal("0.5")

    market_key_uni = MarketInfo("uni")
    market_key_aave = MarketInfo("aave", MarketTypeEnum.aave_v3)

    pool = UniV3Pool(config.usdc, config.weth, 0.05, config.usdc)
    actuator = Actuator()
    broker = actuator.broker

    market_uni = UniLpMarket(market_key_uni, pool)

    broker.add_market(market_uni)  # add market

    market_aave = AaveV3Market(
        market_key_aave, os.path.join(config.APP.aave_data_path, "risk-parameters.csv"), [config.usdc, config.weth]
    )
    market_aave.data_path = config.APP.aave_data_path
    broker.add_market(market_aave)  # add market

    broker.set_balance(config.usdc, 10000)  # set balance
    actuator.strategy = DeltaHedgingStrategy((UPPER_AMP, LOWER_AMP))

    load_data(actuator, market_uni, market_aave, config.APP.start, config.APP.end)
    actuator.interval = "1h"
    actuator.print_action = True
    actuator.run()

    file_path = get_file_name(
        f"{UPPER_AMP}-{LOWER_AMP}",
        config.APP.start,
        config.APP.end,
    )
    actuator.save_result(config.APP.to_path, file_path)
