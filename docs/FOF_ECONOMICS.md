# FOF 自身费用、现金利息与高水位

此功能直接参与原生 `Ledger` 和 `run` 结果，`verify-run` 根据冻结输入与指令重放费用。底层基金净值已包含的管理费用不会再次扣除。配置中的费率只代表外层 FOF 的声明研究假设。

## 运行

```powershell
python -m quant_fund.cli demo --out data/fof-demo
python -m quant_fund.cli run --dataset data/fof-demo --config configs/fof-economics.example.json --out artifacts/fof-economics
python -m quant_fund.cli verify-run artifacts/fof-economics
```

配置的 `fof_policy` 缺省为空，维持旧账本数值和 NAV CSV 字段。启用后增加 `fof_economics.csv`、报告章节、平台快照 `fof_economics` 与 NAV 资产负债分项。配置进入 manifest；输入、代码和输出沿用现有哈希归档规则。

## 精确口径

- 仅支持固定初始投资者资本的 CNY 账户。买卖底层基金属于资产转换；不支持外部投资者申购赎回、份额均衡法、系列份额或税收。不得将外部现金流直接加入 `cash` 后继续使用高水位模型。
- `known_at` 是研究声明的政策获知日，必须不晚于首个账本日；并不认证真实合同。政策在一个 run 中冻结，不支持期间改约。所有日期为明确 ISO 日期，支付与结晶日期必须存在于本次显式账户日历。
- `management_rate`、`custody_rate`、`cash_interest_rate` 是年化小数，日计数仅支持 `ACT/365F`。两个账户日之间按实际日历天数累积，周末不丢失，也不插值基金净值。
- 每个收盘先处理原有确认、分红和到账；然后计息、计费、结晶、支付；之后才产生当日新投资决策。首日不补计成立前费用或利息。
- 管理与托管费用＝**上一账户日收盘扣除全部费用负债后的净资产 × 年化费率 × 实际天数 / 365**。
- 利息＝**上一账户日实际现金减费用负债后的非负可用余额 × 年化利率 × 实际天数 / 365**。冻结申购款和应收款不计息；负利率允许，但现金不足支付负利息时拒绝整日变更。
- 业绩报酬是当前扣除固定应付费用后资产高于上次高水位部分的 `performance_rate`。结晶前每天重新估算准备金；回撤允许准备金转回。没有收益不计提，没有达到旧高水位不重置基准。
- `crystallization_dates` 将当日准备金转为固定应付款，HWM 更新为当前**扣除业绩费后的净资产**与旧 HWM 的较大值。即使尚未支付也不得重复收费。没有指定结晶日，期末准备金继续列作负债，不假设自动结晶。
- 固定费用按 `payment_frequency=daily` 每日支付，或按 `explicit` 的 `payment_dates` 支付。仅支付实际现金允许的部分，不动冻结现金或未到账赎回款；未付部分保留负债，未来支付日再支付。未结晶业绩准备金不支付，但不可用于新申购。
- 恒等式：`total_value = cash + frozen_cash + receivables + holdings_value - fof_fee_liability`。结晶及费用支付只是负债转移/结算，不重复减少净资产。现金利息和计提费用才改变净资产。

沿用原生基金账本浮点金额和既有核对容差，不宣称已升级为法律会计的逐分四舍五入账簿。净资产耗尽时拒绝，不生成负净值或隐性融资。

## 独立验证

`tests/test_fof_economics.py` 覆盖 ACT/365F 跨周末手算、全仓时费用应付、赎回到账后的支付、业绩准备金转回、结晶后再创新高、明确支付日、未来政策拒绝与事务回滚，以及篡改费用 CSV 后重算文件哈希仍不能通过账本重放。100→110→95→120 的 20% 业绩费轨迹应依次准备 0、2、0、4，最后结晶净资产与高水位为 116。
