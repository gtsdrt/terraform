# 安全架构与运维边界

首次上线看 [FRESH_START](FRESH_START.md)；日常部署、审批和故障处理看 [DEPLOYMENT_GUIDE](DEPLOYMENT_GUIDE.md)。本文描述 v2 foundation 与业务层的授权边界。

## Terraform 管理层

`tf-foundation/` 在独立的 foundation state 中创建三个 v2 资源组、自定义角色及角色分配。执行者为具有创建资源组、角色定义和 RBAC 授权能力的 Azure 管理员，例如订阅 Owner；此身份不进入日常 GitHub 部署 job。

三个组是 `terraform-prod-v2`、`terraform-test-v2`、`azlandingzone-v2`。组设置 `prevent_destroy`，工作流的 test 清理及 prod 销毁不操作 foundation state。业务模块通过 data source 使用组，因此仅执行业务 Terraform 无法在缺少 foundation 时创建组。

`prevent_destroy` 只保护 Terraform 管理操作，不阻止 Azure 管理员在 Portal 删除资源组。删除组会破坏业务资源及其组范围授权；需要管理员重新核对 foundation，而不能仅给另一个应用增加 Contributor。

## 认证和授权

三个 GitHub OIDC subject 绑定独立 service principal，当前仓库 owner/repository 的 immutable 数字 ID 构成 subject 前缀。

| 身份 | OIDC subject 后缀 | 管理权限 | state 数据权限 |
|---|---|---|---|
| plan | `ref:refs/heads/main` | 三个 v2 组的只读角色，包含刷新诊断资源所需的 Storage listKeys/Log Analytics sharedKeys | prod/test 容器 Blob Data Reader |
| test | `environment:test` | 仅 test-v2 组 Contributor | 仅 test 容器 Blob Data Contributor |
| prod | `environment:production` | 仅两个生产 v2 组 Contributor；订阅范围附加 deleted-vault 元数据读取 | 仅 prod 容器 Blob Data Contributor |
| 本地管理员 | Azure CLI 登录 | foundation 与初始化所需管理权限 | foundation 容器 Blob Data Contributor |

foundation 自定义 plan 角色的 assignable scope 是目标订阅，但角色分配 scope 只落在三个业务组，不等于授予订阅级密钥读取。它不获得 backend 资源组的 listKeys 权限，不获得管理面写入或业务 secret 数据角色。

AzureRM 创建 Key Vault 时查询同名 deleted vault，生产因此得到 `Microsoft.KeyVault/locations/deletedVaults/read` 和 `Microsoft.KeyVault/locations/operationResults/read`；不获得 purge/recover 写入。新资源名称避开旧软删除名称，provider 的 Key Vault 自动恢复关闭。

plan 因刷新计算属性会读取诊断密钥，仍属于敏感数据身份。production 环境审批保护生产身份及私钥，main Ruleset 保护工作流代码，二者必须同时保持。当前 production 由 `atea-shuangliang` 审批、禁止自行审批和管理员绕过，两个环境仅允许 main。

### 定位服务主体与 RBAC 成员

`gtsdrt-terraform-plan/test/prod` 是 Azure 应用的服务主体，供 GitHub Actions 使用。GitHub 的 `gtsdrt` 和 `atea-shuangliang` 是提交、发起运行及审批账号；Azure 资源权限分配给下表中的服务主体。

| Azure 身份 | Application / Client ID | Service Principal Object ID |
|---|---|---|
| `gtsdrt-terraform-plan` | `dd55a47c-10f7-494f-8ffd-b7905fe1d307` | `75231122-8601-45c7-90d2-8c0955d616eb` |
| `gtsdrt-terraform-test` | `52189b8e-a0c9-47b6-84c3-f3f86b3bd047` | `335dd3bc-d604-408b-983e-55a81f558482` |
| `gtsdrt-terraform-prod` | `a3d03a7c-0c1f-44bf-9ed3-4282114abe9b` | `20b490df-c599-472b-841d-5f450ece0d21` |

