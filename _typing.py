from datetime import date
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
