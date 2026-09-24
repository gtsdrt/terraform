# Terraform Azure 网络部署

push 到 `main` 触发 GitHub Actions，在 runner 上直接跑 Terraform：生产环境部署网络资源 + 一套 landing zone，测试环境部署网络资源用于预检。

> 📖 **完整流程说明书**（架构总览 / 资源清单 / 认证与权限 / 状态与并发 / 五条操作流程 / 坑与对策 / 演进历史）见 **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**。

## 目录

| 路径 | 作用 |
|---|---|
| `modules/network/` | 网络资源的**唯一实现**（RG / VNet / 子网 / NSG / NSG 关联），两个环境共用 |
| `modules/azlandingzone/` | 标准 landing zone（hub VNet / 多层子网 / NSG / Log Analytics / 存储 / Key Vault / 诊断），只由生产环境调用 |
| `tf/` | 生产环境的根模块：backend + 变量 + module 调用，state key `executor.tfstate`（名字是历史遗留，见下方备注） |
| `tf-test/` | 测试环境的根模块，state key `terraform-test.tfstate` |
| `.github/workflows/deploy.yml` | push main / 手动触发：plan → apply test → apply prod（需审批）→ destroy test |
| `.github/workflows/pr-check.yml` | PR 触发：fmt / validate / plan + 贴评论，plan 失败则检查不通过 |

两个环境的 state 都存在存储账户 `gtsdrtterraform2` 的 `tfstate` 容器里（资源组 `terraform`），靠不同的 key 隔离。

## 部署流程

```
push main
  └─ plan        (matrix: tf-test, tf)  只读，tfplan 上传为产物 + 写进 job 摘要
       └─ deploy-test                               应用 tf-test 的 plan
            └─ deploy-prod (environment: production)  ← 审批通过后才签发 OIDC 令牌
                 └─ cleanup-test  destroy tf-test（只有 prod 成功才跑）
```

**审批闸门来自 GitHub 仓库设置，不在代码里**，必须配置一次（见下）。

### 手动触发部署（含"只预览"模式）

`deploy.yml` 支持 `workflow_dispatch`，三种触发方式：

```bash
# 网页：Actions → Terraform Deploy → Run workflow（可下拉选 mode）
gh workflow run deploy.yml --ref main                     # full：完整部署
gh workflow run deploy.yml --ref main -f mode=plan-only   # plan-only：只预览
gh api -X POST repos/gtsdrt/terraform/actions/workflows/deploy.yml/dispatches \
  -f ref=main -f 'inputs[mode]=plan-only'
```

| `mode` | 行为 |
|---|---|
| `full`（默认） | 与 push 完全一致：plan → 建 test 环境 → **等审批** → 建 prod → 销毁 test |
| `plan-only` | 只跑 `plan` job（只读）：两个环境的计划写进 job 摘要并上传 `tfplan` 产物，**三个 apply / cleanup job 全部跳过**，不碰任何资源 |

push 触发时没有 `mode` 参数，会被视为 `full`。

⚠️ **分支要选对**：`workflow_dispatch` 可以在任意分支上跑，但 `backend.tf` 里的 state key 是固定的（`executor.tfstate` / `terraform-test.tfstate`），所以**在非 main 分支上跑 `full` 等于把那个分支的代码直接 apply 到真实的生产/测试 state 上**。想试分支就用 `-f mode=plan-only`，或者走 PR（`pr-check` 会自动 plan）。

> `pr-check.yml` 只能由 PR 触发，不能手动运行。

### 一键销毁（`destroy.yml`）

破坏性操作**只允许手动触发**，并且有三道闸门：**手填确认串 `DESTROY`** → **`production` 环境审批** → **与部署共用 concurrency 锁**（不会边部署边销毁）。

```bash
# 全部销毁（tf：演示网络 + landing zone，两个资源组都会没）
gh workflow run destroy.yml --ref main -f env=tf -f scope=all -f confirm=DESTROY

# 只删 landing zone（保留演示网络）
gh workflow run destroy.yml --ref main -f env=tf -f scope=azlandingzone -f confirm=DESTROY

# 只删演示网络（保留 landing zone）
gh workflow run destroy.yml --ref main -f env=tf -f scope=network -f confirm=DESTROY
```

