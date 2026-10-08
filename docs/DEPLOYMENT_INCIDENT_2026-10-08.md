# 2026-10-08：生产销毁后同名 Key Vault 部署失败

## 已确认原因

最初失败运行的 Terraform 和工作流代码与 2026-10-06 成功部署一致，此前提交仅修改文档。资源生命周期在两次部署之间发生了变化：

| 时间（UTC） | 记录 | 结果 |
|---|---|---|
| 2026-10-06 | [完整部署 37519657614](https://github.com/gtsdrt/terraform/actions/runs/37519657614) | 成功，生产业务资源建立 |
| 2026-10-07 05:21:34 | [销毁运行 37575909382](https://github.com/gtsdrt/terraform/actions/runs/37575909382) | `env=tf / scope=all / confirm=DESTROY`，生产销毁成功 |
| 2026-10-07 05:24:32 | Azure deleted-vault 元数据 | `kv-azlz-9b6ac9f113` 在 `westeurope` 进入软删除状态 |
| 2026-10-08 07:05:42 | [部署运行 37741341989](https://github.com/gtsdrt/terraform/actions/runs/37741341989) | 两个 plan、测试部署和清理成功，生产 apply 失败 |

最初诊断时 Vault 活跃资源清单为空，同名软删除 Vault 仍存在，当时预定到期时间为 2026-10-14 05:24:32 UTC。该日期属于 10 月 7 日那次删除，不能用于判断随后再次删除后的保留截止时间；应以当前 deleted-vault 元数据为准。同名 Vault 在软删除保留期内不能作为空的新 Vault 创建；purge protection 也限制提前永久删除。[Microsoft 的软删除说明](https://learn.microsoft.com/en-us/azure/key-vault/general/soft-delete-overview)

当时的生产 provider 设置 `recover_soft_deleted_key_vaults = false`。AzureRM 在 create 中检测到同名软删除 Vault 后，返回“自动恢复已被 features 关闭”的错误；原执行器没有识别这一文案，所以公开日志只显示 `unclassified`。[AzureRM 4.81 的实现](https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.81.0/internal/services/keyvault/key_vault_resource.go)

## 资源与权限核对

- 生产身份的两个组范围 Contributor、prod state 容器 Blob Data Contributor，以及 deleted-vault 元数据 Reader 都存在。
- 最初诊断时生产 state 有 35 个 managed instances。只读诊断计划显示这 35 个对象无变更，仅缺 Key Vault 及其诊断设置两个对象。该数量是当时的快照，不代表随后全量销毁后的状态。
- Azure 记录中还有一次 NSG 规则并发冲突，随后同一规则操作成功；该记录不能代替上述 Key Vault 阻塞原因。
- 诊断只读取 state 并在本地副本 plan。管理员为此使用的 prod 容器临时 Blob Data Reader 已撤销，远程 state 和业务资源没有在诊断中修改。

## 修复方案

生产 provider 开启 `recover_soft_deleted_key_vaults = true`；test 保持关闭。自动恢复只针对 Terraform 当前配置名称的 Vault，在 production 审批后的 apply 中执行，保留已有 Vault 内容。purge protection、destroy 时不 purge、现有组范围授权均保留，订阅范围角色继续仅允许元数据读取。

生产 plan 摘要遇到 Key Vault create 会提示可能恢复同名软删除 Vault 并保留内容。执行器新增 `key-vault-soft-delete`、`api-conflict`、`provider-error` 固定类别和固定处理提示，仍隐藏原始日志与敏感值。

## 修复后的验证结果

[PR #18](https://github.com/gtsdrt/terraform/pull/18) 已合入 main，验证提交为 `d529ca9b67044f8ed3d143ac7d76c83a3c9fd80e`。

| 运行 | 实际结果 |
|---|---|
| [首次修复运行 37746657054](https://github.com/gtsdrt/terraform/actions/runs/37746657054) | 由 atea-shuangliang 的合入触发；此账号同时是唯一 production 审批人，禁止自行审批导致等待，随后取消 |
| [重新发起的 full 37748553797](https://github.com/gtsdrt/terraform/actions/runs/37748553797) | 由 gtsdrt 发起，atea-shuangliang 批准 production；两个 plan、test apply、test cleanup、prod apply 全部 success |
| [后续 destroy 37749590433](https://github.com/gtsdrt/terraform/actions/runs/37749590433) | `env=tf / scope=all`；只读 destroy plan 与审批后的 destroy job 均 success |

本轮恢复、生产部署和后续全量销毁已通过 GitHub 运行记录确认。原先关闭恢复造成的阻塞已修复；不据此承诺未来任意状态下都成功。保留期内恢复保留 Vault 内容，保留期结束并完成清除后重新创建不能带回原有 secrets/keys。未来部署按[生命周期表](DEPLOYMENT_GUIDE.md#销毁后下次部署会怎样)判断，并审查当次新计划。

审批入口是运行左侧 **Summary → Review deployments → production → Approve and deploy**。使用唯一审批人 atea-shuangliang 登录；发起或合入涉及部署的变更由 gtsdrt 完成。若 atea 自己触发了等待生产的运行，取消该等待运行并由 gtsdrt 发起新的 full；单纯切换浏览器登录账号不能改变旧运行的发起人。

## 后续同名部署

操作顺序：

1. 使用包含 PR #18 的最新 main 创建新运行；若合入其他变更已触发 full，使用该新运行，避免同时再创建多条写入运行。
2. 检查新运行提交与计划。最初诊断只缺 Vault 与诊断设置；随后已执行生产全量销毁，下一次计划会重新建立业务资源，不能仍按只有两个对象处理。
3. 用独立生产审批账号查看提示和准确计划，批准 production 后由 prod 身份执行。
4. 验证 Vault 和诊断设置存在，生产运行成功，测试组内临时资源为空。

如果选择完全新的 Vault 内容，应改用新名称并审查计划。若已由管理员手动恢复同名 Vault，先 import 到当前业务 state 再部署。原失败运行及其保存计划仍引用关闭恢复的配置；重跑旧 apply 无法使用修复。

本记录区分最初诊断快照与后续成功验证。诊断阶段未执行恢复或 purge；实际恢复由正常 production 审批后的部署工作流完成。
