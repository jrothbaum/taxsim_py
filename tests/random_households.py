"""Seeded random households for comparing calculation engines.

Mostly ordinary families, with a few high earners (to reach the top brackets,
AMT and the net investment income tax) and occasional negative amounts. Dollar
amounts scale with the year (about 4% a year, 2022 as the base) so that every
era's low-income provisions and bracket ranges are exercised.
"""

import numpy as np
import polars as pl


def random_households(state: int, year: int, rows: int, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng([seed, state, year])
    growth = 1.04 ** (year - 2022)

    def amount(scale: float, zero_share: float, negative_share: float = 0.05) -> np.ndarray:
        value = np.round(rng.exponential(scale * growth, rows) * (rng.random(rows) > zero_share))
        return np.where(rng.random(rows) < negative_share, -value, value)

    high = rng.random(rows) < 0.05
    depx = rng.choice([0, 0, 0, 1, 2, 3, 5], rows)
    return pl.DataFrame(dict(
        state=state, year=year,
        mstat=rng.choice([1, 1, 2, 2, 3, 6, 8, 33, 66], rows), depx=depx,
        page=rng.choice([0, 25, 45, 64, 65, 70, 90], rows), sage=rng.choice([0, 0, 18, 40, 64, 65, 80], rows),
        pwages=amount(45_000, .1) * np.where(high, 12, 1), swages=amount(30_000, .5) * np.where(high, 6, 1),
        dividends=amount(1_500, .6, 0), intrec=amount(1_200, .5, 0), stcg=amount(3_000, .85),
        ltcg=amount(9_000, .8) * np.where(high, 20, 1), otherprop=amount(5_000, .85), nonprop=amount(2_000, .95),
        pensions=amount(18_000, .8, 0), gssi=amount(20_000, .8, 0), ui=amount(5_000, .93, 0),
        pui=amount(3_000, .96, 0), sui=amount(3_000, .96, 0), transfers=amount(2_000, .9, 0),
        proptax=amount(3_000, .5, 0), otheritem=amount(6_000, .6, 0), mortgage=amount(9_000, .7, 0),
        childcare=amount(2_500, .85, 0), scorp=amount(8_000, .93), pbusinc=amount(7_000, .93),
        pprofinc=amount(6_000, .95), sbusinc=amount(5_000, .95), sprofinc=amount(4_000, .96),
        psemp=amount(9_000, .85), ssemp=amount(6_000, .93), rentpaid=amount(8_000, .7, 0),
        dep13=depx, dep17=depx, dep18=depx, dep6=depx * rng.integers(0, 2, rows),
    ))
