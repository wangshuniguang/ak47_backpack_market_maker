### Brief Introduction to the AK47 Market Making Strategy for Backpack

The AK47 Market Making Strategy is a market making strategy designed for the Backpack exchange, closely adhering to genuine market making standards. It features efficient inventory management capabilities, an intelligent quoting module (which optimizes quoting logic based on market conditions and order books), and hedging within the same exchange.

This enables quoting via **limit orders** on the Backpack exchange with extremely **low-cost slippage**. The project has been tested with real trading volumes of 300-400 million and, even under abnormal market conditions for ETH, achieved an average slippage of nearly 1U per 10,000 trading volume. For the PAXG gold instrument, the average slippage was even lower, close to 0.5 per 10,000.

### Usage Instructions

#### Basic Environment Setup

Deploy a Python 3 and pip3 environment. Python 3.9 to 3.11 is recommended.

Use the command `pip3 install -r requirements.txt` to install dependencies. If you encounter a missing bpx package, you can try installing it separately using the following command:
`pip3 install bpx-py==2.0.11 --ignore-installed --break-system-packages`

#### Configuration Adjustments

You need to replace the configuration file `config.py` in the config directory. The following parameters are currently available:

```python
backpack_ticker = 'BNB'
backpack_public_key = '<Replace with your own Key>'
backpack_secret_key = '<Replace with your own Secret>'
backpack_base_order_size_usd = 100
backpack_risk_threshold = 0.5
backpack_Q_max = 1.0
```

#### Parameter Explanation & Suggested Order Size

- `backpack_base_order_size_usd = 100`
- `backpack_risk_threshold = 0.5`
- `backpack_Q_max = 1.0`

`backpack_public_key` and `backpack_secret_key` need to be replaced with your own API keys. You can view them on the Backpack website: https://backpack.exchange/portfolio/settings/api-keys

`backpack_ticker`: The trading pair you wish to market make. The default is BNB, but it can be changed to others such as ETH or PAXG, depending on your preference.

`backpack_base_order_size_usd`: The minimum order size for quoting, calculated in USD. Based on our experience, if you aim to achieve a daily trading volume of 1 million with minimal slippage, setting this value between 100 and 200 is sufficient.

`backpack_risk_threshold`: The inventory alert threshold, measured in contract units. For example, 0.5 corresponds to 0.5 BNB. This value is empirically calculated as (Initial account balance / Contract price) * (0.3 ~ 0.5). For instance, if your account has 1000U and the current price of BNB is 1000U, a value of 0.5 would be reasonable.

`backpack_Q_max`: The maximum inventory. It is recommended to set this to `backpack_risk_threshold * 2`, as this has proven effective in practice. Feel free to experiment with other parameters.

#### Execution Method

After adjusting the parameters, run the program using `python3 market_maker.py`. The program quotes every 0.03 seconds by default, which has been found to be optimal in practice and does not require adjustment.

### Issue Feedback

Feel free to follow my Twitter: https://x.com/dog_gold70695