| 输入 | 说明 |
|---|---|
| `env` | `tf`（生产，含 landing zone）或 `tf-test`（测试，通常已被 `cleanup-test` 清空） |
| `scope` | `all` = 整个 state；`azlandingzone` / `network` = 只删该模块（**仅 `tf` 有效**，内部用 `terraform destroy -target`） |
| `confirm` | 必须精确是 `DESTROY`，否则第一步就失败 |

销毁结束后结果会写进 job 摘要（env / scope / Key Vault 名）。

> ⚠️ **两个软删除坑，workflow 已自动处理**（否则"销毁 → 重建"会失败）：
> - **Key Vault（名字占用 7 天）**：销毁前记录名字，销毁后自动 `az keyvault purge`。如果 vault 是随资源组一起被删的（已被永久删除），会识别成"无需 purge"并跳过。
> - **Log Analytics（名字占用 14 天）**：删除后工作区进入软删除、名字不释放。按微软文档的做法，销毁后自动执行 `创建临时 RG → recover 工作区 → delete --force（永久删除，释放名字）→ 删除临时 RG`。
>
> 两个步骤都做了幂等判断；如果自动处理失败，日志与 job 摘要里会打印需要手动执行的完整命令（`az monitor log-analytics workspace recover` + `delete --force`）。

## 一次性配置清单

### 1. Azure：为 GitHub Actions 配 OIDC 联邦凭据

`deploy.yml` 用 OIDC，不再使用 `TERRAFORMEXECUTOR_AZURE_CLIENT_SECRET`。给对应的 App Registration / 托管身份加两条 federated credential，subject 必须与 GitHub 实际签发的 assertion **完全一致**：

| 用途 | subject |
|---|---|
| plan / deploy-test / cleanup-test（push 到 main，无 environment） | `<PREFIX>:ref:refs/heads/main` |
| deploy-prod（绑了 `production` environment） | `<PREFIX>:environment:production` |

> ⚠️ **`<PREFIX>` 里带不可变数字 ID**：新创建的仓库，GitHub 签发的 subject 形如
> `repo:gtsdrt@75382482/terraform@1343500781`（owner/repo 后面带各自数字 ID），
> 而不是 `repo:gtsdrt/terraform`。按老格式配会报
> `AADSTS700213: No matching federated identity record found for presented assertion subject ...`
> —— **该错误信息里会原样打印真实的 assertion，照抄它建凭据即可**。

```bash
APP_ID=<App Registration 的对象ID(不是 clientId)>   # az ad app show --id <clientId> --query id -o tsv
PREFIX=$(gh api repos/<org>/<repo> --jq '"repo:\(.owner.login)@\(.owner.id)/\(.name)@\(.id)"')
echo "$PREFIX"   # 例如 repo:gtsdrt@75382482/terraform@1343500781

for SUBJ in "${PREFIX}:ref:refs/heads/main" \
            "${PREFIX}:environment:production"; do
  az ad app federated-credential create --id "$APP_ID" --parameters "{
    \"name\": \"gh-${SUBJ##*:}\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"$SUBJ\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }"
done
```

> 只有 `deploy-prod` 绑了 `environment: production`，因为 OIDC 的 subject 会带上环境名。这样「main 分支凭据」和「生产凭据」是两个不同的信任边界：能 push main 不等于是能上生产。

### 2. GitHub：让 prod 审批真正生效

Settings → Environments：

- `production`：勾选 **Required reviewers**（加至少 1 人）。这样 prod 的 `terraform apply` 不会随 push 自动执行。

### 3. GitHub：清理已废弃执行器的凭据

历史上这套仓库里有一个 `terraform-executor` 容器（FastAPI + Terraform，见 git 历史，已删除）。下面两个已无任何 workflow 引用，建议删除；如果那把 API Key 还能找到对应主体，顺便在 Azure 侧也吊销：

```bash
gh secret delete TERRAFORM_EXECUTOR_API_KEY
gh variable delete TERRAFORM_EXECUTOR_URL
```

### 4. 提交 provider 锁定文件（保证 CI 可复现）

`deploy.yml` 里 apply 的是「上一个 job 生成的 plan 产物」，所以两次 `init` 必须拿到**同一个 provider 版本**。本仓库目前还没有 `.terraform.lock.hcl`，请本地执行一次并提交：

```bash
cd tf      && terraform init -input=false && cd ..
cd tf-test && terraform init -input=false && cd ..
git add tf/.terraform.lock.hcl tf-test/.terraform.lock.hcl
git commit -m "chore: commit terraform provider lock files"
```

