# 开发与贡献

本仓库沿用PureSaber的quant系列开发方式，保持独立依赖、独立数据契约和可复现验证。

1. 从最新`main`创建`codex/<topic>`分支。一次PR聚焦一个明确目标，先说明问题、数据口径及验收条件。
2. 使用Python3.12与`requirements.lock`建立隔离环境。默认验证使用合成数据；真实私募数据和账户资料不得提交。
3. 先定位问题原因，再修改实现。涉及净值时点、收益口径、费用、申赎、分红和资金可用性的改动必须有相应回归证据。
4. 本地执行下列检查，提交后创建PR；CI必须在当前PR提交和最新目标分支上通过，且review讨论已解决。
5. 合并后再次确认`main`的CI通过。版本发布使用新的annotated `v*`标签，已有版本标签不移动、不重建、不删除。

```powershell
python -m pip install --no-deps -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
python -m pip check
python -m ruff check src tests app.py
python -m ruff format --check src tests app.py
python -m pytest -q
```

PR描述写清最终行为、验证结果和适用边界。对外输出的schema、字段含义、货币单位或版本行为发生变化时，同时更新`docs/DATA_CONTRACT.md`或`docs/INTEGRATION.md`。

基金研究与FOF配置共用研究组件，ETF场内成交、跨币种结算和PE/VC现金流模型需要独立适配，不应通过放松现有校验冒充支持。详细扩展顺序见`docs/ROADMAP.md`。

本项目采用MIT许可证；新增第三方依赖或复制上游实现时，应保留其许可证及来源。合成数据的收益不构成策略有效性的证据。
