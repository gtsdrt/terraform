# Terraform Azure 网络部署

push 到 `main` 触发 GitHub Actions，在 runner 上直接跑 Terraform，把 Azure 网络资源（资源组 / VNet / 子网 / NSG）部署到两个环境。

## 目录

| 路径 | 作用 |
|---|---|
| `tf/` | 生产环境，state key `executor.tfstate`（名字是历史遗留，见下方备注） |
| `tf-test/` | 测试环境，state key `terraform-test.tfstate` |
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
- `tf/main.tf` 与 `tf-test/main.tf` 目前逐字节相同。后续如果继续加资源，建议抽成 module，避免两处同步。
- 仓库设置里的 `TERRAFORMEXECUTOR_AZURE_*` 这组名字带 executor 前缀，是历史命名，功能上仍在用（`deploy.yml` / `pr-check.yml`）。
