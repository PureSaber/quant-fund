# 仓库治理

本仓库对齐PureSaber的quant系列[公开治理约定](https://github.com/PureSaber/quant-research-notes/blob/main/P0_GITHUB_GOVERNANCE_CONTROLS.md)。平台保护以GitHub中实际启用的Ruleset为准，不能以文档声明代替。

默认分支为`main`，首次空仓初始化之后的变更通过PR：

- 默认分支禁止删除与force push。
- PR必须解决review讨论，并通过最新目标分支上的全部必需CI检查。
- 当前检查为`test (ubuntu-24.04, py3.12)`和`test (windows-latest, py3.12)`。
- 单人维护阶段独立审批数量为0；存在持续可用的第二位独立维护者后，再升级为至少1名独立审批。
- `refs/tags/v*`版本标签禁止更新和删除。
- 不设置常驻bypass actor，不为红色CI关闭保护规则。

CI以只读`contents`权限运行；GitHub Actions固定到已核对的提交SHA。测试使用合成数据，不依赖账户凭据或实时第三方净值接口。

真正的平台或规则故障需要紧急处理时，先记录原因、影响范围、原规则和恢复计划，仅临时停用受影响规则；修复仍保留PR与验证证据，完成后立即恢复并核验。常规发版、缺少审批人和未通过测试不构成停用规则的理由。
