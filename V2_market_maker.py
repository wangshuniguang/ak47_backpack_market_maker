#!/usr/bin/env python3

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Tuple, Optional, Dict
from config.config import *

import numpy as np
import argparse

from exchanges.backpack_client import BackpackClient
from helpers.logger import setup_logger
from model.trading_config import TradingConfig

logger = setup_logger('V3_market_maker', 'market_maker')


class MarketRegime(Enum):
    NORMAL = "normal"
    HIGH_VOL = "high_volatility"
    LOW_VOL = "low_volatility"
    STRESS = "stress"


@dataclass
class RiskParameters:
    # 根据资金规模调整
    Q_max: float = 0.5
    risk_threshold: float = 0.3
    base_order_size_usd: float = 100

    # 以下参数不需要调整

    gamma: float = 0.10
    kappa: float = 1.8
    sigma: float = 0.30
    phi: float = 0.005
    rebate_rate: float = 1 / 10000  # 0.3 bps
    spread_multiplier: float = 1.5


class OrderManager:
    """专业订单管理器 - 避免闪烁报价"""

    def __init__(self, exchange_client, contract_id, logger):
        self.exchange_client = exchange_client
        self.contract_id = ''
        self.logger = logger
        self.active_orders = {}  # order_id -> order_info
        self.order_history = deque(maxlen=1000)
        self.last_order_update = 0
        self.update_interval = 0.1  # 订单更新间隔(秒)

    def update_contract_id(self, contract_id):
        self.contract_id = contract_id

    async def smart_order_update(
            self, desired_bid: Optional[float], desired_ask: Optional[float],
            size_sol: float) -> bool:
        """智能订单更新 - 只在必要时修改订单"""
        current_time = time.time()

        self.logger.info(
            f'current_time: {current_time}, '
            f'smart order update, '
            f'desired_bid: {desired_bid}, '
            f'desired_ask: {desired_ask}, '
            f'size_sol: {size_sol}')

        # 控制更新频率
        if current_time - self.last_order_update < self.update_interval:
            self.logger.info(
                f'smart order update, current time: {current_time}, last order update: {self.last_order_update}, '
                f'update interval: {self.update_interval}')
            return False

        try:
            # 获取当前活跃订单
            current_orders = self.exchange_client.get_active_orders(self.contract_id)
            for order in current_orders:
                try:
                    await self.exchange_client.cancel_order(order.order_id)
                except Exception as e:
                    self.logger.warning(f"取消订单失败 {order.order_id}: {e}")

            orders_to_place = []
            if desired_bid:
                orders_to_place.append(('buy', desired_bid, size_sol))
            if desired_ask:
                orders_to_place.append(('sell', desired_ask, size_sol))

            self.logger.info(f'order to place: {orders_to_place}')
            for side, price, size in orders_to_place:
                try:
                    if side == 'buy':
                        order_id = await self.exchange_client.place_buy_limit_order(
                            self.contract_id, price, size)
                    else:
                        order_id = await self.exchange_client.place_sell_limit_order(
                            self.contract_id, price, size)

                    if order_id is not None:
                        self.active_orders[order_id] = {
                            'side': side,
                            'price': price,
                            'size': size,
                            'desired_price': price,
                            'create_time': current_time
                        }

                        self.logger.info(
                            f'new place order, order id: {order_id}, '
                            f'side: {side}, price: {price}, '
                            f'size: {side}, desired price: {price}, create time: {current_time}')
                    else:
                        self.logger.warning(f'failed to place order')
                except Exception as e:
                    self.logger.error(f"下单失败 {side}@{price}: {e}")

            self.last_order_update = current_time
            return True

        except Exception as e:
            self.logger.error(f"智能订单更新失败: {e}")
            return False


