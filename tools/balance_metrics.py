#!/usr/bin/env python

import asyncio

from config.config import *
from exchanges.backpack_client import BackpackClient
from model.trading_config import TradingConfig


async def core():
    _config = TradingConfig(
        ticker=backpack_ticker,
        market_type=backpack_market_type,
        public_key=backpack_public_key,
        secret_key=backpack_secret_key
    )

    exchange_client = BackpackClient(
        _config.public_key,
        _config.secret_key,
        _config.ticker,
        _config.market_type
    )

    (contract_id, tick_size, min_quantity) = await exchange_client.update_contract_attributes()

    balance = await exchange_client.get_account_balance()
    avail_balance = balance['USDC']['available']

    history_trades = []
    for i in range(3):
        history_trades = await exchange_client.get_historic_trade(contract_id)
        if len(history_trades) == 0:
            await asyncio.sleep(5)
        else:
            break

    total_cnt = 0
    total_maker_cnt = 0
    total_taker_cnt = 0
    for i in range(len(history_trades)):
        quantity = float(history_trades[i]['quantity']) * float(history_trades[i]['price'])
        total_cnt += quantity
        if history_trades[i]['isMaker']:
            total_maker_cnt += quantity
        else:
            total_taker_cnt += quantity

    print(
        f'当前余额: {avail_balance}, '
        f'总交易量: {total_cnt:.2f}, '
        f'限价单: {total_maker_cnt:.2f}, '
        f'市价单: {total_taker_cnt:.2f}')

    return avail_balance, total_cnt, total_maker_cnt, total_taker_cnt



if __name__ == '__main__':
    asyncio.run(core())
