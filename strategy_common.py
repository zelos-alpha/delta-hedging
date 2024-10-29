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










