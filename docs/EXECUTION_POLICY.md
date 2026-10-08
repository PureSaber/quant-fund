# 显式费率、舍入与分批凭证

本次新增两个独立软件能力：订单按已冻结版本的费率/精度计算；独立确认和到账凭证支持一笔订单的多个批次。后者是只读核对，不会把未到账金额加入模拟账户，也不代表已经建立实盘账户。

## 费率与精度

基金主表和每个历史条款版本可选 `execution_policy`；缺省或 null 保持旧的单一申购费率与不量化份额行为。历史版本缺少这个新字段不会报错。设置策略时必须 `buy_fee=0`，避免两套费率重叠。策略全部字段必填，例如以下**合成示例**：

```json
{
  "subscription_tiers": [["0", "rate", "0.008"], ["1000000", "rate", "0.005"], ["5000000", "fixed", "1000"]],
  "money_decimals": 2,
  "share_decimals": 2,
  "money_rounding": "half_up",
  "share_rounding": "half_up",
  "redemption_fee_rounding": "per_lot",
  "residual_destination": "fund",
  "source_ref": "synthetic:reviewed-fee-table",
  "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

费率档为 `[含费申请金额的含端点下界, rate或fixed, 数值]`，下界从0严格递增，最后一档无金额上限；fixed为每笔固定人民币费用。使用实际条款时，必须核对份额类别、渠道、投资者类别、优惠适用范围和有效期；不同渠道应使用不同数据集或明确区分的基金身份，不自动套用互联网折扣。

申购按档位计算净金额，再按 money 精度舍入，费用为申请金额减净金额；份额为净金额除单位净值，再按 share 精度舍入。赎回总金额按总份额乘净值舍入，赎回费率仍按冻结批次的持有天数取档；`per_lot`逐批次舍入后合计，`aggregate`先合计后舍入。两者不能互换。支持 half_up、half_even、down，内部用 Decimal 计算。

`rounding_residual`随启用策略的成交导出：申购为净金额减确认份额市值，赎回为未舍入市值减舍入总金额，允许带符号。当前仅支持由基金资产承担/享有尾差的 `fund`规则，不自动退款或偷偷归入手续费。账户估值使用实际确认份额/现金，报告重放及条款核验会检查尾差，修改尾差并重新签写文件摘要仍会失败。

人工提交金额/份额超过配置精度时直接拒绝，失败不冻结资金或份额。回测生成目标单时向下取整到订单精度，随后检查最低申购额和现金；不会向上取整而透支。订单冻结提交时已知且申请日有效的策略，后来发布的新费率不改变已有订单。

这是显式规则执行，并不认证 source_ref 或摘要对应的文档真伪。T+7日内支付等合同上限不能填成“实际固定T+7到账”；实际到账以凭证核对。后端申购费、退款尾差、份额拆分、FOF层费用等未建模业务不能套用本策略。

## 分批确认和分次到账

```text
python -m quant_fund.cli reconcile-batches --run artifacts/run --confirmations data/private/batch-confirmations.csv --receipts data/private/batch-receipts.csv
```

确认表为[原确认表](OBSERVATION_RECONCILIATION.md)增加 `final`列：每个批次独立 evidence_id，同订单可以多行；final为true/false，至多一个true，且不能早于其他批次。缺少最终确认即为待确认，不因金额暂时相同而视为完成。

到账表为原到账表增加 `confirmation_id`，必须引用对应赎回确认的 evidence_id。一条确认允许多条实际收款，但收款 evidence_id 唯一；基金和订单身份必须一致，金额须为正，到账不得早于该批确认，也不能超出已验证运行区间。重复凭证、未知关联、未来记录直接拒绝。

结果 schema 为 `quant-fund.batch-reconciliation/v1`，保留 `real_business_certified=false`。逐确认输出确认净额、已实收、尚未收款、付款清单和 paid/unpaid/overpaid 状态；不会用后续批次的超额收款抵掉另一批欠款。对模型的份额/费用/金额比较按订单汇总，而每一批的净值和交易日期独立比较，不能平均掉差异。分次到账日期不同于模型预计日期会列出差异，而非改写模型。

`matched`退出0；`pending`、`differences`、`no_trades`退出2；非法输入非零退出。输入CSV和运行manifest保留摘要。旧 `reconcile-observations`仍严格一单一确认一收款，不接受批次表。两个入口都要求可重放的运行，旧仅哈希核验产物不能冒充完成账本核验。

当前不模拟巨额赎回比例调度、未知确认日、真实银行扣款和银行余额。真实分批凭证可被纳入并显示差异，不会因此将一次性成交模型自动改造成已认证的部分成交模型；扩展业务执行语义仍须真实业务规则和凭证。生产/私有材料只在本地受控目录核验，不能提交 Git。
