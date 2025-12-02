### 背包AK47做市策略简单介绍
AK47做市策略是针对Backpack交易所的做市策略，非常接近于真正做市标准。内置有高效库存管理能力、智能报价模块（能够根据行情、订单簿来优化报价逻辑）、同交易所对冲。
使得能够在背包交易所以极其**低成本的磨损**来通过**限价单**报价。该项目经受了3~4亿交易量的真实交易量的检验，并且针对ETH在异常行情下都能做到平均磨损万交易量接近1U，
对于PAXG 黄金品种甚至都平均接近万0.5。

### 使用方法
#### 基础环境准备
部署python3和pip3环境，python3建议python3.9~python3.11版本

使用pip3 install -r requirements.txt 
命令安装依赖环境，如果遇到bpx包找不到，可以尝试执行如下命令独立安装：
pip3 install bpx-py==2.0.11 --ignore-installed --break-system-packages

#### 配置调整
需要替换config目录下的config.py配置文件。目前有如下几个参数：

backpack_ticker = 'BNB'

backpack_public_key = '<替换成自己的Key>'

backpack_secret_key = '<替换自己的Secret>'

backpack_base_order_size_usd = 100

backpack_risk_threshold = 0.5

backpack_Q_max = 1.0

#### 参数介绍&报价大小建议

backpack_base_order_size_usd = 100
backpack_risk_threshold = 0.5
backpack_Q_max = 1.0

backpack_public_key和backpack_secret_key需要替换成自己的AK，可以点击背包网页查看：https://backpack.exchange/portfolio/settings/api-keys

backpack_ticker：想要做市的标的，默认是BNB，可以改成是自己的，不如ETH、PAXG等，具体看自己喜好；

backpack_base_order_size_usd： 最小的报价仓位，这个是按照U来计算的。根据我们的经验，如果想要一天跑100万交易量，磨损又尽可能小，这个值设置成100~200就可以了。

backpack_risk_threshold：预警库存，单位是合约个数。比如0.5对应的是0.5 BNB。这个经验是根据你起始仓位资金 / 合约价格 * （0.3 ~ 0.5）来计算的，比如，你账户有1000U，BNB目前价格
是1000U，那这个值是0.5就比较合理。

backpack_Q_max： 最大库存，最好是backpack_risk_threshold * 2，这个是实践比较好的经验，欢迎大家尝试其他的参数。

#### 运行方式
参数调整完之后，使用python3 market_maker.py执行程序就可以，程序默认是0.03秒报价一次，这个参数也不需要调整，实践中发现是最好的。

### 问题反馈
欢迎关注个人推特：https://x.com/dog_gold70695

