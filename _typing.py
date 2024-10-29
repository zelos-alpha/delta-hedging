from datetime import date
from decimal import Decimal
from typing import NamedTuple

from demeter import TokenInfo


class ChainConfig(NamedTuple):
    name: str
    rpc: str
    etherscan_key: str


class StgPool(NamedTuple):
    address: str
    name: str
    fee: float
    token0: TokenInfo
    token1: TokenInfo
    is_0_quote: bool


class AppConfig(NamedTuple):
    aave_data_path: str
    to_path: str
    start: date
    end: date
    proxy: str
    ts_apikey:str # trading strategy api key

class Amounts(NamedTuple):
    usdc_aave_supply: Decimal
    usdc_uni_init: Decimal
    eth_uni_lp: Decimal
    usdc_uni_lp: Decimal
    eth_aave_borrow: Decimal
    usdc_aave_borrow: Decimal


class CurrentPosition(NamedTuple):
    h: Decimal
    l: Decimal
    sigma: Decimal
    h_price: Decimal
    l_price: Decimal
    amounts: Amounts