# 平台接入契约v1

独立基金项目通过`platform-snapshot.json`向puresaber或其他平台提供只读研究结果。本次不改动平台现有仓库，也不共用其依赖环境、数据库或账户凭据。

## Python接口

```python
from quant_fund.data import Dataset
from quant_fund.engine import BacktestConfig, run_backtest
from quant_fund.integration import platform_snapshot

data = Dataset.load("data/demo")
result = run_backtest(data, BacktestConfig())
payload = platform_snapshot(result, data)
```

`export_run`自动保存同一快照；无需运行HTTP服务。平台未来适配器先校验schema，再使用输入摘要、配置和时点进行幂等导入。建议将完整研究运行的`manifest.json`摘要作为运行标识，避免不同配置覆盖彼此。

|字段|内容|
|---|---|
|schema|固定为`quant-fund.portfolio-snapshot@1`|
|mode|固定为`research_only`|
|as_of|模拟账本期末日期|
|currency|CNY|
|dataset|classification与输入sha256|
|account|cash、frozen_cash、receivables、holdings_value、total_value、nav、pending_orders|
|config|完整回测配置|
|holdings|基金代码、份额、冻结份额、已知估值、金额、权重、滞后天数|
|orders|模拟指令、申请/确认/到账日期、金额/份额、状态|
|targets|每次成功配置的目标权重，含历史日期|
|risk|权重、波动贡献、风险占比|
|alerts|级别、基金代码、提示文本|
|liquidity|已确认应收、待确认赎回、按当前估值测算的可赎回日历|
|rebalance_review|最近一次成功配置目标、当前差额、下一开放日、可解锁份额和复核状态|

表格日期序列化为ISO字符串；缺失值为JSON的`null`，空表为`[]`。金额使用CNY元，份额为基金份额，权重与费率为小数。`risk_share`为Euler贡献比例，可为负值，不等于资金权重。

`rebalance_review`沿用最近一次成功决策的目标，`target_date`可能早于`as_of`。增配差额未联合分配可用现金，不含预计成交费，因此不能直接映射为交易指令。真实账户接入需要独立的持仓与份额批次输入、条款版本、交易确认及对账流程。

对接验收：平台收到的资产分项之和应等于total_value；`mode`不能被忽略；每条目标保留时点；不能把预计流动性金额展示为已可用现金。接入后再考虑封装REST API和调度，不在首版引入第二个运行服务。

## Exploratory standard/v2 export

A clean Git checkout additionally publishes a `standard/v2` research profile through the pinned quant-lab adapter. It is always `investable=false` and `rankable=false`. A dirty or unavailable checkout retains the original report without claiming a clean code revision. Dataset identities use content hashes, and date-only NAV observations are stamped at the end of their UTC day. This profile does not certify a historical universe or replace the original accounting evidence.
