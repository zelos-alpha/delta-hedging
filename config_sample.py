from datetime import date

from _typing import AppConfig, ChainConfig, StgPool

from demeter import TokenInfo

APP = AppConfig(
    aave_data_path="/data/aave/ethereum/",
    to_path="/data/research/delta-hedging/",
    start=date(2024, 3, 1),
    end=date(2024, 10, 16),
    proxy="http://localhost:7890",
    ts_apikey="[trading strategy api key]"
)

CHAIN = ChainConfig("ethereum", "http://10.0.0.4:8545", "[etherscan key]")


usdc = TokenInfo("USDC", 6, "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48".lower())
usdt = TokenInfo("USDT", 6, "0xdAC17F958D2ee523a2206206994597C13D831ec7".lower())
dai = TokenInfo("DAI", 18, "0x6B175474E89094C44Da98b954EedeAC495271d0F".lower())
weth = TokenInfo("WETH", 18, "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2".lower())
wbtc = TokenInfo("WBTC", 8, "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599".lower())

POOLS = [
    StgPool(  # 0
        address="0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640".lower(),
        name="usdc-eth-005",
        fee=0.0005,
        token0=usdc,
        token1=weth,
        is_0_quote=True,
    )
]
