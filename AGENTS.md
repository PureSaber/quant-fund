# quant-fund

通用基金研究与FOF组合配置项目。以CNY场外公募和私募证券基金为首版交易模型，其他基金类型的边界见README。

## 开发

- 使用Python3.12与独立虚拟环境；依赖以`requirements.lock`为准。
- 功能分支采用`codex/*`，默认分支修改通过PR与CI，遵循`CONTRIBUTING.md`和`.github/GOVERNANCE.md`。
- 对于读取文件，直接打开或搜索，不另建脚本。修复先查根因，不以默认值、插值或硬编码掩盖问题。
- 净值历史严格按`known_at`过滤。不得使用未来修订、累计净值冒充总收益，或将私募低频净值插值成日收益。
- 优化器权重和资金账本分别验收；赎回未到账不可用于申购，待确认指令不可重复占用资金或份额。
- 不提交真实基金私有数据、账户信息、凭据、数据库及运行报告。样本由`demo`命令生成并明确标记为合成数据。
- 对外schema与金融口径变更必须同步文档；不要将未验证模型描述成实盘能力。

## 验证

```bash
python -m pip check
python -m ruff check src tests app.py
python -m ruff format --check src tests app.py
python -m pytest -q
```