以上为 2026-10-06 核对的身份元数据，不是登录密钥。Client ID 配置在 GitHub repository variables；Azure RBAC 的 principal_id 使用 Service Principal Object ID。App registrations 的 Object ID 标识应用注册对象，与 Enterprise applications 中服务主体的 Object ID 不同。[Microsoft 对应用对象与服务主体的说明](https://learn.microsoft.com/en-us/entra/identity-platform/app-objects-and-service-principals)

Portal 中进入 **Microsoft Entra ID → Enterprise applications**，按名称定位服务主体。资源组授权入口是 **Access control (IAM) → Add → Add role assignment → Members → User, group, or service principal → Select members**；搜索 `gtsdrt-terraform-test` 等显示名称并选择。现有 Role assignments 列表的搜索只筛选已经授权的成员。托管身份选择入口不用于这三个应用。[Microsoft 的 RBAC 操作步骤](https://learn.microsoft.com/en-us/azure/role-based-access-control/role-assignments-portal)

| 资源组范围 | 接收权限的服务主体 | 角色 |
|---|---|---|
| `terraform-test-v2` | `gtsdrt-terraform-test` | Contributor |
| `terraform-prod-v2`、`azlandingzone-v2` | `gtsdrt-terraform-prod` | Contributor |
| 上述三个组 | `gtsdrt-terraform-plan` | foundation 定义的 `gtsdrt Terraform v2 Plan Reader <subscription_id>` |

已有普通 Reader 允许读取资源，但不包含 Storage listKeys/Log Analytics sharedKeys；foundation 的自定义 Plan Reader 包含这些刷新操作。test/prod 的 Contributor 授权不会改变 plan 身份的权限。foundation 同时管理生产的订阅范围 deleted-vault 元数据读取授权；三个 state 容器的数据权限由初始化脚本单独准备。

## 从零准备与 state

backend 资源组 `Terraform`、存储账户 `gtsdrtterraform2` 是现有管理基础设施，不放进业务 state。`prepare_fresh_start.py` 在核对订阅、租户、三个 Client ID 与 Object ID 后生成排除在 Git 外的本地输入。如果 v2 组和对应 Contributor 授权已存在，还生成本地 import 块，保留现有组区域；仅导入确定匹配的组、身份、角色和 scope，普通 Reader 或其他身份的授权保持原状。import 在管理员审查后的 foundation plan/apply 中执行。

管理员使用初始化脚本建立/保留私有容器并初始化缺失的 v2 文件：

| 层 | 文件 |
|---|---|
| foundation | `tfstate-foundation/foundation-v2.tfstate` |
| prod | `tfstate-prod/executor-v2.tfstate` |
| test | `tfstate-test/terraform-test-v2.tfstate` |

脚本只允许这三个新 key，先检查存在性，上传禁止覆盖；已有 v2 state 原样保留。旧 prod/test 文件和历史 tfstate 容器不下载、不复制、不覆盖。

初始化需临时的管理员账户级 blob 数据访问。只有脚本新建的临时角色会在 finally 中撤销，已有角色保留；管理员获得 foundation 容器范围的持久数据角色，工作流身份不授予 foundation 容器的数据权限。异常或进程被强制终止时仍应人工复核临时角色，避免遗留。

backend 使用 Entra ID，现有 state 账户的 Shared Key/匿名 Blob 访问保持禁用。prod/test 虽使用同一账户，但分属不同数据容器；本地切换新 backend 用 init -reconfigure，不迁移旧 state 到 v2。

## 计划、审批与日志

公开 artifact 仅包含 CMS AuthEnvelopedData 密文；AES-256-GCM 验证内容完整性，RSA 封装会话密钥。计划文档绑定仓库、提交、run ID、环境、deploy/destroy 目的、范围及 24 小时有效期，密文保留 3 天。

私钥仅存于各 environment 的 `TF_PLAN_PRIVATE_KEY`。解密、上下文、到期和摘要验证全部通过才写出 mode 0600 的计划。审批者审查的是相同计划的公开安全摘要，apply 执行保存计划，不重新生成计划。

摘要只接受有限枚举、布尔值、有界数字和端口；具体 IP、任意字符串、ID、标签、变量、输出、敏感及未知值隐藏。Terraform 原始输出不发布，统一执行器只报告固定错误类别，临时日志删除；必要的详细诊断由授权身份在受限位置复现。

PR 检查不获得 Azure 凭据、OIDC 或 state。foundation mock 测试验证新组名、角色动作和 scope，并纳入必需的 Security Checks；普通 validate 还覆盖三个根模块。

## 写入串行与资源生命周期

完整部署、销毁和恢复共用写入队列，queue:max 最多保留 100 个等待请求；生产审批等待也占用队列。plan-only 走独立只读队列，可能与写入并发，因此只是预览参考。apply 申请 state lease 并拒绝 state serial 已改变的计划；该校验不能替代云端漂移检查。

管理员运行 foundation 时必须暂停上述写入工作流并确保没有活动执行者，避免权限和 backend 初始化与正在执行的部署并发。

test apply 完成或失败后立即清理，测试与清理均成功才进入生产审批。独立恢复在运行结束后检查是否尝试过 test 部署，并每 6 小时兜底；只 checkout main、使用 test 身份与 test state。恢复不读取触发运行的代码或 artifact，并拒绝其他仓库事件。

Key Vault 保持 RBAC、默认拒绝网络及 purge protection；诊断 Storage 默认 Deny、仅放行 Azure 可信服务。普通业务身份的 secret 数据权限和私网访问需另行配置，test 仍只验证 network。

销毁保留 foundation 的组与授权；Key Vault/Log Analytics 不永久 purge。诊断 Storage 属于业务销毁范围，其归档日志不会因上述软删除策略得到保护。未来生产审计需要独立的长期存储生命周期。

## 历史配置

旧 `bootstrap_security.py prepare/retire` 创建身份、迁移旧 state 并撤销旧 executor 权限。v2 不调用它重新迁移历史 state，只复用其中的 CLI 包装函数；新初始化遵循 FRESH_START。

旧 workload 根模块的 removed/destroy=false 块仅为历史资源组地址兼容保留；它们不会从独立 foundation state 删除资源组。下一代再次从零重建时，必须同时选择新组名、generation、backend key 和初始化允许列表，避免两个 state 接管同一批活跃资源。