class ProfessionalMarketMaker:
    """
    专业级库存惩罚单边做市商 (优化版本)
    核心改进: 真实头寸同步 + 智能订单管理 + 增强市场检测
    """

    def __init__(
            self,
            risk_params: Optional[RiskParameters] = None,
            config: TradingConfig = None):

        self.risk_params = risk_params or RiskParameters()
        self.prev_ema_s = 0.0

        # 状态变量
        self.q = 0.0  # 本地头寸
        self.real_q = 0.0  # 真实头寸
        self.t = 0.0
        self.T = 1.0
        self.position_start_time = 0.0
        self.market_regime = MarketRegime.NORMAL
        self.realized_vol = self.risk_params.sigma
        self.current_spread = 0.01
        self.liquidity_ratio = 1.0  # 流动性指标

        # 动态参数
        self.dynamic_spread_multiplier = 2.0
        self.dynamic_order_multiplier = 1.0
        self.dynamic_gamma = self.risk_params.gamma
        self.dynamic_Q_max = self.risk_params.Q_max

        # 价格数据
        self.mid_price = 0.0
        self.price_log = deque(maxlen=1000)
        self.volume_data = deque(maxlen=100)  # 交易量数据

        # 核心metrics
        self.metrics = {
            'total_trades': 0, 'total_rebate': 0.0, 'total_penalty': 0.0,
            'total_slippage': 0.0, 'total_funding': 0.0, 'hedge_count': 0,
            'max_inventory': 0.0, 'min_inventory': 0.0, 'volume_traded': 0.0,
            'position_sync_errors': 0, 'order_update_count': 0
        }

        self.config = config
        self.contract_id = ''
        self.tick_size = Decimal(0)
        self.min_quantity = Decimal(0)

        # 创建交易所客户端
        try:
            # public_key, secret_key, ticker
            self.exchange_client = BackpackClient(
                config.public_key,
                config.secret_key,
                config.ticker,
                config.market_type
            )
            self.order_manager = OrderManager(self.exchange_client, config, logger)
        except ValueError as e:
            raise ValueError(f"创建交易所客户端失败: {e}")

        self.logger = logger
        self.last_position_sync = 0
        self.position_sync_interval = 1  # 头寸同步间隔(秒)

        self.logger.info(f"专业MM优化版初始化完成")

    async def sync_real_position(self) -> bool:
        """真实头寸同步 - 核心风控基础"""
        current_time = time.time()
        if current_time - self.last_position_sync < self.position_sync_interval:
            return True

        try:
            real_position, _ = self.exchange_client.get_account_positions()
            self.real_q = float(real_position)
            self.q = self.real_q
            self.last_position_sync = current_time
        except Exception as e:
            self.logger.error(f"头寸同步失败: {e}")

        return True

    def enhanced_market_regime_detection(self, order_book: Dict) -> bool:
        """增强的市场状态检测 - 结合波动率和流动性"""
        if len(self.price_log) < 10:
            return False

        try:
            # 1. 计算实时波动率 (短窗口)
            recent_prices = list(self.price_log)[-10:]  # 最近10个价格
            returns = [np.log(recent_prices[i] / recent_prices[i - 1])
                       for i in range(1, len(recent_prices))]

            if len(returns) < 5:
                return False

            current_vol = np.std(returns) * np.sqrt(252 * 86400)  # 年化波动率
            self.realized_vol = 0.7 * self.realized_vol + 0.3 * current_vol

            # 2. 计算流动性指标
            if order_book and 'bids' in order_book and 'asks' in order_book:
                bid_depth = sum([float(size) for price, size in order_book['bids'][:3]])  # 前3档买单
                ask_depth = sum([float(size) for price, size in order_book['asks'][:3]])  # 前3档卖单

                if ask_depth > 0:
                    self.liquidity_ratio = bid_depth / ask_depth
                else:
                    self.liquidity_ratio = 1.0

            pressure = abs(np.log(self.liquidity_ratio)) * 0.5 if self.liquidity_ratio > 0 else 0.5  # fix: log formula

            # 3. 综合判断市场状态
            vol_ratio = self.realized_vol / self.risk_params.sigma
            # composite_signal = 0.7 * vol_ratio + 0.3 * liquidity_pressure
            composite_signal = 0.7 * vol_ratio + 0.3 * pressure

            new_regime = self.market_regime
            if composite_signal > 2.0:
                new_regime = MarketRegime.STRESS
            elif composite_signal > 1.5:
                new_regime = MarketRegime.HIGH_VOL
            elif composite_signal < 0.7:
                new_regime = MarketRegime.LOW_VOL
            else:
                new_regime = MarketRegime.NORMAL

            if new_regime != self.market_regime:
                self.logger.info(
                    f"市场状态变化: {self.market_regime.value} -> {new_regime.value} "
                    f"(波动率: {vol_ratio:.2f}, 流动性压力: {pressure:.2f}, "
                    f"流动性比例: {self.liquidity_ratio:.2f})")
                self.market_regime = new_regime
                self._adjust_parameters_by_regime()

            return True

        except Exception as e:
            self.logger.error(f"市场状态检测失败: {e}")
            return False

    def _adjust_parameters_by_regime(self):
        """基于市场状态的参数调整"""
        multipliers = {
            MarketRegime.NORMAL: {'gamma': 1.0, 'Q_max': 2.0, 'order_size': 1.0, 'spread': 1.0},
            MarketRegime.HIGH_VOL: {'gamma': 1.4, 'Q_max': 1.2, 'order_size': 0.5, 'spread': 1.8},
            MarketRegime.LOW_VOL: {'gamma': 0.7, 'Q_max': 2.6, 'order_size': 1.4, 'spread': 0.7},
            MarketRegime.STRESS: {'gamma': 2.0, 'Q_max': 0.8, 'order_size': 0.3, 'spread': 2.5}
        }

        mult = multipliers[self.market_regime]
        self.dynamic_gamma = self.risk_params.gamma * mult['gamma']
        self.dynamic_Q_max = self.risk_params.Q_max * mult['Q_max']
        self.dynamic_order_multiplier = mult['order_size']
        self.dynamic_spread_multiplier = mult['spread']

    def calculate_competitive_spread(self, s: float, order_book: Dict) -> float:
        """竞争性价差计算"""
        # 基础风险调整价差
        time_left = max(0.001, self.T - self.t)
        vol_component = self.dynamic_gamma * (self.realized_vol ** 2) * time_left
        inventory_pressure = 0.3 * (abs(self.q) / self.dynamic_Q_max) * self.current_spread
        market_spread = self.current_spread * self.dynamic_spread_multiplier
        risk_spread = vol_component + inventory_pressure + market_spread

        self.logger.info(
            f'calculate spread, time left: {time_left}, vol component: {vol_component}, '
            f'inventory_pressure: {inventory_pressure}, market spread: {market_spread}, '
            f'risk spread: {risk_spread}')
        try:

            top_bid = float(order_book['bids'][-1][0])
            top_ask = float(order_book['asks'][0][0])
            competitor_spread = top_ask - top_bid

            self.logger.info(
                f'竞争价差 - 最佳买价: {top_bid:.4f}, 最佳卖价: {top_ask:.4f}, '
                f'市场价差: {competitor_spread:.4f}, 风险价差: {risk_spread:.4f}')

        except Exception as e:
            self.logger.error(f"价差计算异常: {e}")

        return risk_spread

    def intelligent_side_selection(self, order_book: Dict) -> str:
        """智能订单方向选择 - 基于订单簿压力"""
        if not order_book or 'bids' not in order_book or 'asks' not in order_book:
            return 'random'

        try:
            # 计算前3档买卖压力
            bid_pressure = sum([size * (1 - i * 0.1) for i, (price, size) in enumerate(order_book['bids'][:3])])
            ask_pressure = sum([size * (1 - i * 0.1) for i, (price, size) in enumerate(order_book['asks'][:3])])

            # 考虑库存偏斜
            inventory_bias = -0.1 * (self.q / self.dynamic_Q_max)  # 库存偏斜系数

            # 综合压力指标
            total_pressure = (ask_pressure - bid_pressure) + inventory_bias

            if total_pressure > 0.1:
                return 'bid'  # 卖压较大，挂买单
            elif total_pressure < -0.1:
                return 'ask'  # 买压较大，挂卖单
            else:
                return 'balanced'

        except Exception as e:
            self.logger.warning(f"方向选择计算失败: {e}")
            return 'random'

    def calculate_dynamic_order_size(self, s: float) -> float:
        """动态订单大小计算"""
        base_size_usd = self.risk_params.base_order_size_usd * self.dynamic_order_multiplier

        # 库存风险惩罚
        inventory_risk_penalty = 1.0 - 0.4 * (abs(self.q) / self.dynamic_Q_max)

        # 波动率调整
        vol_adjustment = 1.0 / (1.0 + 0.5 * (self.realized_vol / self.risk_params.sigma - 1.0))

        # 流动性调整
        liquidity_adjustment = min(1.5, max(0.3, self.liquidity_ratio))

        target_size = base_size_usd * inventory_risk_penalty * vol_adjustment * liquidity_adjustment
        order_size_usd = target_size

        self.logger.info(
            f'calculate dynamic order size, s: {s}, '
            f'base size usd: {base_size_usd}, '
            f'inventory risk penalty: {inventory_risk_penalty}, '
            f'vol adjustment: {vol_adjustment}, '
            f'liquidity adjustment: {liquidity_adjustment}, '
            f'target size: {target_size}, '
            f'order size usd: {order_size_usd}')

        return order_size_usd

    def generate_intelligent_quotes(self, s: float, order_book: Dict) -> Tuple[Optional[float], Optional[float]]:
        """完全重写的智能报价逻辑"""
        try:
            bid_price = None
            ask_price = None
            inventory_ratio = abs(self.q) / self.dynamic_Q_max

            # 安全获取订单簿数据
            if not order_book or 'bids' not in order_book or 'asks' not in order_book:
                self.logger.warning("订单簿数据不完整，返回None报价")
                return None, None

            # 计算竞争性价差

            try:
                spread = self.calculate_competitive_spread(s, order_book)
                half_spread = spread / 2
            except Exception as e:
                self.logger.warning(f"价差计算失败: {e}")
                spread = s * self.risk_params.rebate_rate  # 0.2%默认价差
                half_spread = spread / 2

            try:
                min_ask = float(order_book['asks'][0][0])
                max_bid = float(order_book['bids'][-1][0])
            except (IndexError, ValueError, TypeError) as e:
                self.logger.error(f"解析订单簿价格失败: {e}")
                return None, None

            self.logger.info(
                f'报价计算基础 - 中间价: {s:.4f}, 库存: {self.q:.4f}, '
                f'库存比例: {inventory_ratio:.2f}, 价差: {spread:.4f}, '
                f'最小卖价: {min_ask:.4f}, '
                f'最高买价: {max_bid:.4f}')

            # 基于库存状态的智能报价策略
            if inventory_ratio >= 0.9:  # 极高库存 - 紧急平仓
                if self.q > 0:  # 极高多头 - 紧急卖出
                    # 修复：使用更激进的价格确保成交，但要高于买一价
                    ask_price = max_bid * (1 + self.risk_params.rebate_rate)  # 略高于最佳买价
                    bid_price = None
                    self.logger.warning(f"极高多头库存 - 紧急卖单: {ask_price:.4f}")
                else:  # 极高空头 - 紧急买入
                    bid_price = min_ask * (1 - self.risk_params.rebate_rate)  # 略低于最佳卖价
                    ask_price = None
                    self.logger.warning(f"极高空头库存 - 紧急买单: {bid_price:.4f}")

            elif inventory_ratio >= 0.7:  # 高库存 - 强烈倾斜
                if self.q > 0:  # 高多头 - 积极卖出，保守买入
                    # 卖单：略低于卖一价，优先成交
                    ask_price = s + half_spread * 0.5  # 较近的卖单
                    bid_price = s - half_spread * 1.8  # 较远的买单
                    self.logger.info("高多头库存 - 卖单优先")

                else:  # 高空头 - 积极买入，保守卖出
                    bid_price = s - half_spread * 0.5  # 较近的买单
                    ask_price = s + half_spread * 1.8  # 较远的卖单
                    self.logger.info("高空头库存 - 买单优先")

            elif inventory_ratio >= 0.4:  # 中等库存 - 适度倾斜
                if self.q > 0:  # 中等多头
                    bid_price = s - half_spread * 1.3
                    ask_price = s + half_spread * 0.9
                else:  # 中等空头
                    bid_price = s - half_spread * 0.9
                    ask_price = s + half_spread * 1.3
            else:  # 低库存 - 均衡做市
                bid_price = s - half_spread
                ask_price = s + half_spread

            self.logger.info(
                f'最终报价 - 买价: {bid_price}, '
                f'卖价: {ask_price}')

            return bid_price, ask_price

        except Exception as e:
            self.logger.error(f"报价生成失败: {e}")
            return None, None

    def validate_and_adjust_prices(self, bid_price: Optional[float], ask_price: Optional[float],
                                   s: float) -> Tuple[Optional[float], Optional[float]]:
        """价格合理性验证和调整"""
        # 基础价格范围检查
        min_valid_price = s * 0.8  # 最低允许价格
        max_valid_price = s * 1.2  # 最高允许价格

        if bid_price is not None:
            if bid_price >= s:
                self.logger.warning(f"买价{bid_price:.4f}高于中间价{s:.4f}，已调整")
                bid_price = s * 0.999  # 强制低于中间价
            elif bid_price < min_valid_price:
                self.logger.warning(f"买价{bid_price:.4f}过低，已调整到最低价{min_valid_price:.4f}")
                bid_price = min_valid_price

        if ask_price is not None:
            if ask_price <= s:
                self.logger.warning(f"卖价{ask_price:.4f}低于中间价{s:.4f}，已调整")
                ask_price = s * 1.001  # 强制高于中间价
            elif ask_price > max_valid_price:
                self.logger.warning(f"卖价{ask_price:.4f}过高，已调整到最高价{max_valid_price:.4f}")
                ask_price = max_valid_price

        # 买卖价差检查
        if bid_price and ask_price and ask_price <= bid_price:
            self.logger.error(f"买卖价颠倒: 买价{bid_price:.4f} >= 卖价{ask_price:.4f}")
            # 强制保持最小价差
            min_spread = s * 0.001  # 最小0.1%价差
            ask_price = max(ask_price, bid_price + min_spread)

        return bid_price, ask_price

    async def execute_real_hedge(self, s: float):
        """真实对冲执行"""
        if abs(self.real_q) <= self.risk_params.risk_threshold:
            self.logger.info(
                f'跳过当前对冲, 当前仓位=>{abs(self.real_q)}, '
                f'最小需要对冲仓位: {self.risk_params.risk_threshold}')
            return

        try:
            excess_ratio = (abs(self.real_q) - self.risk_params.risk_threshold) / (
                    self.dynamic_Q_max - self.risk_params.risk_threshold)
            hedge_ratio = min(1.0, 0.3 + 0.7 * excess_ratio)
            hedge_size_sol = abs(self.real_q) * hedge_ratio

            hedge_penalty = -self.risk_params.phi * (self.real_q ** 2) * hedge_ratio

            self.logger.info(
                f'execute real hedge, excess ratio: {excess_ratio}, hedge ratio: {hedge_ratio}, '
                f'hedge size sol: {hedge_size_sol}, '
                f'hedge penalty: {hedge_penalty}, '
                f'real q: {self.real_q}')

            # 执行真实对冲订单
            if self.real_q > 0:
                # 卖出对冲
                order_id = await self.exchange_client.close_position_with_market_order(
                    self.contract_id, hedge_size_sol)  # 略低于市价
            else:
                # 买入对冲
                order_id = await self.exchange_client.close_position_with_market_order(
                    self.contract_id, -hedge_size_sol)  # 略高于市价

            self.logger.info(
                f'Market Order ID: {order_id}, '
                f'contract id: {self.contract_id}, '
                f'hedge size sol: {hedge_size_sol}')

        except Exception as e:
            self.logger.error(f"对冲执行失败: {e}")

    async def step(self, s: float, order_book: Dict, dt: float = 0.001):
        """优化版核心step"""
        self.logger.info('----------------------------------------------------')

        try:
            # 1. 头寸同步 (风控基础)
            if not await self.sync_real_position():
                return  # 头寸同步失败，暂停交易

            # 2. 市场状态检测
            self.mid_price = s
            self.enhanced_market_regime_detection(order_book)

            # 3. 风险对冲
            await self.execute_real_hedge(s)

            # 4. 智能报价
            bid_price, ask_price = self.generate_intelligent_quotes(s, order_book)
            order_size_usd = self.calculate_dynamic_order_size(s)
            size_sol = max(order_size_usd / s, float(self.min_quantity))

            self.logger.info(
                f"Step - 价格: {s}, 头寸: {self.real_q}, "
                f"买价: {bid_price if bid_price else 'None'}, "
                f"卖价: {ask_price if ask_price else 'None'}, "
                f"大小: {size_sol}")

            # 5. 智能订单管理
            if bid_price or ask_price:
                await self.order_manager.smart_order_update(bid_price, ask_price, size_sol)

            self.t += dt

        except Exception as e:
            self.logger.error(f"Step error: {e}")

    async def run(self):
        """优化版主循环"""
        self.contract_id, self.tick_size, self.min_quantity = await self.exchange_client.update_contract_attributes()
        self.order_manager.update_contract_id(self.contract_id)

        recent_returns = []
        t = 1.0  # 30ms循环，避免过频

        reinit_q_max = False

        while True:
            try:
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
                    await asyncio.sleep(t)
                    continue

                # 构建订单簿
                min_ask = min(asks)
                max_bid = max(bids)
                s = (max_bid + min_ask) / 2.0
                self.current_spread = min_ask - max_bid

                if not reinit_q_max:
                    if self.risk_params.base_order_size_usd <= 10:
                        self.risk_params.base_order_size_usd = 50

                    self.risk_params.risk_threshold = self.risk_params.base_order_size_usd * 5 / s
                    self.risk_params.Q_max = self.risk_params.risk_threshold * 2

                    self.dynamic_gamma = self.risk_params.gamma
                    self.dynamic_Q_max = self.risk_params.Q_max

                    reinit_q_max = True

                    self.logger.info(f'重新调整参数，当前参数如下。 Risk params: {self.risk_params}')

                # 更新价格序列
                if self.price_log:
                    recent_returns.append(np.log(s / self.price_log[-1]))
                    if len(recent_returns) > 20:
                        recent_returns = recent_returns[-20:]

                self.price_log.append(s)

                # 执行核心逻辑
                await self.step(s, order_book, dt=t)

                await asyncio.sleep(t)

            except Exception as e:
                self.logger.error(f"主循环错误: {e}")
                await asyncio.sleep(1.0)  # 错误时暂停1秒


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backpack market maker strategy",
        epilog="example: python3 market_maker.py --ticker ETH --market-type PERP --key xxx --secret xxx --order-size 50"
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
    logger.info("args:")
    for arg_name, arg_value in vars(args).items():
        logger.info(f"  {arg_name}: {arg_value}")

    _ticker = args.ticker
    _market_type = args.market_type
    _public_key = args.key
    _secret_key = args.secret
    _base_order_size_usd = float(args.order_size)

    logger.info(
        f'finished init market maker config, '
        f'ticker: {_ticker}, '
        f'market type: {_market_type}, '
        f'public key: {_public_key}, '
        f'public secret: {_secret_key}, '
        f'base order size usd: {_base_order_size_usd}')

    # backpack_Q_max, backpack_risk_threshold 这两个参数会在程序中重新调整，忽略它们。
    params = RiskParameters(
        Q_max=backpack_Q_max,
        risk_threshold=backpack_risk_threshold,
        base_order_size_usd=_base_order_size_usd)

    _config = TradingConfig(
        ticker=_ticker,
        market_type=_market_type,
        public_key=_public_key,
        secret_key=_secret_key
    )

    mm = ProfessionalMarketMaker(risk_params=params, config=_config)
    asyncio.run(mm.run())