（`.gitignore` 已刻意**不**忽略这两个文件，但请确保用的是 `terraform init` 而不是把 `.terraform/` 目录也提交上去。）

## 本地检查

```bash
cd tf      # 或 tf-test
terraform fmt -check -recursive
terraform init -input=false
terraform validate
terraform plan -var-file=terraform.tfvars
```

本地跑 plan 需要能访问 state 存储账户与 Azure management API（`az login` 后设置 `ARM_*` 环境变量，或先用 `ARM_USE_OIDC`/`ARM_USE_CLI`）。

## Landing Zone（`modules/azlandingzone`）

由**生产环境**调用（`tf/main.tf` 里的 `module "azlandingzone"`），在资源组 **`azlandingzone`** 里创建一套精简的标准 landing zone（共 26 个资源）：

| 组件 | 内容 |
|---|---|
| hub VNet | `vnet-azlz-hub` = `10.0.0.0/16` |
| 子网 | `GatewaySubnet` `10.0.0.0/27`、`AzureFirewallSubnet` `10.0.0.64/26`、`Management` `10.0.1.0/24`、`Shared` `10.0.2.0/24`、`Workload` `10.0.3.0/24` |
| NSG | Management / Shared / Workload 各一个（前两个保留子网按 Azure 建议不挂 NSG），每个含 3 条基线入站规则：允许 VNet、允许 LB 健康探测、显式拒绝 Internet |
| 集中日志 | Log Analytics 工作区 `log-azlz-<hash>`（保留 30 天） |
| 审计落地 | 存储账户 `stazlz<hash>`（TLS 1.2、禁止公共 blob 访问） |
| 密钥管理 | Key Vault `kv-azlz-<hash>`（RBAC 授权、网络默认拒绝、软删除 7 天、关闭清除保护） |
| 诊断 | Key Vault 的 `AuditEvent` + `AllMetrics` 同时送 Log Analytics 和存储账户 |

**关于 `<hash>`**：Key Vault 与存储账户的名字必须全局唯一，所以用 `sha256(资源组名 + name_prefix)` 的前 6 位生成稳定后缀（不会每次 plan 都变，也不需要额外的 random provider）。想用固定名字就传 `key_vault_name` / `storage_account_name`；想换地址空间就传 `vnet_address_space`。

**成本**：VNet / 子网 / NSG / 规则 / 关联全部免费；Log Analytics 按摄入量、存储账户按量、Key Vault 按操作计费（正常情况下每月几十美分级别）。
**刻意不含**：Azure Firewall（约 $900/月）、Bastion（约 $140/月）、VPN/ExpressRoute 网关、DDoS 防护计划、Private DNS/私有终结点、NSG/VNet flow logs、Policy 与管理组（后者需要租户级权限）。要加哪个说一声。

**为什么只在生产环境**：资源组名固定为 `azlandingzone`，两个环境同时部署会互相抢；且 Key Vault 删除后会进入软删除状态、名字继续被占用，而 `tf-test` 是"每次 push 都建了又删"的临时环境，第二次 apply 就会因为名字被占用而失败。要在 test 里也验证的话，需要额外加一步 `az keyvault purge`。

> 因此 `tf/main.tf` 与 `tf-test/main.tf` **不再逐字节相同**：test 环境只覆盖 `modules/network`，landing zone 的改动靠 PR 里的 plan 预检。

## 如何扩展（加资源 / 加环境）

### 加一类资源、加变量

资源写在 `modules/` 下的模块里（环境目录只保留 backend、变量和 module 调用）。注意 `modules/network/` 被**两个环境**共用，`modules/azlandingzone/` **只被生产环境**调用：

| 你要改的东西 | 改哪里 |
|---|---|
| 网络相关新资源 | `modules/network/main.tf`（两个环境同时生效） |
| landing zone 新资源 | `modules/azlandingzone/main.tf`（只影响生产） |
| 新的输入 | 对应模块的 `variables.tf` + 在 `tf/main.tf`（和 `tf-test/main.tf`，若两个环境都调用）的 module 块里传值 |
| 新的输出 | 对应模块的 `outputs.tf`（需要暴露给外部时再加根模块的 `output`） |
| 只属于某个环境的值 | 各自的 `terraform.tfvars` |
| 敏感值 | ❌ 不要放 `terraform.tfvars`（已提交进 git）：用 GitHub Secrets，在 workflow 里注入 `TF_VAR_xxx` |

