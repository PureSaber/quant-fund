# 数据契约v2

原有研究数据集包含以下五个文件；`calendars.json`、`holdings.csv`和`fund_terms.json`存在时也纳入输入指纹。v2指纹对每个实际加载文件同时绑定文件名和原始字节。净值/申赎日期使用无时区的`YYYY-MM-DD`，模型粒度为日，决策发生在收盘后。新日历和披露契约的available_at必须带时区。产品在真实市场的日内截止时间尚未建模。

|文件|内容|
|---|---|
|funds.json|旧模式为固定产品快照；历史条款模式只含不可变身份字段|
|fund_terms.json|可选；存在时启用按有效期及获知日选择的完整条款/分类版本|
|nav.csv|单位净值与分红再投资收益净值的历史版本|
|calendar.csv|`date`列，唯一、升序、显式交易日历|
|distributions.csv|现金分红；没有分红时仍保留表头|
|dataset.json|来源、数据分类、已知日期口径和日历来源|

## 净值列

|列名|类型与要求|用途|
|---|---|---|
|fund_id|文本，须在基金主表登记；Excel代码列建议设为文本|保留前导零|
|nav_date|净值所属日期|收益与成交定位|
|known_at|实际首次可获知该版本的日期，不早于nav_date|历史时点过滤|
|unit_nav|有限正数|份额申赎和持仓估值|
|total_return_nav|有限正数，分红再投资口径|收益研究，不能使用累计净值替代|
|source|非空来源说明|追溯数据|

每个`fund_id/nav_date/known_at`只能有一条记录。不自动推断披露日，不填0、不跨缺失月份插值、不以旧净值替代交易日净值。研究用月度/周度期末值必须覆盖显式日历中该期最后交易日，且在决策时已获知。

研究指标使用各基金最后一段连续完整月份；同类百分位要求基金类型、策略和样本起止相同。同类不足两只不显示排名。优化器使用窗口内完整共同观测，至少12期，既不填充缺失观测，也不把不同频率的风险直接混合。

## 基金身份、旧静态快照与历史条款

没有`fund_terms.json`时，`funds.json`沿用旧契约，必填`fund_id,name,manager,strategy,kind,inception,known_at`；其他固定条款继续使用下表的旧默认值和校验。此路径标记为`legacy_static`和`historical_terms_pit=false`，保持旧计算语义，但不能作为历史条款PIT证据。

存在`fund_terms.json`时，`funds.json`每项只能包含`fund_id,name,inception,known_at`。名称在v2中是不可变身份；管理人、策略和kind属于版本表。不得在身份文件中保留“最新条款”作为缺失版本的回退。

`fund_terms.json`是完整版本数组。每项必须包含`fund_id,terms_id,version_id,effective_from,effective_to,known_at`和下表全部条款字段。`terms_id`标识同一逻辑条款的修订链；`version_id`全局唯一；同一基金/terms_id/known_at只能有一个版本。`effective_to`为不含该日的上界，无截止时必须为JSON的`null`，不能使用空字符串。每个获知切片先按terms_id和known_at取当时最新修订，再按`effective_from <= effective_on < effective_to`筛选；结果缺失、重叠或歧义均失败。版本ID不参与新旧排序。

`kind`为`public/private/etf`；策略示例为`equity/bond/cta/neutral`。首版申赎模型只支持CNY，净值必须标记`nav_fee_basis=net_all_fund_fees`。

|字段|含义|默认/要求|
|---|---|---|
|manager|管理人分类|版本模式必填非空|
|strategy|策略分类|版本模式必填非空|
|kind|基金交易类型|`public/private/etf`|
|currency|计价币种|CNY|
|end_date|终止日，含当日不再纳入新配置|可空；无自动清算|
|share_group|同一基金不同份额分组|同组只选一个合格代码，按代码排序|
|confirm_lag|申请净值日后确认滞后|confirmation 用途开日，默认1；仅 synthetic 旧模式沿用单日历|
|settle_lag|赎回到账滞后|banking 用途开日，默认3；同时检查实际到账日不早于确认日|
|notice_days|最少预约期|自然日；不允许收盘决策当日成交|
|lock_days|每批份额锁定期|自然日，从申购申请净值日开始|
|buy_fee|外扣式申购费率|`净申购金额=总金额/(1+费率)`|
|sell_tiers|`[[持有天数上界,费率],…]`|上界不含，升序；末档至少100000天|
|open_dates|显式开放日数组|私募必填；公募空数组代表日历各交易日|
|min_buy|最低申购金额|币种金额，默认0|
|max_stale_days|研究/下单允许净值滞后|自然日，默认10|
|nav_fee_basis|基金净值费用口径|只接受`net_all_fund_fees`|

