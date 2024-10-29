import logging
import os.path
from datetime import date, timedelta
from decimal import Decimal
from typing import NamedTuple

import pandas as pd
from demeter import ChainType, Strategy, Actuator, MarketInfo, RowData, MarketTypeEnum, PeriodTrigger
from demeter.aave import AaveV3Market
from demeter.uniswap import UniV3Pool, UniLpMarket, V3CoreLib, base_unit_price_to_sqrt_price_x96

import config
from math_lib_v1 import optimize_delta_neutral
from utils import load_from_trading_strategy, load_clmm_data_to_uni_lp_market


class Amounts(NamedTuple):
    usdc_aave_supply: Decimal
    usdc_uni_init: Decimal
    eth_uni_lp: Decimal
    usdc_uni_lp: Decimal
    eth_aave_borrow: Decimal
    usdc_aave_borrow: Decimal


def generate_backtest():
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

    return actuator, broker, market_uni, market_aave


def load_data(actuator, market_uni, market_aave, start_date, end_date):
    pool_data = load_from_trading_strategy(
        ChainType[config.CHAIN.name], [market_uni.pool_info], start_date, end_date, config.APP.ts_apikey
    )
    load_clmm_data_to_uni_lp_market(market_uni, pool_data, start_date, end_date)
    market_aave.load_data(ChainType.ethereum, [config.usdc, config.weth], start_date, end_date)
    actuator.set_price(market_uni.get_price_from_data())


def get_file_name(title, start: date, end: date):
    return f"{title}.{start.strftime('%Y%m%d')}~{end.strftime('%Y%m%d')}"


AAVE_POLYGON_USDC_ALPHA = Decimal("0.7")  # The rate of borrow eth and supply usdc


class CommonStrategy(Strategy):
    def __init__(self, broker, market_uni, market_aave):
        super().__init__()

        self.fee0 = []
        self.fee1 = []
        self.last_collect_fee0 = 0
        self.last_collect_fee1 = 0
        self.last_reposition_time = None
        self.pause_investment = False
        self.broker = broker
        self.market_uni = market_uni
        self.market_aave = market_aave
        self.net_value_without_fee_list = []
        self.base = market_uni.pool_info.base_token
        self.quote = market_uni.pool_info.quote_token

    NET_VALUE_REBALANCE_RESTHOLD = Decimal("0.02")

    def initialize(self):
        work_trigger = PeriodTrigger(time_delta=timedelta(hours=1), trigger_immediately=True, do=self.work_on_the_hour)
        self.triggers.append(work_trigger)

    def invest(self, row_data: RowData):
        pass

    def work_on_the_hour(self, row_data: RowData):
        pass

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
        if len(self.market_uni.positions) < 1:
            return False

        mb = self.market_uni.get_market_balance()

        self.last_collect_fee0 += mb.base_uncollected
        self.last_collect_fee1 += mb.quote_uncollected
        self.market_uni.remove_all_liquidity()
        for b_key in self.market_aave.borrow_keys:
            swap_amount = self.market_aave.get_borrow(b_key).amount - self.broker.assets[config.weth].balance
            if swap_amount > 0:
                self.market_uni.buy(swap_amount * (1 + self.market_uni.pool_info.fee_rate))
            self.market_aave.repay(b_key)
        for s_key in self.market_aave.supply_keys:
            self.market_aave.withdraw(s_key)
        self.market_uni.sell(self.broker.assets[config.weth].balance)
        return True

    def get_cash_net_value(self, price: pd.Series):
        return Decimal(sum([asset.balance * price[asset.name] for asset in self.broker.assets.values()]))

    def get_net_value_without_fee(self, price):
        cash = self.get_cash_net_value(price)
        lp_value = 0
        sqrt_price = base_unit_price_to_sqrt_price_x96(
            price[config.weth.name],
            self.market_uni.pool_info.token0.decimal,
            self.market_uni.pool_info.token1.decimal,
            self.market_uni.pool_info.is_token0_quote,
        )
        for pos_key, pos in self.market_uni.positions.items():
            amount0, amount1 = V3CoreLib.get_token_amounts(
                self.market_uni.pool_info, pos_key, sqrt_price, pos.liquidity
            )
            lp_value += amount0 * price[config.usdc.name] + amount1 * price[config.weth.name]

        aave_status = self.market_aave.get_market_balance()
        all_value = cash + aave_status.net_value + lp_value
        # account_status = self.broker.get_account_status(price)
        return all_value

    def after_bar(self, row_data: RowData):
        mb = self.market_uni.get_market_balance()
        self.fee0.append(mb.base_uncollected + self.last_collect_fee0)
        self.fee1.append(mb.quote_uncollected + self.last_collect_fee1)
        value = self.get_net_value_without_fee(row_data.prices)
        self.net_value_without_fee_list.append(value)

        pass
