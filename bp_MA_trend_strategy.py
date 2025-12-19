#!/usr/bin/env python

import argparse
import asyncio
import time
from decimal import Decimal

from config.config import *
from exchanges.backpack_client import BackpackClient
from exchanges.backpack_public import get_last_day_5min_bars
from helpers.logger import setup_logger
from model.trading_config import TradingConfig

logger = setup_logger('ema_trend_strategy', 'market_maker')


class ProfessionalMATrendFollowStrategy:

    def __init__(
            self,
            config: TradingConfig = None,
            base_order_size_usd: float = 0.0
    ):
        self.config = config
        self.contract_id = ''
        self.tick_size = Decimal(0)
        self.min_quantity = Decimal(0)
        self.base_order_size_usd = base_order_size_usd
        self.last_avg_price = None
        self.real_q = 0

        # 创建交易所客户端
        try:
            # public_key, secret_key, ticker
            self.exchange_client = BackpackClient(
                config.public_key,
                config.secret_key,
                config.ticker,
                config.market_type
            )
        except ValueError as e:
            raise ValueError(f"创建交易所客户端失败: {e}")

        self.logger = logger
        self.logger.info(f"背包专业趋势跟随策略初始化完成")

    async def update_order_book_avg_price(self):
        # 获取市场数据
        bids, asks = await self.exchange_client.get_latest_bids_asks(self.contract_id)

        order_book = {
            'bids': bids[-5:],  # 简化size
            'asks': asks[:5]
        }

        bids = [float(b[0]) for b in bids[-5:]] if bids else []
        asks = [float(a[0]) for a in asks[:5]] if asks else []

        if not bids or not asks:
            self.logger.warning("买卖盘数据不完整，跳过")
            return

            # 构建订单簿
        min_ask = min(asks)
        max_bid = max(bids)
        s = (max_bid + min_ask) / 2.0
        self.last_avg_price = s

    def generate_ticker_tech_index(self):
        for i in range(3):
            try:
                df = get_last_day_5min_bars(self.contract_id)
                df['avg_ma_5'] = df.close.rolling(window=5).mean()
                df['avg_ma_144'] = df.close.rolling(window=144).mean()
                df.dropna(inplace=True)
                df.reset_index(drop=True, inplace=True)
                return df.copy()
            except Exception as e:
                logger.warning(f'exception in get ticker bars: {e}, retry times: {i + 1}')
                time.sleep(1)

    async def cancel_all_orders(self):
        current_orders = self.exchange_client.get_active_orders(self.contract_id)
        for order in current_orders:
            try:
                await self.exchange_client.cancel_order(order.order_id)
            except Exception as e:
                self.logger.warning(f"取消订单失败 {order.order_id}: {e}")

    async def check_and_close_exists_positions(self):
        real_position, _ = self.exchange_client.get_account_positions()
        self.real_q = float(real_position)

        self.logger.info(f'get account position, real q: {self.real_q}')
        if abs(self.real_q) >= self.min_quantity:
            self.logger.info(f'start close exists positions: {self.real_q}')
            await self.exchange_client.close_position_with_market_order(
                self.contract_id,
                self.real_q)

    async def run(self):
        """优化版主循环"""
        self.contract_id, self.tick_size, self.min_quantity = await self.exchange_client.update_contract_attributes()

        await self.cancel_all_orders()
        await self.check_and_close_exists_positions()

        t = 5 * 60
        execute_cnt = 0

        while True:
            try:
                ticker_df = self.generate_ticker_tech_index()
                if ticker_df.shape[0] <= 0:
                    logger.warning(f'获取数据失败')
                    await asyncio.sleep(t)

                should_long = True if ticker_df['avg_ma_5'].iloc[-1] > ticker_df['avg_ma_144'].iloc[-1] else False

                if should_long:
                    logger.info(f'============ 准备开始做多！ ============')
                else:
                    logger.info(f'============ 准备开始做空！ ============')

                real_position, _ = self.exchange_client.get_account_positions()
                self.real_q = float(real_position)

                self.logger.info(f'get account position, real q: {self.real_q}')

                if should_long and self.real_q > self.min_quantity:
                    self.logger.info(f'exists long positions: {self.real_q}, skip it.')
                    await asyncio.sleep(t)
                    continue
                elif not should_long and self.real_q < -self.min_quantity:
                    self.logger.info(f'exists short positions: {self.real_q}, skip it.')
                    await asyncio.sleep(t)
                    continue

                await self.check_and_close_exists_positions()

                await asyncio.sleep(1)

                await self.update_order_book_avg_price()

                if self.last_avg_price is None:
                    await asyncio.sleep(t)
                    execute_cnt += 0
                    self.logger.warning(f'last avg price is invalid, skip this time.')
                    continue

                quantity = self.base_order_size_usd / self.last_avg_price
                if should_long:
                    # long
                    order_id = await self.exchange_client.place_buy_market_order(self.contract_id, quantity)
                else:
                    order_id = await self.exchange_client.place_sell_market_order(self.contract_id, quantity)

                self.logger.info(
                    f'contract id: {self.contract_id}, '
                    f'operate direction: {should_long}, '
                    f'quantity: {quantity}, '
                    f'order id: {order_id}')

                await asyncio.sleep(t)

            except Exception as e:
                self.logger.error(f"主循环错误: {e}")
                await asyncio.sleep(t)


if __name__ == "__main__":
    # 创建 ArgumentParser 对象
    parser = argparse.ArgumentParser(
        description="Backpack MA trend follow strategy.",
        epilog="example: python3 bp_MA_trend_strategy.py --ticker ETH "
               "--market-type PERP --key xxx --secret xxx --order-size 50"
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

    # 解析参数
    args = parser.parse_args()

    # 打印解析结果
    logger.info("args:")
    for arg_name, arg_value in vars(args).items():
        logger.info(f"  {arg_name}: {arg_value}")

    _ticker = args.ticker
    _market_type = args.market_type
    _key = args.key
    _secret = args.secret
    _size_usd = float(args.order_size)

    logger.info(
        f'finished init bp market config, '
        f'ticker: {_ticker}, '
        f'market type: {_market_type}, '
        f'public key: {_key}, '
        f'public secret: {_secret}, '
        f'base order size usd: {_size_usd}')

    _config = TradingConfig(
        ticker=_ticker,
        market_type=_market_type,
        public_key=_key,
        secret_key=_secret
    )

    mm = ProfessionalMATrendFollowStrategy(config=_config, base_order_size_usd=_size_usd)
    asyncio.run(mm.run())