**不需要动 `backend.tf` 和 workflow**（除非引入新 provider 或新环境）。改完本地先验证：

```bash
export ARM_USE_CLI=true                 # 复用 az login 的凭据
export ARM_SUBSCRIPTION_ID=<订阅ID>
export ARM_TENANT_ID=<租户ID>

cd tf-test                              # 先拿 test 验证更安全
terraform fmt -recursive
terraform init -input=false
terraform validate
terraform plan -var-file=terraform.tfvars   # 确认要建/改/删什么，尤其别出现 destroy
```

然后开分支 → push → 开 PR（`pr-check` 会自动跑 plan 并贴出结果）→ 合并 → 自动部署。

### 引入新的 provider（random / tls / azuread / azapi …）

两个 `providers.tf` 的 `required_providers` 都要加，然后**重新生成并提交两个 lock 文件**（两个平台都要，否则本地或 CI 会报校验和不匹配）：

```bash
terraform init -upgrade
terraform providers lock -platform=linux_amd64 -platform=darwin_arm64
git add tf/.terraform.lock.hcl tf-test/.terraform.lock.hcl
```

### 新增一个环境（例如 staging）

1. 复制 `tf-test/` 为 `staging/`，改 `backend.tf` 的 `key`（如 `staging.tfstate`）和 `terraform.tfvars`
2. `deploy.yml` 的 `plan` job 把 `staging` 加进 matrix，并补对应的 apply / cleanup job
3. 如果该 job 绑了 `environment: staging`，要再加一条联邦凭据，subject 形如 `repo:<owner>@<id>/<repo>@<id>:environment:staging`（`<owner>@<id>/<repo>@<id>` 用本文档第 1 节的 PREFIX 推导命令得到）
4. `pr-check.yml` 的 matrix 也加上 `staging`

### ⚠️ 四个坑

1. **改资源地址 = 删了重建**。这些操作都会改变资源地址，plan 里会出现 `-/+ destroy and then create`：
   - `count` 与 `for_each` 互改
   - 改 `for_each` 的 key 计算方式（例如改 `local.subnet_defs` 的 `"${i}-${j}"`）
   - 把资源在根模块与 module 之间搬动

   跨模块搬动**必须写 `moved` 块**（`tf/main.tf` 末尾就是现成例子：本仓库正是靠它把 13 个已有资源零重建迁进 `modules/network` 的）。任何改动合入前，本地 `plan` 都要确认 `0 to destroy`。
2. **变量必须两边都有值**。新变量若没有 `default`，两个 `terraform.tfvars` 都要写，否则 CI 报 `No value for required variable`。
3. **NSG 规则用独立的 `azurerm_network_security_rule`**，内联 `security_rule` 块在 azurerm 4.x 已弃用（升级 5.x 会移除）。
4. **`deploy.yml` 只监听 `tf/**`、`tf-test/**`、`modules/**`**。改 README 或 workflow 本身不会触发部署，需要手动 `gh workflow run deploy.yml`；以后若新增别的资源目录，记得同步加进 paths。

## Runbook：把 state key 从 `executor.tfstate` 改名

`tf/backend.tf` 里的 `key = "executor.tfstate"` 是历史遗留命名。**不要只改配置里的字符串**——Terraform 会把新 key 当成空 state，然后试图重建已经存在的资源并报错。正确顺序是先复制 state blob，再改配置：

```bash
SA=gtsdrtterraform2; C=tfstate

# 0) 确认当前没有 apply 在跑（本流水线有 concurrency，手动操作时也要自己确认）
# 1) 复制 state blob：lineage / serial 原样保留，所以不需要 terraform state push/migrate
az storage blob copy start --auth-mode login --account-name $SA \
  --destination-container $C --destination-blob prod.tfstate \
  --source-uri "https://$SA.blob.core.windows.net/$C/executor.tfstate"

# 2) 等复制完成（status 变成 success）
az storage blob show --auth-mode login --account-name $SA -c $C -n prod.tfstate --query 'properties.copy.status' -o tsv

# 3) 改 tf/backend.tf: key = "prod.tfstate"，然后本地验证「应该没有变化」
cd tf && terraform init -input=false && terraform plan -var-file=terraform.tfvars

# 4) 确认 plan 是 No changes 后再删掉旧 blob
az storage blob delete --auth-mode login --account-name $SA -c $C -n executor.tfstate
```

