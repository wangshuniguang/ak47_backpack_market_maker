#!/usr/bin/env python

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import List, Tuple, Dict
import asyncio

from bpx.account import Account
from bpx.constants.enums import OrderTypeEnum, TimeInForceEnum
from bpx.public import Public

from helpers.logger import setup_logger
from model.order_info import OrderInfo
from model.order_result import OrderResult

logger = setup_logger('backpack_client')


class CustomAccountClient:
    def __init__(self, account_client, broker_id='2110'):
        self.account_client = account_client
        self.broker_id = broker_id
        self.logger = logger

    def execute_order(self, *args, **kwargs):
        request_config = self.account_client.__class__.__bases__[0].execute_order(
            self.account_client, *args, **kwargs
        )

        if hasattr(request_config, 'headers'):
            request_config.headers["X-BROKER-ID"] = self.broker_id

        return self.account_client.http_client.post(
            url=request_config.url,
            headers=request_config.headers,
            data=request_config.data,
        )

    def __getattr__(self, name):
        return getattr(self.account_client, name)


class BackpackClient(object):

    def __init__(self, public_key, secret_key, ticker, market_type='PERP'):
        self.public_key = public_key
        self.secret_key = secret_key

        self.ticker = ticker
        self.market_type = market_type

        if not self.public_key or not self.secret_key:
            raise ValueError("BACKPACK_PUBLIC_KEY and BACKPACK_SECRET_KEY must be set in environment variables")

        self.public_client = Public()
        self.account_client = Account(
            public_key=self.public_key,
            secret_key=self.secret_key
        )

        self.custom_client = CustomAccountClient(self.account_client)

        self.logger = logger

        self.contract_id = ''
        self.tick_size = Decimal(0)
        self.min_quantity = Decimal(0)

        self.logger.info(
            f'public key: {self.public_key}, secret key: {self.secret_key}, '
            f'account: {self.account_client}')

    def round_to_tick(self, price) -> Decimal:
        price = Decimal(price)

        tick = self.tick_size
        # quantize forces price to be a multiple of tick
        return price.quantize(tick, rounding=ROUND_HALF_UP)

    @staticmethod
    def align_floor(quantity: Decimal, min_quantity: Decimal) -> Decimal:
        """
        向下对齐到最小交易单位的整数倍

        Args:
            quantity: 原始数量
            min_quantity: 最小数量

        Returns:
            向下对齐后的数量
        """
        multiplier = (Decimal(quantity) / Decimal(min_quantity)).to_integral_value(rounding=ROUND_DOWN)
        return multiplier * min_quantity

    async def get_recent_trades(self, contract_id, limit):
        return self.public_client.get_recent_trades(contract_id, limit)

    async def get_account_balance(self):
        return self.account_client.get_balances()

    async def get_historic_trade(self, contract_id):
        all_trades = []
        start_idx = 0
        length = 1000
        while True:
            try:
                current_trades = self.account_client.get_fill_history(
                    contract_id,
                    length,
                    start_idx
                )

                if isinstance(current_trades, list):
                    start_idx += len(current_trades)
                    all_trades.extend(current_trades)
                elif isinstance(current_trades, dict) and 'code' in current_trades:
                    print(f'start index: {start_idx}, len: {len(current_trades)}, {current_trades}')

                print(f'start index: {start_idx}, len: {len(current_trades)}')

                if len(current_trades) < length:
                    break

                await asyncio.sleep(5)

            except Exception as e:
                self.logger.warning(f'exception in process history record: {e}')

        return all_trades

    async def get_account_all_positions(self) -> List[Dict]:
        account_positions = []
        positions_data = None
        try:
            positions_data = self.account_client.get_open_positions()
            for position in positions_data:
                account_positions.append({
                    'symbol': position.get('symbol', ''),
                    'netQuantity': Decimal(position.get('netQuantity', 0))
                })
        except Exception as e:
            self.logger.info(
                f'exception in get account all positions: {e}, '
                f'positions data: {positions_data}')

        return account_positions

    async def place_sell_limit_order(self, contract_id, order_price, quantity, order_type=TimeInForceEnum.GTC):
        self.logger.info(
            f'place sell limit order, contract id: {contract_id}, order price: {order_price}, '
            f'quantity: {quantity}')

        try:
            align_quantity = BackpackClient.align_floor(quantity, self.min_quantity)
        except Exception as e:
            self.logger.warning(
                f'exception in align: {e}, quantity: {quantity}, min quantity: {self.min_quantity}')
            align_quantity = round(quantity, 4)

        try:
            order_result = self.custom_client.execute_order(
                symbol=contract_id,
                side='Ask',
                order_type=OrderTypeEnum.LIMIT,
                quantity=str(align_quantity),
                price=str(self.round_to_tick(order_price)),
                post_only=True,
                time_in_force=order_type
            )

            if not order_result:
                self.logger.info(
                    f'exception in place order, symbol: {contract_id}, '
                    f'quantity: {align_quantity}, order price: {order_price}')
                return None

            if 'code' in order_result:
                message = order_result.get('message', 'Unknown error')
                self.logger.warning(f"[OPEN] Error placing order: {message}")
                return None

            order_id = order_result.get('id')
            if not order_id:
                self.logger.error(f"[OPEN] No order ID in response: {order_result}")

            return order_id
        except Exception as e:
            self.logger.warning(f'exception in batch place orders: {e}, order_result: {e}')

        return None

    async def place_sell_market_order(self, contract_id, quantity, order_type=TimeInForceEnum.GTC):
        self.logger.info(
            f'place sell market order, contract id: {contract_id}, '
            f'quantity: {quantity}, order type: {order_type}')

        try:
            align_quantity = BackpackClient.align_floor(quantity, self.min_quantity)
        except Exception as e:
            self.logger.warning(
                f'exception in align: {e}, quantity: {quantity}, min quantity: {self.min_quantity}')
            align_quantity = round(quantity, 4)

        try:
            order_result = self.custom_client.execute_order(
                symbol=contract_id,
                side='Ask',
                order_type=OrderTypeEnum.MARKET,
                quantity=str(align_quantity),
                post_only=False,
                time_in_force=order_type
            )

            self.logger.info(
                f'sell market order execute result: {order_result},'
                f'contract id: {contract_id}, '
                f'quantity: {quantity}')

            if not order_result:
                self.logger.info(
                    f'exception in place market order, symbol: {contract_id}, '
                    f'quantity: {align_quantity}')
                return None

            if 'code' in order_result:
                message = order_result.get('message', 'Unknown error')
                self.logger.warning(f"[OPEN] Error placing order: {message}")
                return None

            order_id = order_result.get('id')
            if not order_id:
                self.logger.error(f"[OPEN] No order ID in response: {order_result}")

            return order_id
        except Exception as e:
            self.logger.warning(f'exception in batch place orders: {e}')

        return None

    async def place_buy_limit_order(
            self, contract_id, order_price, quantity,
            order_type=TimeInForceEnum.GTC):
        quantity = max(quantity, self.min_quantity)

        self.logger.info(
            f'place buy limit order, contract id: {contract_id}, order price: {order_price}, '
            f'quantity: {quantity}')

        try:
            align_quantity = BackpackClient.align_floor(quantity, self.min_quantity)
        except Exception as e:
            self.logger.warning(
                f'exception in align: {e}, quantity: {quantity}, min quantity: {self.min_quantity}')
            align_quantity = round(quantity, 4)

        try:
            order_result = self.custom_client.execute_order(
                symbol=contract_id,
                side='Bid',
                order_type=OrderTypeEnum.LIMIT,
                quantity=str(align_quantity),
                price=str(self.round_to_tick(order_price)),
                post_only=True,
                time_in_force=order_type
            )

            if not order_result:
                self.logger.info(
                    f'exception in place order, symbol: {contract_id}, '
                    f'quantity: {align_quantity}, order price: {order_price}')

            if 'code' in order_result:
                message = order_result.get('message', 'Unknown error')
                self.logger.warning(f"[OPEN] Error placing order: {message}")
                return

            order_id = order_result.get('id')
            if not order_id:
                self.logger.error(f"[OPEN] No order ID in response: {order_result}")
            return order_id
        except Exception as e:
            self.logger.info(f'exception in batch place orders: {e}')

        return None

    async def close_position_with_market_order(self, contract_id, real_position):
        quantity = abs(real_position)
        side = 'Bid' if real_position < 0 else 'Ask'

        self.logger.info(
            f'close position with market order, contract id: {contract_id}, '
            f'real position: {real_position}, '
            f'quantity: {quantity}, '
            f'side: {side}')

        try:
            align_quantity = BackpackClient.align_floor(quantity, self.min_quantity)
        except Exception as e:
            self.logger.warning(
                f'exception in align: {e}, quantity: {quantity}, min quantity: {self.min_quantity}')
            align_quantity = round(quantity, 4)

        try:
            order_result = self.custom_client.execute_order(
                symbol=contract_id,
                side=side,
                order_type=OrderTypeEnum.MARKET,
                quantity=str(align_quantity),
                post_only=False,
                reduce_only=True
            )

            self.logger.info(
                f'buy market order execute result: {order_result},'
                f'contract id: {contract_id}, '
                f'quantity: {quantity}')

            if not order_result:
                self.logger.info(
                    f'exception in place order, symbol: {contract_id}, '
                    f'quantity: {align_quantity}, ')

            if 'code' in order_result:
                message = order_result.get('message', 'Unknown error')
                self.logger.warning(f"[OPEN] Error placing order: {message}")
                return

            order_id = order_result.get('id')
            if not order_id:
                self.logger.error(f"[OPEN] No order ID in response: {order_result}")
            return order_id
        except Exception as e:
            self.logger.info(f'exception in batch place orders: {e}')

        return None

    async def place_buy_market_order(
            self, contract_id, quantity,
            order_type=TimeInForceEnum.GTC):
        quantity = max(quantity, self.min_quantity)

        self.logger.info(
            f'place buy market order, contract id: {contract_id}, '
            f'quantity: {quantity}, order type: {order_type}')

        try:
            align_quantity = BackpackClient.align_floor(quantity, self.min_quantity)
        except Exception as e:
            self.logger.warning(
                f'exception in align: {e}, quantity: {quantity}, min quantity: {self.min_quantity}')
            align_quantity = round(quantity, 4)

        try:
            order_result = self.custom_client.execute_order(
                symbol=contract_id,
                side='Bid',
                order_type=OrderTypeEnum.MARKET,
                quantity=str(align_quantity),
                post_only=False,
                time_in_force=order_type
            )

            self.logger.info(
                f'buy market order execute result: {order_result},'
                f'contract id: {contract_id}, '
                f'quantity: {quantity}')

            if not order_result:
                self.logger.info(
                    f'exception in place order, symbol: {contract_id}, '
                    f'quantity: {align_quantity}, ')

            if 'code' in order_result:
                message = order_result.get('message', 'Unknown error')
                self.logger.warning(f"[OPEN] Error placing order: {message}")
                return

            order_id = order_result.get('id')
            if not order_id:
                self.logger.error(f"[OPEN] No order ID in response: {order_result}")
            return order_id
        except Exception as e:
            self.logger.info(f'exception in batch place orders: {e}')

        return None

    async def get_latest_bids_asks(self, contract_id):
        order_book = self.public_client.get_depth(contract_id)
        if not isinstance(order_book, dict):
            return OrderResult(success=False, error_message='Unexpected order book response format')

        bids = order_book.get('bids', [])
        asks = order_book.get('asks', [])
        return bids, asks

    async def cancel_order(self, order_id: str) -> OrderResult:
        try:
            if order_id is None or len(order_id) == 0:
                return OrderResult(success=False, error_message=f'订单ID为空')

            # Cancel the order using Backpack SDK
            cancel_result = self.account_client.cancel_order(
                symbol=self.contract_id,
                order_id=order_id
            )

            if not cancel_result:
                return OrderResult(success=False, error_message='Failed to cancel order')

            return OrderResult(success=True)

        except Exception as e:
            return OrderResult(success=False, error_message=str(e))

    def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Get active orders for a contract using official SDK."""
        try:
            # Get active orders using Backpack SDK
            active_orders = self.account_client.get_open_orders(symbol=contract_id)

            if not active_orders:
                return []

            # Return the orders list as OrderInfo objects
            order_list = active_orders if isinstance(active_orders, list) else active_orders.get('orders', [])
            orders = []

            for order in order_list:
                if isinstance(order, dict):
                    side = 'sell'
                    if order.get('side', '') == 'Bid':
                        side = 'buy'
                    elif order.get('side', '') == 'Ask':
                        side = 'sell'

                    orders.append(OrderInfo(
                        order_id=order.get('id', ''),
                        side=side,
                        size=Decimal(order.get('quantity', 0)),
                        price=Decimal(order.get('price', 0)),
                        status=order.get('status', ''),
                        filled_size=Decimal(order.get('executedQuantity', 0)),
                        remaining_size=Decimal(order.get('quantity', 0)) - Decimal(order.get('executedQuantity', 0))
                    ))

            return orders

        except Exception:
            return []

    def get_account_positions(self) -> Tuple[Decimal, Decimal]:
        """Get account positions using official SDK."""
        position_amt = 0
        position_entry_price = 0
        positions_data = None
        for i in range(30):
            try:
                positions_data = self.account_client.get_open_positions()
                for position in positions_data:
                    if position.get('symbol', '') == self.contract_id:
                        position_amt = Decimal(position.get('netQuantity', 0))
                        position_entry_price = abs(Decimal(position.get('entryPrice', 0)))
                        break

                return position_amt, position_entry_price
            except Exception as e:
                print(f'exception in get account positions: {e}, position data: {positions_data}')

        return position_amt, position_entry_price

    async def update_contract_attributes(self) -> Tuple[str, Decimal, Decimal]:
        """Get contract ID for a ticker."""
        ticker = self.ticker
        if len(ticker) == 0:
            self.logger.error("Ticker is empty")
            raise ValueError("Ticker is empty")

        markets = self.public_client.get_markets()

        min_quantity = 0
        for market in markets:
            if (market.get('marketType', '') == self.market_type and
                    market.get('baseSymbol', '') == ticker and
                    market.get('quoteSymbol', '') == 'USDC'):
                self.logger.info(f'get contract attributes, ticker: {ticker}, market: {market}')
                self.contract_id = market.get('symbol', '')
                min_quantity = Decimal(market.get('filters', {}).get('quantity', {}).get('minQuantity', 0))
                self.tick_size = Decimal(market.get('filters', {}).get('price', {}).get('tickSize', 0))
                self.min_quantity = min_quantity
                break

        self.logger.info(
            f'contract id: {self.contract_id}, '
            f'market type: {self.market_type}, '
            f'min quantity: {min_quantity}, '
            f'tick size: {self.tick_size}')

        if self.contract_id == '':
            self.logger.error("Failed to get contract ID for ticker")
            raise ValueError("Failed to get contract ID for ticker")

        if self.tick_size == 0:
            self.logger.error("Failed to get tick size for ticker")
            raise ValueError("Failed to get tick size for ticker")

        return self.contract_id, self.tick_size, self.min_quantity
