#!/usr/bin/env python

from decimal import Decimal
import asyncio
import time
import traceback
import argparse
from config.config import *
from exchanges.backpack_client import BackpackClient
from helpers.logger import setup_logger
from model.trading_config import TradingConfig

logger = setup_logger('earn_a_little_market_maker', 'market_maker')


class TradingBot:
    def __init__(
            self,
            config: TradingConfig,
            base_order_amount: float = 40,
            take_profit: float = 0.1 / 100,
            stop_loss: float = 0.10,
            direction: str = 'buy',
            max_orders: int = 10,
            grid_price_factor: float = 1.5,
            grid_base_spacing: float = 0.01,
            grid_quantity_factor: float = 1.5
    ):
        self.config = config
        self.logger = logger

        self.exchange_client = BackpackClient(
            config.public_key,
            config.secret_key,
            config.ticker,
            config.market_type
        )

        self.balance = None

        # Trading state
        self.active_close_orders = []
        self.last_close_orders = 0

        self.base_order_amount = base_order_amount
        self.quantity = None

        self.take_profit = take_profit
        self.stop_loss = stop_loss
        self.direction = direction
        self.max_orders = max_orders

        self.direction = direction
        self.close_order_side = 'buy' if self.direction == 'sell' else 'sell'

        # 网格价格因子和数量因子，如果改成为1，就变成是普通网格。
        self.grid_price_factor = grid_price_factor
        self.grid_base_spacing = grid_base_spacing

        if self.grid_base_spacing <= 0 or self.grid_base_spacing >= 1:
            self.grid_base_spacing = 0.01

        if self.grid_price_factor <= 1:
            self.grid_price_factor = 1

        self.grid_quantity_factor = grid_quantity_factor

        self.contract_id = None
        self.tick_size = None
        self.min_quantity = None

    async def get_latest_avg_price(self):
        bids, asks = await self.exchange_client.get_latest_bids_asks(self.contract_id)

        bids = [float(b[0]) for b in bids[-5:]] if bids else []
        asks = [float(a[0]) for a in asks[:5]] if asks else []

        if not bids or not asks:
            self.logger.warning("买卖盘数据不完整，跳过")
            await asyncio.sleep(0.1)
            return False

        min_ask = min(asks)
        max_bid = max(bids)
        avg_price = (max_bid + min_ask) / 2.0
        return avg_price

    async def place_all_open_orders(self) -> bool:
        """Place an order and monitor its execution."""
        try:
            self.logger.info(
                f'place open order, contract id: {self.contract_id}, '
                f'quantity: {self.quantity}, direction: {self.direction}')

            avg_price = await self.get_latest_avg_price()

            tmp_grid_price_factor = 0
            for i in range(self.max_orders):
                curr_quantity = self.quantity * (self.grid_quantity_factor ** i)

                curr_grid_price_factor = tmp_grid_price_factor + self.grid_price_factor ** i

                if self.direction == 'buy':
                    order_price = round(float(avg_price) * (1 - self.grid_base_spacing * curr_grid_price_factor), 2)
                else:
                    order_price = round(float(avg_price) * (1 + self.grid_base_spacing * curr_grid_price_factor), 2)

                tmp_grid_price_factor = curr_grid_price_factor

                if self.direction == 'buy':
                    await self.exchange_client.place_buy_limit_order(
                        self.contract_id,
                        order_price,
                        curr_quantity
                    )
                else:
                    await self.exchange_client.place_sell_limit_order(
                        self.contract_id,
                        order_price,
                        curr_quantity
                    )

                time.sleep(0.1)

            return True

        except Exception as e:
            self.logger.error(f"Error placing order: {e}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return False

    async def _handle_place_close_order(self, amount: Decimal, avg_price: Decimal):
        if abs(amount) < self.min_quantity:
            self.logger.info(f'there is no position, skip it. amount: {amount}, avg price: {avg_price}')
            return True

        # Place close order
        close_side = self.close_order_side
        if close_side == 'sell':
            close_price = float(avg_price) * (1 + self.take_profit)
        else:
            close_price = float(avg_price) * (1 - self.take_profit)

        if self.close_order_side == 'sell':
            await self.exchange_client.place_sell_limit_order(
                self.contract_id,
                close_price,
                float(amount)
            )
        else:
            await self.exchange_client.place_buy_limit_order(
                self.contract_id,
                close_price,
                float(amount)
            )

    def get_active_open_order_count(self):
        active_orders = self.exchange_client.get_active_orders(self.contract_id)

        # Filter close orders
        count = 0
        for order in active_orders:
            if order.side == self.direction:
                count += 1

        return count

    async def close_all_active_open_orders(self):
        # Get active orders
        active_orders = self.exchange_client.get_active_orders(self.contract_id)

        # Filter close orders
        self.active_close_orders = []
        for order in active_orders:
            if order.side == self.direction:
                self.active_close_orders.append({
                    'id': order.order_id,
                    'price': order.price,
                    'size': order.size
                })
        self.logger.info(
            f'start to close all active open orders, active orders count: {len(self.active_close_orders)}, '
            f'active orders: {self.active_close_orders}')

        for i in range(len(self.active_close_orders)):
            try:
                order_id = self.active_close_orders[i].get('id')
                cancel_order_result = await self.exchange_client.cancel_order(order_id)
                self.logger.info(f'cancel order: {order_id}, cancel order result: {cancel_order_result}')
                time.sleep(0.1)
            except Exception as e:
                self.logger.warning(f'exception in cancel order: {e}')

    def close_all_orders(self):
        # Get active orders
        active_orders = self.exchange_client.get_active_orders(self.contract_id)

        # Filter close orders
        self.active_close_orders = []
        for order in active_orders:
            self.active_close_orders.append({
                'id': order.order_id,
                'price': order.price,
                'size': order.size
            })

        self.logger.info(
            f'start to close all active orders, active orders count: {len(self.active_close_orders)}, '
            f'active orders: {self.active_close_orders}')

        for i in range(len(self.active_close_orders)):
            try:
                order_id = self.active_close_orders[i].get('id')
                cancel_order_result = self.exchange_client.cancel_order(order_id)
                self.logger.info(f'cancel order: {order_id}, cancel order result: {cancel_order_result}')
            except Exception as e:
                self.logger.warning(f'exception in cancel order: {e}')

    def get_active_open_orders(self):
        # Get active orders
        active_orders = self.exchange_client.get_active_orders(self.contract_id)

        # Filter close orders
        active_open_orders = []
        for order in active_orders:
            if order.side == self.direction:
                active_open_orders.append({
                    'id': order.order_id,
                    'price': order.price,
                    'size': order.size
                })

        return active_open_orders

    def get_active_close_orders(self):
        active_orders = self.exchange_client.get_active_orders(self.contract_id)

        active_close_orders = []
        total_size = 0
        for order in active_orders:
            if order.side == self.close_order_side:
                active_close_orders.append({
                    'id': order.order_id,
                    'price': order.price,
                    'size': order.size
                })

                total_size += order.size

        return active_close_orders, total_size

    async def close_all_active_close_orders(self):
        # Get active orders
        active_orders = self.exchange_client.get_active_orders(self.contract_id)

        # Filter close orders
        self.active_close_orders = []
        for order in active_orders:
            if order.side == self.close_order_side:
                self.active_close_orders.append({
                    'id': order.order_id,
                    'price': order.price,
                    'size': order.size
                })
        self.logger.info(
            f'start to close all active orders, active orders count: {len(self.active_close_orders)}, '
            f'active orders: {self.active_close_orders}')

        for i in range(len(self.active_close_orders)):
            try:
                order_id = self.active_close_orders[i].get('id')
                cancel_order_result = await self.exchange_client.cancel_order(order_id)
                self.logger.info(f'cancel order: {order_id}, cancel order result: {cancel_order_result}')
            except Exception as e:
                self.logger.warning(f'exception in cancel order: {e}')

    async def check_and_reset_quantity(self):
        if self.quantity is None:
            current_avg_price = await self.get_latest_avg_price()
            self.quantity = max(self.min_quantity, self.base_order_amount / current_avg_price)


    async def run(self):
        """Main trading loop."""
        try:
            self.contract_id, self.tick_size, self.min_quantity = await self.exchange_client.update_contract_attributes()

            self.balance = self.exchange_client.get_account_balance()

            await self.check_and_reset_quantity()

            self.logger.info(
                f'trade direct: {self.direction}, '
                f'contract id: {self.contract_id}, '
                f'tick size: {self.tick_size}, '
                f'base trade size: {self.base_order_amount}, '
                f'base trade quantity: {self.quantity}')

            sleep_times = 30
            await self.close_all_active_open_orders()

            while True:
                try:
                    await self.check_and_reset_quantity()

                    active_order_count = self.get_active_open_order_count()
                    position_amt, position_entry_price = self.exchange_client.get_account_positions()
                    if abs(position_amt) < self.min_quantity:
                        if active_order_count == self.max_orders:
                            self.logger.info(
                                f'active order count: {active_order_count}, '
                                f'max orders: {self.max_orders}')
                        else:
                            await self.close_all_active_open_orders()
                            await self.place_all_open_orders()
                    else:
                        await self.close_all_active_close_orders()
                        await self._handle_place_close_order(position_amt, position_entry_price)

                    self.last_close_orders += 1

                    time.sleep(sleep_times)
                except Exception as e:
                    self.logger.warning(f"exception in process: {e}")
                    traceback.print_exc()
        except Exception as e:
            self.logger.error(f"Critical error: {e}")
        finally:
            pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Backpack multi grid bot strategy",
        epilog="example: python3 bp_multi_grid_bot.py --ticker ETH --market-type PERP --key xxx "
               "--secret xxx --order-size 50 --direction buy --max-orders 10 --grid-price-factor 1.5 "
               "--grid-quantity-factor 1.5 --take-profit 0.0001"
    )

    # 添加可选参数
    parser.add_argument(
        "-t", "--ticker",
        default=backpack_ticker,
        help="ticker: such as ETH"
    )

    parser.add_argument(
        "-m", "--market-type",
        choices=['PERP', 'SPOT'],
        default=backpack_market_type,
        help="PERP, SPOT"
    )

    parser.add_argument(
        "-k", "--key",
        default=backpack_public_key,
        help="API Key"
    )

    parser.add_argument(
        "-s", "--secret",
        default=backpack_secret_key,
        help="API Secret"
    )

    parser.add_argument(
        "-o", "--order-size",
        default=backpack_base_order_size_usd,
        help="Order size per trade"
    )

    parser.add_argument(
        "-d", "--direction",
        default='buy',
        help="buy: 做多, sell: 做空"
    )

    parser.add_argument(
        "-max", "--max-orders",
        default=10,
        help="max orders"
    )

    parser.add_argument(
        "-gb", "--grid-base-spacing",
        default=0.01,
        help="grid price base spacing"
    )

    parser.add_argument(
        "-gp", "--grid-price-factor",
        default=1.5,
        help="grid price factor"
    )

    parser.add_argument(
        "-gq", "--grid-quantity-factor",
        default=1.5,
        help="grid quantity factor"
    )

    parser.add_argument(
        "-tp", "--take-profit",
        default=2 / 10000,
        help="take profit"
    )

    parser.add_argument(
        "-sl", "--stop-loss",
        default=0.10,
        help="stop loss"
    )

    # 解析参数
    args = parser.parse_args()

    # 打印解析结果
    logger.info("args:")
    for arg_name, arg_value in vars(args).items():
        logger.info(f"  {arg_name}: {arg_value}")

    _ticker = args.ticker
    _market_type = args.market_type
    _public_key = args.key
    _secret_key = args.secret
    _quantity = float(args.order_size)
    _direction = args.direction
    _max_orders = int(args.max_orders)

    _grid_price_factor = float(args.grid_price_factor)
    _grid_base_spacing = float(args.grid_base_spacing)
    _grid_quantity_factor = float(args.grid_quantity_factor)

    _take_profit = float(args.take_profit)
    _stop_loss = float(args.stop_loss)

    logger.info(
        f'finished init grid config, ticker: {_ticker}, '
        f':market type: {_market_type}, '
        f'public key: {_public_key}, '
        f'secret key: {_secret_key}, '
        f'quantity: {_quantity}, '
        f'direction: {_direction}, '
        f'max orders: {_max_orders}, '
        f'grid price factor: {_grid_price_factor}, '
        f'grid quantity factor: {_grid_quantity_factor}, '
        f'take profit: {_take_profit}, '
        f'stop loss: {_stop_loss}')

    _config = TradingConfig(
        ticker=_ticker,
        market_type=_market_type,
        public_key=_public_key,
        secret_key=_secret_key
    )

    bot = TradingBot(
        _config,
        base_order_amount=_quantity,
        take_profit=_take_profit,
        stop_loss=_stop_loss,
        direction=_direction,
        max_orders=_max_orders,
        grid_price_factor=_grid_price_factor,
        grid_base_spacing=_grid_base_spacing,
        grid_quantity_factor=_grid_quantity_factor
    )

    try:
        asyncio.run(bot.run())
    except Exception as e:
        print(f"Bot execution failed: {e}")
