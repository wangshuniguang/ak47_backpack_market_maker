#!/usr/bin/env python

import asyncio
import time
import traceback
from decimal import Decimal

from config.config import *
from exchanges.backpack_client import BackpackClient
from helpers.logger import setup_logger
from model.trading_config import TradingConfig

logger = setup_logger('backpack_king_of_hedge', 'hedge')


class KingOfHedge(object):
    def __init__(self):
        self.logger = logger

        config = TradingConfig(
            ticker=backpack_ticker,
            public_key=backpack_public_key,
            secret_key=backpack_secret_key,
            market_type=backpack_market_type
        )
        self.config = config
        self.backpack_client = BackpackClient(
            config.public_key,
            config.secret_key,
            config.ticker,
            config.market_type
        )

        # 对冲账户配置
        hedge_config = TradingConfig(
            ticker=backpack_ticker,
            public_key=backpack_hedge_public_key,
            secret_key=backpack_hedge_secret_key,
            market_type=backpack_market_type
        )

        self.hedge_config = hedge_config
        self.backpack_hedge_client = BackpackClient(
            hedge_config.public_key,
            hedge_config.secret_key,
            hedge_config.ticker,
            hedge_config.market_type
        )

        self.contract_id = ''
        self.tick_size = Decimal(0)
        self.min_quantity = Decimal(0)

    async def get_need_hedge_positions(self):
        backpack_positions = await self.backpack_client.get_account_all_positions()
        hedge_positions = await self.backpack_hedge_client.get_account_all_positions()

        need_hedge_positions = []
        for i in range(len(backpack_positions)):
            position = backpack_positions[i]
            symbol = position.get('symbol')
            quantity = position.get('netQuantity')
            self.logger.info(f'symbol: {symbol}, quantity: {quantity}')

            real_hedge_quantity = 0
            for j in range(len(hedge_positions)):
                hedge_symbol = hedge_positions[j].get('symbol')
                hedge_quantity = float(hedge_positions[j].get('netQuantity'))

                if abs(hedge_quantity) == 0:
                    continue

                if symbol == hedge_symbol:
                    real_hedge_quantity = hedge_quantity
                    break

            need_hedge_quantity = -float(quantity) - float(real_hedge_quantity)
            need_hedge_positions.append({
                'symbol': symbol,
                'quantity': need_hedge_quantity
            })

        for j in range(len(hedge_positions)):
            hedge_symbol = hedge_positions[j].get('symbol')
            real_hedge_quantity = float(hedge_positions[j].get('netQuantity'))

            if abs(real_hedge_quantity) == 0:
                continue

            is_found = False
            for i in range(len(backpack_positions)):
                position = backpack_positions[i]
                symbol = position.get('symbol')
                quantity = position.get('netQuantity')
                self.logger.info(f'symbol: {symbol}, quantity: {quantity}')

                if hedge_symbol == symbol:
                    is_found = True
                    break

            if not is_found:
                need_hedge_positions.append({
                    'symbol': hedge_symbol,
                    'quantity': float(real_hedge_quantity)
                })

        self.logger.info(
            f'backpack positions: {backpack_positions}, '
            f'hedge positions: {hedge_positions}, '
            f'need hedge positions: {need_hedge_positions}')

        return need_hedge_positions

    async def do_smart_hedges(self, need_hedge_positions):
        for i in range(len(need_hedge_positions)):
            try:
                symbol = need_hedge_positions[i]['symbol']
                quantity = need_hedge_positions[i]['quantity']
                self.logger.info(f'do hedge, symbol: {symbol}, quantity: {quantity}')

                if quantity >= self.min_quantity:
                    try:
                        order_id = await self.backpack_hedge_client.place_buy_market_order(
                            symbol, abs(quantity))
                        self.logger.info(
                            f'place buy market order, symbol: {symbol}, '
                            f'quantity: {quantity}, order id: {order_id}')
                    except Exception as ee:
                        self.logger.warning(
                            f'exception in place buy limit orders: {ee}, '
                            f'{traceback.print_stack()}')
                elif quantity <= -self.min_quantity:
                    try:
                        order_id = await self.backpack_hedge_client.place_sell_market_order(
                            symbol, abs(quantity))
                        self.logger.info(
                            f'place sell market order, symbol: {symbol}, '
                            f'quantity: {quantity}, order id: {order_id}')
                    except Exception as ee:
                        self.logger.warning(
                            f'exception in place sell limit orders: {ee}, '
                            f'{traceback.print_stack()}')
            except Exception as e:
                self.logger.info(f'exception in do hedges: {e}, {traceback.print_stack()}')

    async def run_optimized_hedge(self):
        """优化后的对冲模块"""
        contract_info = await self.backpack_client.update_contract_attributes()
        self.contract_id, self.tick_size, self.min_quantity = contract_info

        hedge_contract_info = await self.backpack_hedge_client.update_contract_attributes()
        _, _, _ = hedge_contract_info

        self.logger.info(f'INIT -----> contract info: {contract_info}')

        last_hedge_time = 0
        hedge_interval = 1.0
        min_hedge_amount = self.min_quantity * 5

        while True:
            try:
                current_time = time.time()
                if current_time - last_hedge_time < hedge_interval:
                    await asyncio.sleep(0.1)
                    continue

                need_hedge_positions = await self.get_need_hedge_positions()
                self.logger.info(f'Need hedge positions: {need_hedge_positions}')

                # 只对冲达到阈值的头寸
                significant_hedge = any(
                    abs(pos['quantity']) >= min_hedge_amount
                    for pos in need_hedge_positions
                )

                if significant_hedge:
                    # 取消现有对冲订单
                    active_orders = self.backpack_hedge_client.get_active_orders(
                        self.contract_id
                    )
                    for order in active_orders:
                        await self.backpack_hedge_client.cancel_order(order.order_id)

                    await self.do_smart_hedges(need_hedge_positions)
                    last_hedge_time = current_time

                await asyncio.sleep(0.3)

            except Exception as e:
                self.logger.warning(f'Exception in hedge run: {e}')
                await asyncio.sleep(1)


async def main():
    # Create and run the bot
    bot = KingOfHedge()

    try:
        await bot.run_optimized_hedge()
    except Exception as e:
        print(f"Bot execution failed: {e}")
        # The bot's run method already handles graceful shutdown
        return


if __name__ == "__main__":
    asyncio.run(main())
