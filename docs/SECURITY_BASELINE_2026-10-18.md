# 安全基线与部署流程总结（2026-10-18）

本文汇总 v2 环境的完整生命周期、2026-10-17/18 的安全整改、当前控制矩阵和运维注意事项。日常操作步骤见[部署与审批指南](DEPLOYMENT_GUIDE.md)，权限边界见[安全架构](ARCHITECTURE.md)，新增资源见[添加资源指南](ADDING_RESOURCES.md)。

## 1. 范围与结论

- 仓库：`gtsdrt/terraform`（public）
- Azure：单订阅、`westeurope`；资源组由 foundation 管理
- 身份：GitHub OIDC，plan/test/prod 三个独立 service principal
- 结论：部署、测试清理、生产审批、漂移检测、销毁均已实际验证；foundation 与业务 state 分离；当前环境可按需 deploy / destroy

## 2. 端到端流程

```text
管理员（一次性/变更时）
  └─ tf-foundation plan/apply：3 个资源组、RBAC、平台审计工作区、活动日志、Defender

提交
  └─ 功能分支 → PR → Terraform Validate(tf/tf-test/tf-foundation) + Security Checks
     + CodeQL/GitGuardian + 代码所有者审查 → 合入 main

部署（main，命中 tf/**、tf-test/**、modules/**、.github/**）
  ├─ plan：plan 身份（plan-readonly 环境）只读生成计划；发布脱敏摘要；加密上传
  ├─ deploy-test → cleanup-test：tf-test 临时资源创建与清理
  ├─ production 审批：非触发账号批准（prevent_self_review，双审批人）
  └─ deploy-prod：解密并 apply 同一份已审查计划

运维
  ├─ 每日 Drift Detection：plan 只读身份，只发布脱敏动作，漂移/未部署时运行变红
  └─ 手动 Destroy：plan → 删除清单 → 加密 → production 审批 → apply

测试恢复
  └─ Terraform Deploy 结束后检查并清理 test state；每 6 小时兜底
```

关键规则：

- 只有 `main` 可部署；plan/test/prod 身份互不相同，`check_identities.py` 强制校验。
- plan 只能通过 `plan-readonly` 环境取得 OIDC token；旧 `ref:refs/heads/main` 联邦凭据已删除。
- 生产审批人为 `atea-shuangliang` 与 `gtsdrt`，`prevent_self_review`：谁触发，另一人审批；管理员不可绕过。
- plan 使用只读 state 身份（`-lock=false`）；apply 使用对应环境身份并申请 state lease。
- 计划文档绑定仓库/提交/run/环境/用途/范围，24 小时过期；密文保留 3 天。
- 完整部署、销毁、恢复共享 `terraform-deploy` 写入队列；plan-only 使用独立只读队列。

## 3. 本次整改（发现 → 修复）

| # | 问题 | 处理 | 状态 |
|---|---|---|---|
| 1 | 检测基线缺失：无订阅活动日志、无 Defender、无 flow log | 活动日志 + Defender 迁入 foundation；新增 VNet Flow Log | ✅ |
| 2 | 审计 Storage 启用共享密钥、无自身诊断 | `shared_access_key_enabled=false`；blob service 级 `StorageRead/Write/Delete` 诊断 | ✅ |
| 3 | 关键资源无删除保护 | Lock Manager 角色 + 3 把 `CanNotDelete` 锁 | ✅ |
| 4 | 工作负载身份越权承载订阅级资源 | 订阅级资源迁入 foundation；plan job 增加静态跨层权限预检 | ✅ |
| 5 | plan 身份可用于任意 main 工作流 | plan job 绑定 `plan-readonly` 环境；环境联邦凭据；旧 ref 凭据删除 | ✅ |
| 6 | 无漂移检测 | 每日只读 drift workflow，只发布脱敏动作 | ✅ |
| 7 | 平台安全开关未开 | secret scanning、push protection、Dependabot、SHA pinning、CodeQL | ✅ |
| 8 | `.gitignore` 放行目录内任意 PEM | 仅放行两个计划公钥证书 | ✅ |
| 9 | 审批死锁（唯一审批人触发） | 双审批人 + `prevent_self_review` | ✅ |
| 10 | Terraform 1.9.8 偏旧 | 升级到 1.16.6（三个 root + 全部工作流） | ✅ |
| 11 | 公开文档含订阅/租户/Client ID | 全部替换为占位符，真实值保留在 variables/私有记录 | ✅ |
| 12 | 无漏洞披露政策 | 新增 `SECURITY.md` | ✅ |
| 13 | Defender 默认 subplan 导致永久 replace | 显式固定 `subplan` | ✅ |
| 14 | 销毁时诊断先于锁删除，触发 `ScopeLocked` | 锁 `depends_on` 诊断设置；新增 `scope-locked` 错误类别 | ✅ |
| 15 | 部署时同 VNet subnet 并行创建触发 `AnotherOperationInProgress` | 确认为 Azure 瞬时并发；重试成功；如复发可加低并行度或串行化 | ⚠️ 运维处理 |
| 16 | drift 脚本 `-chdir DIR` 语法错误 | 改为 `-chdir=DIR` 并加回归测试 | ✅ |

