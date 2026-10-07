# v2 部署验证记录：2026-10-06

本记录保存此次 v2 重建后的实际 GitHub 运行和 Azure 资源核对结果。日常步骤见[部署指南](DEPLOYMENT_GUIDE.md)，首次初始化或管理员维护见[FRESH_START](FRESH_START.md)，身份与 RBAC 见[安全架构](ARCHITECTURE.md)。

## 提交与运行

| 项目 | 已核对结果 |
|---|---|
| 初始化与配置修复 | [PR #16](https://github.com/gtsdrt/terraform/pull/16) 已合入 main |
| 部署提交 | `1e7cf74f399ef1c381c17fdb87f6e769115f38cb` |
| 只读预览 | [37519443309](https://github.com/gtsdrt/terraform/actions/runs/37519443309)，success |
| 完整部署 | [37519657614](https://github.com/gtsdrt/terraform/actions/runs/37519657614)，success |
| full 发起账号 | `gtsdrt` |
| production 审批 | GitHub 审批记录显示 `atea-shuangliang` approved |
| full 创建时间 | 2026-10-06 19:31:50 UTC（Europe/Oslo 21:31:50） |
| full 完成状态更新时间 | 2026-10-06 19:40:57 UTC（Europe/Oslo 21:40:57） |
| 独立测试恢复 | [37520805877](https://github.com/gtsdrt/terraform/actions/runs/37520805877)，success |

完整部署的 `plan (tf-test)`、`plan (tf)`、`deploy-test`、`cleanup-test`、`deploy-prod` 全部成功。生产在测试部署与清理成功、独立 production 审批通过后，执行了同一次 full 运行保存并加密的计划。

三个正常工作流 Terraform Deploy、Terraform Destroy、Terraform Test Recovery 均已启用。production 核对时仅允许 main，审批人为 `atea-shuangliang`，禁止自行审批，管理员不可绕过。

## Foundation 与业务资源

本次在 Portal 建立的三个组及对应 Contributor 授权由 foundation 接管，组区域保留为 `westeurope`。修正可选 AAD-check 标志后，管理员的只读 foundation plan 显示 12 个 managed objects 全部为 no-op，三个 Contributor 授权无更新或替换。

| 层 | 资源组 | 区域 | Azure 资源清单核对 |
|---|---|---|---|
| 生产 network | `terraform-prod-v2` | `westeurope` | 2 个 VNet、2 个 NSG |
| Landing Zone | `azlandingzone-v2` | `westeurope` | 1 个 hub VNet、3 个 NSG、1 个 Key Vault、1 个 Log Analytics workspace、1 个 Storage account |
| 临时 test | `terraform-test-v2` | `westeurope` | 组保留，组内资源清单为空 |

以上是 `az resource list` 可见的资源清单，不能直接与 Terraform managed resource 数量相等：子网、规则、关联和诊断设置分别由 Terraform 管理。当前配置的 foundation 为 12 个 managed objects，生产业务为 37 个，test 部署时为 12 个 network 对象，完成后清理。

state 账户继续使用 backend 组 `Terraform` 中的 `gtsdrtterraform2`，与业务组的生命周期分开。三个 v2 key 为：

- `tfstate-foundation/foundation-v2.tfstate`
- `tfstate-prod/executor-v2.tfstate`
- `tfstate-test/terraform-test-v2.tfstate`

历史 state 保留；本记录仅包含运行、身份和资源清单元数据，不包含完整 state、计划或原始 Terraform 日志。

## 后续操作

1. 日常变更通过 PR 合入 main，按 `plan-only → full → production 审批` 生成和执行新计划。预览成功后仍需审查 full 中重新生成的摘要。
2. foundation 已初始化；业务部署继续引用现有组和授权。原修复分支合入后可清理，部署使用 main。
3. 生产实际审计事件投递、私网/业务访问，以及更严格的网络策略仍需按业务要求验证。本次确认了工作流与资源部署成功，尚未验证完整业务数据路径。
4. 后续清理只操作相应业务 state。生产销毁需独立审查；foundation state 保留组与权限。

复核本次运行和资源清单可使用：

```bash
gh run view 37519657614 --repo gtsdrt/terraform
gh run view 37520805877 --repo gtsdrt/terraform
az resource list --resource-group terraform-prod-v2 --query '[].{name:name,type:type}' -o table
az resource list --resource-group azlandingzone-v2 --query '[].{name:name,type:type}' -o table
az resource list --resource-group terraform-test-v2 --query '[].{name:name,type:type}' -o table
```

这是 2026-10-06 的验证记录；以后状态以当前 Actions 运行、Azure 资源和 Terraform state 为准。
