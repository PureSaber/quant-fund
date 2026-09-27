# 数据契约v1

原有研究数据集包含以下五个文件，输入指纹覆盖其文件名和原始字节；新增 `calendars.json` / `holdings.csv` 也纳入指纹。净值/申赎日期使用无时区的`YYYY-MM-DD`，模型粒度为日，决策发生在收盘后。新日历和披露契约的 available_at 必须带时区。产品在真实市场的日内截止时间尚未建模。

|文件|内容|
|---|---|
|funds.json|基金主表与固定产品条款，数组形式|
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

## 基金主表

必填：`fund_id,name,manager,strategy,kind,inception,known_at`。`kind`为`public/private/etf`；策略示例为`equity/bond/cta/neutral`。首版申赎模型只支持CNY，净值必须标记`nav_fee_basis=net_all_fund_fees`。

|字段|含义|默认/要求|
|---|---|---|
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

历史条款变化不应直接覆盖当前主表后用于过去回测。首版需选择条款固定的研究区间，或者先实现有效期版本表。

## 分红与资金

`distributions.csv`列为`fund_id,ex_date,pay_date,known_at,cash_per_share`。公告获知日不晚于除息日，支付日不早于除息日；每份现金分红为正。除息日必须在日历中。

现金分为可用现金、冻结申购款、应收赎回/分红款。卖款未到账前不可再投资。日历不足以确定确认或到账日时保持待确认/应收，不会提前压缩到样本末日。分红公告已知、单位净值仍为除息前估值时，从持仓估值中扣除已除息金额，防止与应收分红重复计算。

初次导入真实数据时，应检查单位净值、复权净值与分红事件是否一致；系统不能只凭数值列证明供应商的复权方法正确。

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
