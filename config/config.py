#!/usr/bin/env python

# 标的选择

backpack_ticker = 'ETH'
backpack_public_key = '<替换成自己的Key>'
backpack_secret_key = '<替换自己的Secret>'

# ************** 对于对冲模式才需要填写 **************
backpack_hedge_public_key = '<替换成Backpack对冲账号的Key>'
backpack_hedge_secret_key = '<替换Backpack对冲账号的Secret>'

# 默认是合约，如果想支持现货，需要改成是：SPOT
backpack_market_type = 'PERP'

# 报价大小建议

backpack_base_order_size_usd = 100
backpack_risk_threshold = 0.5
backpack_Q_max = 1.0