## 4. 当前控制矩阵

| 领域 | 控制 | 归属 | 验证方式 |
|---|---|---|---|
| 网络 | 子网 CIDR 入站分段；显式拒绝出站 Internet；VNet Flow Log（v2/90 天） | `tf` + `modules` | `az network nsg rule`、`flow-log list` |
| 身份 | 3 个独立 SP；plan 环境绑定；Lock/Flow Log 最小角色；无订阅级 Contributor | foundation | `az role assignment list`、foundation test |
| 机密 | Key Vault RBAC + purge protection + 网络 Deny；计划 RSA/AES 加密 | `tf` + workflow | `az keyvault show`、plan artifact |
| 数据 | Storage 共享密钥关闭、TLS1.2、网络 Deny、blob 审计诊断；LA 每日配额 | `tf` | `az storage account show` |
| 检测 | 平台 LA（90 天）、订阅活动日志、Defender 三计划 | foundation | `az monitor diagnostic-settings subscription list`、`az security pricing list` |
| 供应链 | Action SHA 固定、secret scanning + push protection、Dependabot、CodeQL、gitleaks | repo/CI | PR 检查、Security 页面 |
| 审批 | 双人 production 审批、`prevent_self_review`、禁管理员绕过 | repo settings | run pending deployments |
| 漂移 | 每日只读 plan + 脱敏摘要 | workflow | Drift Detection run |
| 预检 | 订阅级资源/未授权锁/身份相同在审批前失败 | workflow | plan job |

## 5. 验证记录

- Deploy：`39112868852`（2026-10-18）plan/test/cleanup/prod 全 success
- Destroy：`39117229263` 全 success（锁先于诊断删除）
- foundation：apply 后二次 plan `No changes`
- 本地：Terraform 1.16.6 下三个 root 的 fmt/validate/test；Python 45 tests；actionlint 0
- 部署后核验：3 条 flow log（v2/90 天）、3 把锁、KV/Storage 诊断、订阅活动日志、Defender

## 6. 运维注意事项

- **`stale-plan`**：部分 apply 或并发操作后旧计划失效，必须新建运行，不要重跑旧 apply job。
- **`AnotherOperationInProgress`**：Azure 对同一 VNet 的并发写会返回 409（`api-conflict`）。等待后新建 full 运行；若持续出现，可考虑降低 apply 并行度或串行化同 VNet 子网。
- **`ScopeLocked`**：父资源 `CanNotDelete` 锁会阻止删除子资源。当前锁已 `depends_on` 诊断设置；如仍出现，先确认锁已删除，再新建 destroy plan。
- **销毁后 drift**：环境完全销毁后，配置仍定义资源，`plan` 会显示 create（exit 2），Drift Detection 会报 drift detected——这是“未部署”，不是配置漂移。
- **Key Vault 软删除**：销毁后名称保留 7 天；下次部署按配置恢复同名 Vault 并保留内容；保留期结束并清除后无法恢复。
- **foundation**：没有自动 apply 工作流；变更必须由管理员 plan/apply，且先于依赖它的业务部署。
- **审批**：触发者不能批准自己的部署；用另一个审批账号。

## 7. 未完成/后续

- Private Endpoint、Azure Firewall + UDR（需连通性与成本评估）
- 强制签名提交（当前仅在 `SECURITY.md` 建议；启用会阻塞未配置签名的账号）
- 数据保护：Landing Zone LA 保留期、审计 Storage GRS、Blob 软删除/版本化
- plan 角色进一步收窄（`*/read` 与诊断刷新列表操作需在真实 plan 身份下验证）
- state 备份/恢复演练、跨区域 DR
