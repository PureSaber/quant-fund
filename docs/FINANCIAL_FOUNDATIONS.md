# 05 / 10 用途日历与披露穿透

使用独立 Python 3.12 环境，依赖以 requirements.lock 为准。真实分类数据（user_provided/public_source）必须提供 calendars.json；synthetic 可以继续使用明确标记的旧单日历场景。

calendars.json 是 QDK PurposeCalendar 字段的数组；dataset.json 添加 calendar_ids，把 dealing、confirmation、banking 分别映射到 calendar_id。申购开放日、份额确认、资金到账按各自已知版本计算。先解析所有到期日，再冻结现金/份额；覆盖失败不留下半条订单。赎回/分红款仅在银行开日释放，未到账资金不可再投。

可选 holdings.csv 采用 QDK financial.holdings.COLUMNS；权重为比例字符串，holding_date 与带时区 available_at 分离。加载文件纳入数据指纹；平台快照新增 lookthrough，输出披露敞口、路径、已知/未知权重。部分披露的未报告部分保留 UNKNOWN，不当作现金；只支持 long-only，披露穿透不是实时净持仓。

本模型仍以 CNY 场外基金日频申赎为主，基金主表条款固定；并未扩展为 ETF 场内撮合或 PE/VC 现金流模型。真实来源授权、历史披露完整性和准确披露时间需要单独核验。
