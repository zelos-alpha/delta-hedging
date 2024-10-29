from datetime import timedelta, datetime, date
from decimal import Decimal
from typing import List

import pandas as pd
from tradingstrategy.chain import ChainId
from tradingstrategy.client import Client
from tradingstrategy.pair import PandasPairUniverse
from tradingstrategy.timebucket import TimeBucket

from demeter import ChainType
from demeter.uniswap import UniV3Pool, UniLpMarket
from demeter.uniswap.data import fillna
import config


class TradingStrategyUtil:
    time_bucket = TimeBucket.m1
    market = "uniswap-v3"

    @staticmethod
    def to_pair_desc(chain: ChainType, pool: UniV3Pool):
        return (
            ChainId[chain.name],
            TradingStrategyUtil.market,
            pool.base_token.name,
            pool.quote_token.name,
            pool.fee_rate,
        )

    @staticmethod
    def set_pair_id(pools: List, metadatas: List):
        for pool in pools:
            for pair in metadatas:
                if (
                        pair.token0_symbol == pool.token0.name
                        and pair.token1_symbol == pool.token1.name
                        and Decimal(str(pair.fee_tier)) == pool.fee_rate
                ):
                    setattr(pool, "pair_id", pair.pair_id)
                    break


def load_from_trading_strategy(
        chain: ChainType, pools: List[UniV3Pool], start: datetime | date, end: datetime | date, api_key=None
) -> pd.DataFrame:
    # Load pairs in all exchange
    if isinstance(start, date):
        start = datetime.combine(start, datetime.min.time())
    if isinstance(end, date):
        end = datetime.combine(end, datetime.min.time())
    print("Loading markets")
    client = Client.create_live_client(api_key=api_key)
    exchange_universe = client.fetch_exchange_universe()
    pairs_df = client.fetch_pair_universe().to_pandas()
    pair_universe = PandasPairUniverse(
        pairs_df[(pairs_df["dex_type"] == "uniswap_v3") & (pairs_df["chain_id"] == chain.value)],
        exchange_universe=exchange_universe,
    )
    pair_descriptions = [TradingStrategyUtil.to_pair_desc(chain, pool) for pool in pools]

    # Load metadata for the chosen trading pairs (pools)
    pair_metadata = [pair_universe.get_pair_by_human_description(desc) for desc in pair_descriptions]
    TradingStrategyUtil.set_pair_id(pools, pair_metadata)
    # Map to internal pair primary keys
    pair_ids = [pm.pair_id for pm in pair_metadata]

    # Load CLMM data for selected pairs
    clmm_df = client.fetch_clmm_liquidity_provision_candles_by_pair_ids(
        pair_ids,
        TradingStrategyUtil.time_bucket,
        start_time=start,
        end_time=end + timedelta(days=1),
    )
    return clmm_df


def load_clmm_data_to_uni_lp_market(
        market: UniLpMarket,
        df: pd.DataFrame,
        start_date: datetime,
        end_date: datetime,
):
    assert isinstance(market, UniLpMarket)
    assert isinstance(df, pd.DataFrame)

    df = df.rename(
        columns={
            "bucket": "timestamp",
            "close_tick": "closeTick",
            "open_tick": "openTick",
            "low_tick": "lowestTick",
            "high_tick": "highestTick",
            "in_amount0": "inAmount0",
            "in_amount1": "inAmount1",
            "current_liquidity": "currentLiquidity",
            "net_amount0": "netAmount0",
            "net_amount1": "netAmount1",
        }
    )

    del df["pair_id"]  # Fails in fillna() below

    #
    # Following is copy paste from market.py from Demeter
    # I have no idea what it is supposed to do
    #

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.set_index("timestamp", inplace=True)

    # fill empty row (first minutes in a day, might be blank)
    full_indexes = pd.date_range(
        start=start_date,
        end=datetime.combine(end_date, datetime.min.time()) + timedelta(days=1) - timedelta(minutes=1),
        freq="1min",
    )
    df = df.reindex(full_indexes)

    df: pd.DataFrame = fillna(df)
    if pd.isna(df.iloc[0]["closeTick"]):
        df = df.bfill()

    market.add_statistic_column(df)
    market.data = df


def load_data(actuator, market_uni, market_aave, start_date, end_date):
    pool_data = load_from_trading_strategy(
        ChainType[config.CHAIN.name], [market_uni.pool_info], start_date, end_date, config.APP.ts_apikey
    )
    load_clmm_data_to_uni_lp_market(market_uni, pool_data, start_date, end_date)
    market_aave.load_data(ChainType.ethereum, [config.usdc, config.weth], start_date, end_date)
    actuator.set_price(market_uni.get_price_from_data())


def get_file_name(title, start: date, end: date):
    return f"{title}.{start.strftime('%Y%m%d')}~{end.strftime('%Y%m%d')}"