研究决策日D使用`effective_on=D,known_on=D`的分类。收益历史仍是同一产品的完整历史，不表示整段历史都属于决策日策略；监控代理篮子在每个收益端点使用该端点有效、监控日已知的分类。

订单提交日S逐日检查候选申请日的条款，使用`effective_on=申请日,known_on=S`解析开放日、预约期、终止日和最低金额。选中申请日后，订单冻结该版本、确认/到账滞后和申赎费率；确认只等待确切申请净值，不重新读取后来获知的条款。申购确认生成的Lot冻结申购版本和lock_days；以后赎回逐Lot使用该锁定期，缺少批次证据时历史条款模式失败。赎回费使用赎回申请日冻结的sell_tiers和各Lot持有天数。

管理人、策略、开放日、费率等真实业务资料仍需来源合同和实际获知时间核验；`historical_pit`描述选择模型能力，不自动证明来源真实或完整。

## 分红与资金

`distributions.csv`列为`fund_id,ex_date,pay_date,known_at,cash_per_share`。公告获知日不晚于除息日，支付日不早于除息日；每份现金分红为正。除息日必须在日历中。

现金分为可用现金、冻结申购款、应收赎回/分红款。卖款未到账前不可再投资。日历不足以确定确认或到账日时保持待确认/应收，不会提前压缩到样本末日。分红公告已知、单位净值仍为除息前估值时，从持仓估值中扣除已除息金额，防止与应收分红重复计算。

`Ledger.advance`以整个交易日为事务边界。结算、确认、分红、对账或估值失败时，日期、现金、冻结款、应收款、份额、订单状态、分红标记和当日新增记录全部回滚，原异常继续抛出；已有订单与份额对象的引用保持有效。修正输入后可重试同一天，不能跳过失败日继续计算。该保证针对内存账本，不代表持久化数据库事务。

初次导入真实数据时，应检查单位净值、复权净值与分红事件是否一致；系统不能只凭数值列证明供应商的复权方法正确。

公开采集的复权研究序列从首个单位净值归一为1，后续每个净值日乘以`(当日单位净值+当日每份现金分红)/上一观测单位净值`。来源的日增长率可能只包含价格变化，不能直接连乘。累计净值仅用于核对区间现金分红合计，禁止作为总收益净值。四份来源CSV分别为`.raw.csv`、`.cumulative.raw.csv`、`.dividends.raw.csv`和`.splits.raw.csv`；原始空分红表是该来源的无记录响应，不是独立历史完整性认证。首个观测前与最后观测后的分红不进入该区间收益；区间除息日必须有净值，拆分、重复记录、单位不明、源日期不齐或现金合计不一致均拒绝，不插值或猜测。

这是一条按当期捕获信息重建的回顾性研究序列，不等同于现金分红到账的申赎账本。实际首次获知日期保持采集日；不能直接把来源分红表转换为历史已知的`distributions.csv`。旧采集快照不覆盖，修正后必须生成新的快照和独立导入库；同一获知日期下不同数值仍触发版本冲突。

## 元数据

```json
{
  "classification": "user_provided",
  "label": "私募研究样本",
  "calendar_source": "请填写实际日历来源与覆盖范围",
  "known_at_policy": "请填写披露/接收时间的真实来源"
}
```

`classification`只允许`synthetic/user_provided/public_source`。可选`default_as_of/default_start`设置页面默认日期。不得把合成数据标为真实来源。样本生成器的Excel与CSV均显式标记合成来源。

## 分用途日历与披露持仓扩展

真实分类必须提供 calendars.json（PurposeCalendar 数组）及 calendar_ids（dealing/confirmation/banking 到 id 的映射）；缺少覆盖失败，不回退工作日。可选 holdings.csv 使用 QDK financial.holdings.COLUMNS，披露权重按 available_at 过滤；未知部分保留 UNKNOWN。平台快照新增 lookthrough 字段。完整字段和调用边界见 [FINANCIAL_FOUNDATIONS.md](FINANCIAL_FOUNDATIONS.md)。
## 显式执行精度与费率扩展

基金主表与历史条款版本增加可选 `execution_policy`，默认 null 保持旧口径。完整字段、费率档位、舍入顺序、尾差及版本冻结语义见[执行规则](EXECUTION_POLICY.md)。启用时成交CSV增加 `rounding_residual`；归档输入和本仓原生验证器共同校验，不能用旧验证器认证新策略。