> 如果第 1 步报 403：同账户复制本来不需要 SAS，但失败的存储账户多半禁用了共享密钥/公共访问，此时改用 `az storage blob generate-sas` 给源 blob 生成一个临时 SAS 再拼到 `--source-uri` 上。改 key 之前请确认没有其它系统还在读旧 key。

## Runbook（可选）：给 PR 检查换只读凭据

`pr-check.yml` 现在优先用 `TERRAFORM_PLAN_AZURE_CLIENT_ID` / `TERRAFORM_PLAN_AZURE_CLIENT_SECRET`，没建就自动回退到现有的 `TERRAFORMEXECUTOR_AZURE_*`，所以**可以随时做、不会打断 PR 流程**；两个新 secret 一加上就自动切换，之后即可删掉 `TERRAFORMEXECUTOR_AZURE_CLIENT_SECRET`。

```bash
# 1) 新建只用于 plan 的身份
APP=$(az ad app create --display-name terraform-plan-readonly --query appId -o tsv)
az ad sp create --id "$APP" -o none
SECRET=$(az ad app credential reset --id "$APP" --append --display-name gh-plan --years 1 --query password -o tsv)

# 2) 授权：先给目标资源组的读权限（比订阅级 Reader 更小）
for RG in terraform terraform-prod terraform-test; do
  az role assignment create --assignee "$APP" --role Reader \
    --scope "/subscriptions/<SUBSCRIPTION_ID>/resourceGroups/$RG"
done

# 3) state 存储的权限：backend 目前用 account key 认证，所以 plan 也需要拿到 key
az role assignment create --assignee "$APP" --role "Storage Account Contributor" \
  --scope "$(az storage account show -n gtsdrtterraform2 -g terraform --query id -o tsv)"

# 4) 写进 GitHub secrets
gh secret set TERRAFORM_PLAN_AZURE_CLIENT_ID --body "$APP"
gh secret set TERRAFORM_PLAN_AZURE_CLIENT_SECRET --body "$SECRET"
```

关于"只读"的边界（别被名字骗了）：

- `terraform plan` 默认要对 state **加锁**（acquire lease），并且 backend 用 account key 认证，所以这个身份仍然需要 `listKeys` 级别的权限，看起来并不"纯只读"。
- 想要真正的数据面只读，需要给两个环境都加 `use_azuread_auth = true`，并把 `Storage Blob Data Contributor` 同时授给 **plan 身份和 deploy 的 OIDC 身份**（后者现在很可能没有数据面权限，只改一边会让 deploy 直接失败）。这是更大的改动，建议单独做。
- 最实际的收益是：PR 流程用的身份不再和"能改生产"的身份是同一个。

## 备注：遗留细节

- NSG 已通过 `azurerm_subnet_network_security_group_association` 关联到各 VNet 下的子网（此前建了 NSG 却没挂载，等于不生效）。注意 `azurerm_network_security_group` 里的**内联 `security_rule` 块在 azurerm 4.x 已标记弃用**，将来升级 5.x 前建议迁到独立的 `azurerm_network_security_rule`。
- `pr-check.yml` 会**跳过 fork PR**（拿不到 secret，跑了也是红）。如果你的分支保护把这个检查设成必需、且 PR 一直停在 Expected，就把它改成"fork 场景下正常退出"的写法。
- state 文件里可能包含明文敏感值（例如将来的密码类资源），该存储账户的访问范围应与其敏感度匹配。
- CI 里传递的 `tfplan` 产物同样可能含明文敏感值（它内嵌了变量值），因此只保留 1 天；如果仓库将来变成 public，需要改成不落盘的方式（或在 plan 时用 `-json` 提取摘要）。
- `deploy.yml` 里 apply 用的永远是这个流水线早先 plan 出来的那份产物，所以如果中途有别人改过 state，apply 会以「计划已过期」直接失败，而不是盲目覆盖。
- `tf/` 与 `tf-test/` 的根模块现在不再逐字节相同：两者都调用 `modules/network`，但只有 `tf/` 调用 `modules/azlandingzone`（原因见上方 Landing Zone 章节）。新增的网络资源记得让两个环境共用同一模块，避免又出现两处同步。
- 仓库设置里的 `TERRAFORMEXECUTOR_AZURE_*` 这组名字带 executor 前缀，是历史命名，功能上仍在用（`deploy.yml` / `pr-check.yml`）。
