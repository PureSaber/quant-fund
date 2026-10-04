# 基金研究工作台

[![CI](https://github.com/PureSaber/quant-fund/actions/workflows/tests.yml/badge.svg)](https://github.com/PureSaber/quant-fund/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

独立Python项目，提供通用基金量化研究与组合配置，以中国公募和私募证券基金FOF为首阶段重点。首版提供单只基金研究、场外基金申赎回测、组合监控三条完整流程，并通过只读JSON快照预留puresaber等平台的接入位置。当前验收使用7只合成基金，未验证真实产品的投资效果。

公开仓库：[PureSaber/quant-fund](https://github.com/PureSaber/quant-fund)。本项目属于PureSaber的quant系列，开发采用`codex/*`功能分支、PR和CI门禁，见[贡献流程](CONTRIBUTING.md)与[仓库治理](.github/GOVERNANCE.md)。

## 运行

已经安装独立虚拟环境并生成样本时，在本目录运行：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

打开<http://127.0.0.1:8517>。默认数据目录为`data/demo`，合成研究时点为2026-08-31。若端口上的服务已经运行，直接打开页面即可。服务只监听本机。

在Windows新环境安装并生成样本（Python3.12）：

```powershell
git clone https://github.com/PureSaber/quant-fund.git
cd quant-fund
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m quant_fund.cli demo --out data/demo
.\.venv\Scripts\python.exe -m quant_fund.cli demo --historical-terms --out data/demo-terms
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Linux使用`python3.12 -m venv .venv`创建环境，并将以上`.\.venv\Scripts\python.exe`替换为`.venv/bin/python`。

已有非空样本目录不会被覆盖。`requirements.lock`记录完整依赖版本；CI分别验证Ubuntu和Windows的Python3.12。其他Python版本尚未列入支持矩阵。真实数据、数据库、虚拟环境和运行报告不提交到仓库，合成样本用`demo`命令生成。

## 三部分功能

平台入口可先运行`python -m quant_fund.cli preflight --dataset <目录> --config <配置JSON>`，
只读核验数据和静态回测条件，再用同一配置显式运行。预检不创建账户或回放申赎，
不代表优化器可行或真实业务认证；输出契约及边界见[接入说明](docs/INTEGRATION.md)。

|部分|首版已实现|主要入口|
|---|---|---|
|基金研究|按获知日期取净值版本；月度收益、年化波动、回撤、夏普、同类比较、相关性；CSV/Excel导入|页面“基金研究”“数据与导入”|
|组合回测|等权、股债60/40、OptimalPortfolios风险预算、skfolio风险平价与HRP；逐月滚动、显式费用、开放日、锁定期、资金到账、现金分红|页面“组合回测”|
|组合监控|管理人集中度、实际权重漂移、风险贡献、陈旧估值、可赎回资金日历、私募代理因子暴露、调仓差额复核|页面“组合监控”|

回测结束后导出完整报告，包含独立HTML、逐日资产账本、成交/订单/决策CSV、输入副本、代码副本、依赖版本、文件摘要和平台快照。界面中的基金类型筛选只影响研究视图；回测使用数据集的全部合格基金。

## 实际组合使用的开源组件

本项目采用小型适配层组合现有库；基金份额与现金账本独立实现。

|组件|实际用途|本次安装版本|上游|
|---|---|---|---|
|skfolio|RiskBudgeting、HierarchicalRiskParity|1.3.4|[官方仓库](https://github.com/skfolio/skfolio)|
|OptimalPortfolios|带权重上限的风险预算优化器|7.8.0|[官方仓库](https://github.com/ArturSepp/OptimalPortfolios)|
|qis/QuantInvestStrats|Euler波动风险贡献，并核对贡献合计|5.31.0|[官方仓库](https://github.com/ArturSepp/QuantInvestStrats)|
|AKShare|可选公募净值快照采集；日增长率构建总收益序列|1.18.97|[官方仓库](https://github.com/akfamily/akshare)|

skfolio、OptimalPortfolios和qis已通过真实库调用测试。AKShare适配器已完成轻量真实净值采集、入库幂等和冲突回滚验证；首次采集历史按采集日获知，不据此声称历史FOF账本已获验证。HRP使用完整层次树做递归风险分配，显式保留全部叶节点，不运行与其权重无关的平面聚类数量选择；因此支持两只基金的小研究池，同时保留现金预算与权重上限检查。暂未引入Riskfolio-Lib、bt和xalpha，避免首版出现重复优化器、重复账本以及依赖冲突；后续可以新增适配器进行同口径比较。各依赖许可证以上游仓库为准，锁定版本不替代发布时的许可证清单。

## CSV/Excel导入

`data/demo/nav-import-example.xlsx`提供21行合成净值示例，完整CSV在`data/demo/nav.csv`。Excel第一张表使用标准列名，也支持中文列名映射，例如`{"基金代码":"fund_id","净值日期":"nav_date"}`。

```powershell
.\.venv\Scripts\python.exe -m quant_fund.cli import-nav --input data/demo/nav-import-example.xlsx --database data/private/nav.sqlite
.\.venv\Scripts\python.exe -m quant_fund.cli export-nav --database data/private/nav.sqlite --out data/private/nav-snapshot.csv
```

同一基金、净值日期、获知日期构成不可变版本键。重复导入相同记录不增加行数；同键不同值会回滚整次导入。新修订应使用新的实际获知日期。导入库与研究数据集分开，配齐基金主表、日历和分红表后，将导出的CSV作为新数据集的`nav.csv`。详见[数据契约](docs/DATA_CONTRACT.md)。

## 命令行研究与复现

```powershell
.\.venv\Scripts\python.exe -m quant_fund.cli validate --dataset data/demo
.\.venv\Scripts\python.exe -m quant_fund.cli research --dataset data/demo --as-of 2026-08-31 --out artifacts/research.csv
.\.venv\Scripts\python.exe -m quant_fund.cli run --dataset data/demo --strategy optimal_risk --out artifacts/my-run
.\.venv\Scripts\python.exe -m quant_fund.cli verify-run artifacts/my-run
.\.venv\Scripts\python.exe -m quant_fund.cli run --dataset artifacts/my-run/inputs --config artifacts/my-run/manifest.json --out artifacts/reproduced
```

所有研究导出路径必须是新路径。`verify-run`检查已列文件的SHA-256、输入数据摘要和每日资金对账，不是数字签名，也不能证明金融模型正确。历史运行目录中的`code/src/quant_fund`、`code/pyproject.toml`和`code/requirements.lock`可在独立环境安装后重跑。精确复现还依赖同一Python/依赖版本和求解器环境。

可选公募采集：

```powershell
.\.venv\Scripts\python.exe -m quant_fund.cli fetch-public --code 000001 --out data/public/000001-snapshot.csv
```

这会访问公共数据接口。首次采集的历史记录全部以采集日作为`known_at`；不能凭今天下载的历史数据生成过去已知的数据库。分红权益、基金条款和真实交易日历仍需单独提供。

## 首版的适用边界

- 当前可交易模型为CNY场外公募和私募。ETF可做净值研究，交易回测明确拒绝，需另加场内成交价格、滑点和分红模型；外币交易同样需补汇率与换汇账本。
- 私募净值直接使用真实披露频率，缺失月份不插值。日度账本沿用最近已知估值并显示滞后，平滑曲线不能解释为低风险。
- 仅接受已扣基金层管理费、托管费及业绩报酬的净值；FOF自身层面的管理费、税、现金利息、业绩报酬高水位等尚未实现。申购费与按持有天数分档的赎回费已实现。
- 可选`fund_terms.json`支持管理人、策略、开放日、锁定、确认/到账和申赎费的历史版本。订单冻结申请日有效且提交日已知的版本，Lot冻结申购批次锁定期；报告保留实际消费版本并可原生复核。没有版本文件的旧`funds.json`继续按静态快照运行，并明确标记为非历史PIT。
- 分红采用除息日前已持有份额的简化权益规则；遇到前期申赎未确认导致权益不明会停止。转增、拆分、红利再投、复杂登记日规则、巨额赎回比例确认和清算尚未实现。
- 开放日、预约期、确认与到账滞后均按输入条款模拟。真实交易日历与真实合同需要验证；默认日历只排除周末，没有中国节假日。
- 监控基于模拟组合期末状态，未接入真实账户持仓、自动通知或实时调度。调仓差额是复核清单，不会生成或发送真实订单。
- 缺少清盘/退出基金的真实数据池仍可能存在幸存者偏差；净值披露延迟、回填和估值平滑也不能靠优化器消除。

下一步开发顺序与验收条件见[开发路线](docs/ROADMAP.md)，平台对接见[接口契约](docs/INTEGRATION.md)。

完整研究导出同时保留`platform-snapshot.json`。在干净Git检出下，还通过quant-lab发布`standard/v2`的`research`适配视图，可供统一校验、索引和Report Hub只读展示；其中`investable=false`、`rankable=false`，不等同于完整执行账本认证。适配器不替代原始申赎、应收款和每日资金对账，也不让合成样例变成真实FOF业绩。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m pip check
```

测试覆盖：未来净值隔离、历史修订、缺失月份、末期净值未披露、CSV/Excel幂等与冲突回滚、三类优化器实际调用、锁定/开放/预约限制、资金冻结与到账、分红对账、无持仓应收款、风险贡献、报告快照完整性。验收记录见[VALIDATION.md](docs/VALIDATION.md)。
