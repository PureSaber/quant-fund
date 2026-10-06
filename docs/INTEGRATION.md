# 平台接入契约v1

独立基金项目通过`platform-snapshot.json`向puresaber或其他平台提供只读研究结果。本次不改动平台现有仓库，也不共用其依赖环境、数据库或账户凭据。

## 低频账本请求与重放核验

`Ledger.submit(..., request_id="业务请求标识")`为可选的重复调用契约：同一标识与同一基金、
方向、金额和份额返回首次订单，即使订单后来已经确认，也不会再次冻结或成交；
同一标识对应不同内容明确失败。省略标识仍表示新订单，不按金额或日期猜测重复。
`orders.csv`及平台快照的订单新增可选`request_id`字段，原有schema保持不变。
该标识保存在内存订单与研究证据中，不代表已提供跨进程的基金账户持久化服务。

提交和日推进共用回滚边界；异常及中断恢复现金、冻结、应收、批次、订单和历史。
运行中的`check()`核对待确认申购冻结金额、待确认赎回逐批冻结份额及有限非负应收款。
赎回应收在到账日历允许且实际确认后到账，未到账款项不能再次申购。

新版`verify-run`使用归档输入和保存的订单逐日重放，不重新运行优化器。
它逐日比较现金、冻结、应收、估值、总资产、净值和订单数量，并比较成交、费用、
订单状态、期末批次及赎回批次引用。缺少或重复日期、非有限金额、即使重新签写文件
摘要的错误经济数据均失败。金额绝对容差为1e-6元，份额1e-8，净值1e-12，
订单编号、订单数量与锁定天数精确比较；基金代码和版本标识按字符串处理。

成功返回`ledger_replay=pass`、`verified_ledger_days`和`verified_ledger_orders`。
旧`research-run@1`仍按原契约验证，返回`ledger_replay=not_available_legacy_schema`，
不会被追认为新版完整重放通过；旧schema@2缺少请求标识时按未标识订单重放。
这些验收使用合成或归档研究数据，不认证真实基金业务。

## 原生只读预检命令

```powershell
python -m quant_fund.cli preflight --dataset data/demo --config config.json
```

配置与`run --config`相同，接受完整BacktestConfig对象或带`config`字段的保存清单。
成功时只在标准输出返回`quant-fund.preflight/v1`JSON，包含`software_preflight=pass`、
`read_only=true`、`investable=false`、数据分类、基金与净值行数、账户日历区间、
输入和配置摘要、规范化配置、`terms_mode`、`historical_terms_pit`及检查边界。失败返回非零退出状态。

预检复用Dataset及正式回测的静态前置校验，检查净值获知时点、条款、日历、
CNY场外基金适用范围、策略名称、初始资金、权重/现金比例和研究区间。
检查前后重新比较输入文件集合、内容摘要及修改时间，期间变化会失败。
它不创建Ledger、不分配权重、不生成订单、不回放申赎、不写数据集或输出目录；
不保证每个决策日共同历史充分或优化器可行。后续运行重读输入，预检不锁定输入。

Studio可通过自己的模板调用该命令，并通过独立Python环境运行本应用。
原生`nav.csv`的`nav`是期初为1的单位净值，金额币种为CNY；界面不能将初始资金
作为该净值列的分母，也不能将数据分类为synthetic的产物显示为真实市场表现。

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
|lots/order_lots|申购批次锁定版本及赎回订单引用的批次份额|
|term_versions|研究配置与申赎订单实际消费的条款版本证据|
|targets|每次成功配置的目标权重，含历史日期|
|risk|权重、波动贡献、风险占比|
|alerts|级别、基金代码、提示文本|
|liquidity|已确认应收、待确认赎回、按当前估值测算的可赎回日历|
|rebalance_review|最近一次成功配置目标、当前差额、下一开放日、可解锁份额和复核状态|

`dataset`新增`terms_mode`和`historical_terms_pit`可选字段；保持`quant-fund.portfolio-snapshot@1`，旧消费者可忽略新增字段。表格日期序列化为ISO字符串；缺失值为JSON的`null`，空表为`[]`。金额使用CNY元，份额为基金份额，权重与费率为小数。`risk_share`为Euler贡献比例，可为负值，不等于资金权重。

原生研究目录升级为`quant-fund.research-run@2`。归档inputs复制Dataset实际加载的全部输入，包括可选日历、披露持仓和历史条款；`verify-run`除文件摘要和资金对账外，还会从归档输入重新选择每笔订单的申请日版本，复核开放/预约条件、确认/到账、确切申请净值、申赎费和Lot锁定证据。即使篡改后重新写入manifest文件摘要，错误版本绑定仍会失败。旧`research-run@1`且只有静态条款时继续按旧输入摘要验证，返回`legacy_static`和零条新绑定复核；旧产物缺失其原运行实际使用的可选输入时不能完整复核。

`rebalance_review`沿用最近一次成功决策的目标，`target_date`可能早于`as_of`。增配差额未联合分配可用现金，不含预计成交费，因此不能直接映射为交易指令。真实账户接入需要独立的持仓与份额批次输入、条款版本、交易确认及对账流程。

对接验收：平台收到的资产分项之和应等于total_value；`mode`不能被忽略；每条目标保留时点；不能把预计流动性金额展示为已可用现金。接入后再考虑封装REST API和调度，不在首版引入第二个运行服务。

## 探索性standard/v2导出

干净Git检出通过固定的quant-lab适配器额外发布`standard/v2`的`research`视图，始终标记`investable=false`和`rankable=false`。检出不干净或Git身份不可用时保留原报告，不声称干净提交身份。数据身份使用内容哈希，只有日期的净值观测标记为对应UTC日末；该视图不认证历史股票池或替代原始会计证据。

使用`quant-lab validate --run-dir <研究目录>`核验后，可扫描到独立实验索引，由Report Hub按索引位置重新验证产物并展示。索引是可重建缓存；来源损坏时应显示不可用，不能使用旧缓存指标。`research`视图中的汇总NAV不能承担完整QExec现金分录、订单和成交链路的精确归因。

v2的`metrics.json`保留原报告的全部指标，并添加数据分类、估值口径和`backtest_stats`展示行。完整估值区间使用首末已知估值及完整路径回撤，年化与Sharpe留空；完整月末收益样本另行展示收益、月末回撤和按12期年化的指标，两行不能混合使用。陈旧估值可能低估风险；没有新增基金基准或将低频净值插值成日收益。`config.json`的`study_config`保留原回测配置。
