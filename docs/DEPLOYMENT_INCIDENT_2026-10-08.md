# 2026-10-08：生产销毁后同名 Key Vault 部署失败

## 已确认原因

本次 Terraform 和工作流代码与 2026-10-06 成功部署一致，后续提交仅修改文档。资源生命周期在两次部署之间发生了变化：

| 时间（UTC） | 记录 | 结果 |
|---|---|---|
| 2026-10-06 | [完整部署 37519657614](https://github.com/gtsdrt/terraform/actions/runs/37519657614) | 成功，生产业务资源建立 |
| 2026-10-07 05:21:34 | [销毁运行 37575909382](https://github.com/gtsdrt/terraform/actions/runs/37575909382) | `env=tf / scope=all / confirm=DESTROY`，生产销毁成功 |
| 2026-10-07 05:24:32 | Azure deleted-vault 元数据 | `kv-azlz-9b6ac9f113` 在 `westeurope` 进入软删除状态 |
| 2026-10-08 07:05:42 | [部署运行 37741341989](https://github.com/gtsdrt/terraform/actions/runs/37741341989) | 两个 plan、测试部署和清理成功，生产 apply 失败 |

诊断时 Vault 活跃资源清单为空，同名软删除 Vault 仍存在，预定到期时间为 2026-10-14 05:24:32 UTC。同名 Vault 在软删除保留期内不能作为空的新 Vault 创建；purge protection 也限制提前永久删除。[Microsoft 的软删除说明](https://learn.microsoft.com/en-us/azure/key-vault/general/soft-delete-overview)

当时的生产 provider 设置 `recover_soft_deleted_key_vaults = false`。AzureRM 在 create 中检测到同名软删除 Vault 后，返回“自动恢复已被 features 关闭”的错误；原执行器没有识别这一文案，所以公开日志只显示 `unclassified`。[AzureRM 4.81 的实现](https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.81.0/internal/services/keyvault/key_vault_resource.go)

## 资源与权限核对

- 生产身份的两个组范围 Contributor、prod state 容器 Blob Data Contributor，以及 deleted-vault 元数据 Reader 都存在。
- 当前生产 state 有 35 个 managed instances。只读诊断计划显示这 35 个对象无变更，仅缺 Key Vault 及其诊断设置两个对象。
- Azure 记录中还有一次 NSG 规则并发冲突，随后同一规则操作成功；该记录不能代替上述 Key Vault 阻塞原因。
- 诊断只读取 state 并在本地副本 plan。管理员为此使用的 prod 容器临时 Blob Data Reader 已撤销，远程 state 和业务资源没有在诊断中修改。

## 修复与下一步

生产 provider 开启 `recover_soft_deleted_key_vaults = true`；test 保持关闭。自动恢复只针对 Terraform 当前配置名称的 Vault，在 production 审批后的 apply 中执行，保留已有 Vault 内容。purge protection、destroy 时不 purge、现有组范围授权均保留，订阅范围角色继续仅允许元数据读取。

生产 plan 摘要遇到 Key Vault create 会提示可能恢复同名软删除 Vault并保留内容。执行器新增 `key-vault-soft-delete`、`api-conflict`、`provider-error` 固定类别和固定处理提示，仍隐藏原始日志与敏感值。

操作顺序：

1. 审查并合入恢复修复 PR；如果合入触发 full，使用该新运行，避免同时再创建多条写入运行。
2. 检查新运行提交与计划。依据本次诊断，预期只恢复 Key Vault 并重建诊断设置；若看到其他变化，应一并审查。
3. 用独立生产审批账号查看提示和准确计划，批准 production 后由 prod 身份执行。
4. 验证 Vault 和诊断设置存在，生产运行成功，测试组内临时资源为空。

如果选择完全新的 Vault 内容，应改用新名称并审查计划。若已由管理员手动恢复同名 Vault，先 import 到当前业务 state 再部署。原失败运行及其保存计划仍引用关闭恢复的配置；重跑旧 apply 无法使用修复。

本记录描述已验证的原因及修复方案。写入恢复操作需通过新的生产审批；诊断阶段未执行恢复或 purge。
