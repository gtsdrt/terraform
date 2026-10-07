# 从零重建：Terraform 创建资源组与业务资源

本方案用于原业务资源已经丢失、选择全新重建的场景。v2 使用新资源组、新业务资源名称和新 state key，不覆盖旧 state，不尝试恢复或 purge 旧 Key Vault/日志资源。

2026-10-06 已完成此次 v2 初始化及完整部署，代码通过 [PR #16](https://github.com/gtsdrt/terraform/pull/16) 合入 main。结果见[部署记录](DEPLOYMENT_RECORD_2026-10-06.md)。日常更新按[部署指南](DEPLOYMENT_GUIDE.md)运行 main 的 `plan-only → full`；本文保留管理员初始化和接管已有基础资源的步骤。再次建立全新一代环境时，先协调新的 namespace 和 state key，见第 6 节。

当前仍存在的 backend 资源组 `Terraform`、存储账户 `gtsdrtterraform2` 和 GitHub 的三个 OIDC 身份继续使用。backend 属于独立的管理基础设施，不能在其承载的业务 state 中自我创建或删除。如果该账户也不存在，必须先另行建立 backend；本初始化脚本会停止，不会自动覆盖或替换它。

## 1. 分成三个 Terraform 层

| 目录 | 执行身份 | 创建或管理什么 | state |
|---|---|---|---|
| `tf-foundation/` | 本地 Azure 管理员 | 三个资源组、plan/test/prod 的限定授权，以及生产创建 Key Vault 所需的 deleted-vault 元数据读取角色 | `tfstate-foundation/foundation-v2.tfstate` |
| `tf-test/` | GitHub test 身份 | 临时网络测试资源 | `tfstate-test/terraform-test-v2.tfstate` |
| `tf/` | GitHub prod 身份，先经 production 审批 | 生产 network 与 Landing Zone | `tfstate-prod/executor-v2.tfstate` |

foundation 真正声明了 `azurerm_resource_group`，创建 `terraform-prod-v2`、`terraform-test-v2`、`azlandingzone-v2`。业务模块仍用 data source 引用这些组，避免每次测试清理时删除资源组和授权。

资源组设置 `prevent_destroy = true`；这约束 Terraform foundation 操作，不会阻止具有 Azure 删除权限的账号在 Portal 手动删除。不要把 foundation state 纳入日常 destroy 或测试清理。

plan 的诊断密钥读取角色只分配在三个业务资源组；test/prod 的 Contributor 也仅分配在各自资源组。生产在订阅范围额外获得两个 deleted-vault 元数据读取操作，供 provider 检查名称占用，不获得恢复或 purge 权限。GitHub 身份不需要订阅 Owner，也不需要订阅 Contributor。

## 2. 为什么先由管理员执行 foundation

Contributor 可以创建业务资源，但不能创建角色定义或分配 RBAC。foundation 同时创建资源组和授权，因此执行者需要这两类权限，例如订阅 Owner，或具有等效权限的管理员。[Azure Contributor 的边界](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/privileged#contributor)

这是一次管理员初始化；后续业务部署继续使用三个独立 OIDC 身份及现有生产审批。给其他应用（例如 `gtsdrt-ai-api`）权限，不会改变 GitHub 使用的 plan/test/prod 身份。

## 3. 准备新配置，暂停旧写入

初始化代码已在 main。使用最新 main 中的脚本和配置；原 `feat/terraform-fresh-foundation` 分支合入后可删除。先暂停写入工作流并等待现有运行结束，再执行管理员初始化或接管，避免 backend、授权与正在执行的部署并发修改。

在本地使用 Terraform 1.9.8、Azure CLI、GitHub CLI 和 Python 3。GitHub CLI 需要能读取仓库变量、管理工作流；Azure CLI 登录能初始化 foundation 的管理员账号。

```bash
# 读取已合入的初始化配置；本地 fetch/pull 不触发 GitHub 部署。
git fetch origin
git switch main
git pull --ff-only

# 确认 gh 的登录账号。关闭自动/手动写入入口并等待已有运行结束。
gh auth status
gh workflow disable deploy.yml --repo gtsdrt/terraform
gh workflow disable destroy.yml --repo gtsdrt/terraform
gh workflow disable cleanup-test.yml --repo gtsdrt/terraform
gh run list --repo gtsdrt/terraform --limit 20

# Azure 管理员登录并选取仓库配置的订阅。
az login
az account set --subscription ebb9caf4-b13d-40ca-a083-488a3aec4271
```

等待已有 deploy/destroy/recovery 运行完成或取消后再继续。不要在 apply 正执行时同时初始化。PR Check 可继续启用，它没有 Azure 凭据。

本地 foundation 使用 Azure CLI 登录，避免继承其他应用的 ARM 凭据：

```bash
unset ARM_CLIENT_ID ARM_CLIENT_SECRET ARM_CLIENT_CERTIFICATE_PATH ARM_CLIENT_CERTIFICATE_PASSWORD
unset ARM_OIDC_TOKEN ARM_OIDC_TOKEN_FILE_PATH ARM_TENANT_ID ARM_SUBSCRIPTION_ID
export ARM_USE_CLI=true ARM_USE_OIDC=false ARM_USE_MSI=false
```

## 4. 初始化独立 backend 与输入

```bash
# 默认只读核对订阅/租户/三个身份，并写本地 foundation 输入，展示 backend 准备清单。
python3 scripts/prepare_fresh_start.py

# 确认清单后执行 backend 准备；仍不创建业务资源组或业务资源。
python3 scripts/prepare_fresh_start.py --apply
```

脚本从仓库变量读取三个 Client ID，通过 Azure 查出对应的 service principal Object ID，验证它们属于当前目标配置，然后写入 `tf-foundation/foundation.tfvars.local.json`。此文件排除在 Git 之外，权限为 0600，包含身份元数据，不含密钥。

如果已经在 Portal 创建 v2 组和对应身份的组范围 Contributor 授权，脚本还会生成 `tf-foundation/imports.local.tf`。此文件仅包含这三个组和匹配 Contributor 授权的 import 块，排除在 Git 外；默认预览生成文件不修改 Azure 或 state。已有组的区域会写入输入，例如 `westeurope`，避免按默认 `norwayeast` 计划替换它们。不同组区域不一致、重复或带条件的 Contributor 授权需要先人工核对，脚本会停止。

普通 Reader 授权保持原状，不作为自定义 Plan Reader 导入；foundation 会补充生产诊断资源刷新所需的 listKeys/sharedKeys 操作，以及生产创建 Key Vault 所需的 deleted-vault 元数据读取。给 test/prod Contributor 后，仍需完成这些授权。

`--apply` 的具体操作：

1. 保留现有 state 账户，创建/保留三个私有容器；新增 `tfstate-foundation`。
2. 初始化缺失的三个 v2 state 文件；使用新的 lineage 和空资源清单。已存在的 v2 blob 原样保留，上传明确禁止覆盖。
3. 保留原有 `executor.tfstate`、`terraform-test.tfstate` 及历史 `tfstate` 容器，不下载、不迁移、不覆盖它们。
4. 为管理员授予仅 foundation 容器的 Blob Data Contributor；为已有工作流身份配置各自容器的 Reader/Contributor。
5. 为初始化暂时使用的管理员账户级 Blob Data Contributor 在结束或失败时撤销；已有管理员数据角色不会删除。

Azure Owner 是管理面权限，不自动授予 blob 数据访问。初始化临时数据授权与角色传播等待用于建立上述文件，后续 backend 使用 Entra ID，不启用 Shared Key。[Terraform Azure backend 的认证要求](https://developer.hashicorp.com/terraform/language/backend/azurerm)

若提示订阅、租户或应用名称不匹配，先核对 GitHub repository variables，不应把 Object ID 和 Client ID 混用。若提示临时数据角色未传播，脚本会撤销临时授权；稍后重新执行，已有 v2 state 不会被清空。

## 5. Terraform 创建资源组和授权

```bash
terraform -chdir=tf-foundation init -reconfigure -input=false -lockfile=readonly
terraform -chdir=tf-foundation validate
terraform -chdir=tf-foundation plan -var-file=foundation.tfvars.local.json -out=foundation.tfplan
terraform -chdir=tf-foundation apply foundation.tfplan
terraform -chdir=tf-foundation output resource_groups
```

全部对象不存在时，foundation 计划创建 12 个 managed resources：3 个资源组、2 个自定义角色、7 个角色分配。如果已经手动创建三个组及三个 Contributor 授权，预计导入这 6 个对象，创建剩余 6 个授权相关对象，并可能更新资源组标签。核对计划中只包含这些对象的创建、导入或标签更新，尤其检查角色分配范围及 principal IDs；资源组应无删除或替换，然后再执行 apply。import 块随该保存计划一起执行。[Terraform import 的工作方式](https://developer.hashicorp.com/terraform/language/import)

全新组的位置默认是 `norwayeast`；已有组保留脚本检测到的位置，network 和 Landing Zone 业务资源也使用各自组的位置。初始化成功后等待 RBAC 传播，再运行业务 plan。反复 apply foundation 会对照其 state 更新缺失或漂移的基础资源。

如果已有组由另一个 Terraform state 管理，应先由管理员协调所有权，不能重复导入。对于这次在 Portal 手动建立的 v2 组，核对上述自动生成的 import 块后，通过 foundation 的保存计划接管。

foundation 的远程 state 由管理员使用，GitHub 的 plan/test/prod 身份不授予该容器的数据权限，因此它们不能改动其中的资源组或授权记录。

如果使用早期版本初始化遇到 Contributor 授权的 `doesn't support update`，原因是导入后的 `skip_service_principal_aad_check` 空值与配置中的 true 产生更新差异，而 AzureRM 4.81 不支持角色分配 update。最新 foundation 省略该可选标志，保留 `principal_type = "ServicePrincipal"`。更新到最新 main 后重新 plan，核对三个 Contributor 授权为 no-op，再 apply 新计划；已经成功创建或导入的对象继续由现有 state 管理。旧的保存计划仍包含错误更新动作，不能用于这次重试。

## 6. 新业务名称如何避开软删除

业务组名包含 v2，Landing Zone 的 Key Vault/Storage/Log Analytics 后缀由订阅 ID、资源组名、前缀和 `deployment_generation = "v2"` 共同计算。后缀稳定，不会每次 plan 改变，也与旧 `kv-azlz-9ee601` 等名字不同。

Key Vault 的自动恢复关闭，生产仍保持 purge protection。旧软删除资源留在原状态，本流程不 purge、不恢复，不使用它们原来的名称。

新 v2 state 不接管旧资源。若发现旧 namespace 仍有活跃资源，应另外盘点其费用和生命周期，不能假定改变 state key 会删除它们。下一次再次重建为 v3 时，应一起调整资源组名、generation、三个 backend key 及初始化脚本的允许列表；不能只改 state key 而继续重复管理同一批活跃资源。

## 7. 启用工作流、预览与部署

foundation 完成且业务配置已在 main 后，启用正常工作流。若之后修改初始化代码，需要先完成 PR 审查及合入；当前 PR #16 已合入。

```bash
gh workflow enable deploy.yml --repo gtsdrt/terraform
gh workflow enable destroy.yml --repo gtsdrt/terraform
gh workflow enable cleanup-test.yml --repo gtsdrt/terraform

# 先创建一条新的只读预览运行。
gh workflow run deploy.yml --repo gtsdrt/terraform --ref main -f mode=plan-only
```

检查 plan 摘要目标是三个 v2 组、资源动作为新建，且不出现对旧 namespace 的删除或更新。确认后，由 `gtsdrt` 创建新的 full 运行：

```bash
gh workflow run deploy.yml --repo gtsdrt/terraform --ref main -f mode=full
```

顺序仍是：两个 plan → test apply → test cleanup → production 审批 → prod apply。用 `atea-shuangliang` 在 Actions 的运行 Summary 中查看计划并选择 **Review deployments → production → Approve and deploy**。PR 的 Approve/Merge 不是部署审批，详见[部署说明](DEPLOYMENT_GUIDE.md)。

不要重跑旧提交的失败运行，它仍可能使用旧 backend key 或旧恢复脚本。使用合入新配置后的 main 创建新运行。

## 8. 验收与状态管理

```bash
az resource list --resource-group terraform-prod-v2 --query '[].{name:name,type:type}' -o table
az resource list --resource-group azlandingzone-v2 --query '[].{name:name,type:type}' -o table
az resource list --resource-group terraform-test-v2 --query '[].{name:name,type:type}' -o table
```

确认生产资源与日志配置正常，test 组内的临时受管资源已清理，但三个组仍存在。foundation 是长期层；工作流的 destroy 只操作业务 state，test recovery 也只操作 test state。

本地切换业务 backend 时使用 `terraform init -reconfigure`，不使用 `-migrate-state` 把旧业务 state 复制到 v2。不要删除远程旧 state、执行 state rm 清空记录，或把空 state 覆盖到已有文件。

若 backend 或身份初始条件不成立，先停止并解决该前提；本指南不会通过赋予 GitHub 订阅 Owner、关闭生产审批或启用 Shared Key 来绕过错误。
