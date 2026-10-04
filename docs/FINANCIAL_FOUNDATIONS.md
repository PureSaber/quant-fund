# 05 / 10 用途日历与披露穿透

使用独立 Python 3.12 环境，依赖以 requirements.lock 为准。真实分类数据（user_provided/public_source）必须提供 calendars.json；synthetic 可以继续使用明确标记的旧单日历场景。

calendars.json 是 QDK PurposeCalendar 字段的数组；dataset.json 添加 calendar_ids，把 dealing、confirmation、banking 分别映射到 calendar_id。申购开放日、份额确认、资金到账按各自已知版本计算。先解析所有到期日，再冻结现金/份额；覆盖失败不留下半条订单。赎回/分红款仅在银行开日释放，未到账资金不可再投。

监控中的赎回流动性日期复用`Ledger.dealing_date`和`Ledger.offset(..., purpose="banking")`。锁定期只作为最早成交日下限，通知期仍从当前提交日计算一次；基金开放日、终止日和分用途日历与真实赎回指令使用同一条路径。预测仍是非约束性估计，不代表指令已经提交或保证成交。

可选 holdings.csv 采用 QDK financial.holdings.COLUMNS；权重为比例字符串，holding_date 与带时区 available_at 分离。加载文件纳入数据指纹；平台快照新增 lookthrough，输出披露敞口、路径、已知/未知权重。部分披露的未报告部分保留 UNKNOWN，不当作现金；只支持 long-only，披露穿透不是实时净持仓。

本模型仍以CNY场外基金日频申赎为主。可选`fund_terms.json`按有效期和获知日解析管理人、策略、开放日、锁定、确认/到账及费率，并在订单与份额批次保存版本证据；旧`funds.json`完整快照继续作为明确标记的非历史PIT路径。模型并未扩展为ETF场内撮合或PE/VC现金流模型。真实来源授权、合同完整性和准确获知时间仍需单独核验。
