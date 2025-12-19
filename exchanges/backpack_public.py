#!/usr/bin/env python

import time
import pandas as pd
from bpx.public import Public


def get_last_day_5min_bars(symbol):
    pb = Public()
    df = pd.DataFrame(pb.get_klines(symbol, '5m', int(time.time()) - 24 * 3600))
    return df


if __name__ == '__main__':
    tmp_df = get_last_day_5min_bars('BTC_USDC')
    print(tmp_df.columns)
    print(tmp_df.shape[0], tmp_df.head(), tmp_df.tail())

    tmp_df['avg_ma_5'] = tmp_df.close.rolling(window=5).mean()
    tmp_df['avg_ma_144'] = tmp_df.close.rolling(window=144).mean()
    tmp_df.dropna(inplace=True)
    tmp_df.reset_index(drop=True, inplace=True)
    print(tmp_df.tail())
