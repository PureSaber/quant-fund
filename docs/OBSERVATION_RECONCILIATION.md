# 外部申赎确认与赎回到账对账

`verify-run`核验保存的模型账本。`reconcile-observations`先执行同一核验，再只读比较独立提供的确认单和赎回到账记录；不会更改持仓或现金，也不会发送交易。

```powershell
python -m quant_fund.cli reconcile-observations --run artifacts/my-run --confirmations data/private/confirmations.csv --receipts data/private/receipts.csv
```

结果输出到标准输出；重定向保存时请使用私有目录中的新文件名，不要覆盖输入。真实原始文件、账户信息和对账差异不得提交Git。输入表必须由独立凭证整理，不能从模型输出复制后声称完成真实验收。

## 输入契约

UTF-8 CSV（允许BOM），必须有且仅有下述列；列顺序可变。空表仍需表头。每一行所有字段非空；每张表的`order_id`及`evidence_id`各自唯一。支持一笔订单对应一条完整确认和一条完整赎回收款；拆分确认、分期到账、退款冲销及多币种尚不支持，不能自行合并后声称覆盖这些业务。

确认表表头：

```csv
evidence_id,order_id,fund_id,side,deal_date,confirmed,unit_nav,shares,gross,fee,currency,source_ref
```

到账表表头：

```csv
evidence_id,order_id,fund_id,received,amount,currency,source_ref
```

|字段|含义与要求|
|---|---|
|evidence_id|输入凭证唯一编号；不要放完整银行账号|
|order_id|人工核对后映射到运行中的正整数订单ID，不按金额猜配|
|fund_id / side|准确的基金份额类别代码；BUY或SELL|
|deal_date / confirmed|确认凭证上的交易日期、实际确认日期，YYYY-MM-DD；确认不能早于交易|
|unit_nav / shares|成交单位净值、确认份额，有限正数|
|gross / fee|申购gross为含申购费的申请金额；赎回gross为扣赎回费前金额；fee为该笔交易费用，有限非负数|
|received / amount|银行或销售机构的实际赎回到账日及净收款金额，不能用预计日期和应收款代替|
|currency|当前仅允许CNY|
|source_ref|可定位原始凭证的本地引用或脱敏索引；程序不打开引用，也不认证其真实性|

程序要求完整覆盖运行中的所有已确认交易。赎回预期到账按已核验输入的账户日历、银行用途日历和实际模型确认日计算，不早于计划结算日或确认日。运行截止日尚未到账的订单列入`pending_redemption_order_ids`；不向未来延长回放。输入的额外订单或超出预期范围的收款会报告`unexpected`，不会自动入账。

## 输出契约

schema为`quant-fund.observation-reconciliation/v1`：

- `status=matched`：有已确认交易，且本次比较无差异；退出码0。
- `status=differences`：存在missing、unexpected或字段mismatch；退出码2。
- `status=no_trades`：无可对比已确认交易；退出码2，不能当成验收通过。
- 非法输入、重复身份、损坏运行等抛出异常并返回非零退出码。
- `read_only=true`、`real_business_certified=false`始终保留；`classification`继承原始运行，合成样本不会升级为真实。
- 输出期末日期、确认/收款数量、待到账订单、差异及运行manifest和两张输入表的SHA-256。

金额绝对容差为0.000001元，净值和份额为0.00000001，不采用相对容差。日期和身份必须精确相同。这是数值比较容差，不代表实际业务舍入规则；若销售机构采用分位/份额舍入，应根据真实条款明确建模并重新验收，不能放宽容差隐藏差异。

## 真实验收仍需要的资料

以同一基金份额类别的一次申购、持有和赎回为最小闭环，提供历史有效招募说明书/费率及适用销售渠道规则、申请与确认凭证、对应净值披露、交易与银行日历、赎回实际到账流水。每项保留来源、适用期、真实获知时间及原始文件摘要；材料缺失时留空并阻断对应认证，不反推历史获知时间。

此入口覆盖已确认交易和赎回收款的对比，不核验申购银行扣款、分红实收、期初期末银行余额、未确认申请、凭证真伪及完整业务模型。公开净值与最新招募说明书不能替代这些资料。测试中的外部表由合成模型生成，仅验证软件路径，并通过修改金额、份额、费用和日期验证差异检出。
