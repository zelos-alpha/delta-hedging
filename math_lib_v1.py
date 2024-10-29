from scipy.optimize import minimize
import numpy as np
from math import sqrt


"""
https://medium.com/zelos-research/how-to-implement-uniswap-delta-neutral-strategy-with-lending-protocol-eee10371a77f
"""


def optimize_delta_neutral(ph, pl, alpha, delta=0):
    uni_amount_con = (1 - 1 / ph**0.5) / (1 - pl**0.5)

    liq = 1 / (2 - 1 / sqrt(ph) - sqrt(pl))
    # solution x
    # V_U_A x[0]:  value go into AAVE
    # V_U_init x[1]:  init usdc , not go into AAVE
    # V_U_uni x[2]:  usdc in uniswap
    # V_E_uni x[3]:  eth in uniswap
    # V_E_lend x[4]:  eth lend from aave
    # V_U_lend x[5]:  some eth lend from aave exchange to usdc

    # add constrains
    cons = (
        {"type": "eq", "fun": lambda x: x[0] + x[1] - 1},  # V_U_A + V_U_init = 1
        {"type": "eq", "fun": lambda x: x[2] * uni_amount_con - x[3]},  # uniswap providing constrain
        {
            "type": "eq",
            "fun": lambda x: (1 - 1 / ph**0.5) * (x[2] + x[3]) * liq - x[4] - delta,
        },  # delta netural
        # ineq
        {"type": "eq", "fun": lambda x: x[1] + x[5] - x[2]},  # amount relation for usdc
        {"type": "eq", "fun": lambda x: x[4] - x[3] - x[5]},  # amount relation for eth
        {"type": "eq", "fun": lambda x: alpha * x[0] - x[4]},  # relation for aave
        # all x >= 0
        {"type": "ineq", "fun": lambda x: x[0]},
        {"type": "ineq", "fun": lambda x: x[1]},
        {"type": "ineq", "fun": lambda x: x[2]},
        {"type": "ineq", "fun": lambda x: x[3]},
        {"type": "ineq", "fun": lambda x: x[5]},
    )
    init_x = np.array((0, 0, 0, 0, 0, 0))
    # # Method Nelder-Mead cannot handle constraints.
    res = minimize(lambda x: -(x[3] + x[2]), init_x, method="SLSQP", constraints=cons)

    return res.fun, res.x



