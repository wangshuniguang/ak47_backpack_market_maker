#!/usr/bin/env python3

import argparse
import asyncio
import random
from decimal import Decimal

from config.config import *
from exchanges.backpack_client import BackpackClient
from helpers.logger import setup_logger
from model.trading_config import TradingConfig

logger = setup_logger('market_taker', 'market_taker')


class ProfessionalMarketTaker:

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
        self.logger.info(f"专业Market Taker初始化完成")

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

    async def run(self):
        """优化版主循环"""
        self.contract_id, self.tick_size, self.min_quantity = await self.exchange_client.update_contract_attributes()

        recent_returns = []
        t = 5  # 30ms循环，避免过频
        execute_cnt = 0

        while True:
            try:
                if execute_cnt % 10000 == 0 or self.last_avg_price is None:
                    await self.update_order_book_avg_price()

                real_position, _ = self.exchange_client.get_account_positions()
                self.real_q = float(real_position)

                self.logger.info(f'get account position, real q: {self.real_q}')

                if abs(self.real_q) >= self.min_quantity:
                    order_id = await self.exchange_client.close_position_with_market_order(self.contract_id,
                                                                                           self.real_q)

                    self.logger.info(
                        f'close current position, contract id: {self.contract_id}, '
                        f'quantity: {abs(self.real_q)}, '
                        f'order id: {order_id}')

                else:
                    if self.last_avg_price is None:
                        await asyncio.sleep(t)
                        execute_cnt += 0
                        self.logger.warning(f'last avg price is invalid, skip this time.')
                        continue

                    quantity = self.base_order_size_usd / self.last_avg_price
                    result = random.choice([0, 1])
                    if result == 0:
                        # long
                        order_id = await self.exchange_client.place_buy_market_order(self.contract_id, quantity)
                    else:
                        order_id = await self.exchange_client.place_sell_market_order(self.contract_id, quantity)

                    self.logger.info(
                        f'contract id: {self.contract_id}, '
                        f'choice: {result}, '
                        f'quantity: {quantity}, order id: {order_id}')

                await asyncio.sleep(random.randint(5, 10))
                execute_cnt += 0

            except Exception as e:
                self.logger.error(f"主循环错误: {e}")
                await asyncio.sleep(1.0)  # 错误时暂停1秒


if __name__ == "__main__":
    # 创建 ArgumentParser 对象
    parser = argparse.ArgumentParser(
        description="Backpack all taker strategy",
        epilog="example: python3 bp_market_taker.py --ticker ETH --market-type PERP --key xxx --secret xxx --order-size 50"
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
        f'finished init bp market taker config, '
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

    mm = ProfessionalMarketTaker(config=_config, base_order_size_usd=_size_usd)
    asyncio.run(mm.run())
