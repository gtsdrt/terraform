# Terraform on GitHub Actions — 完整流程说明书

> 这份文档描述整个交付流程：代码怎么改、怎么验证、怎么部署到 Azure、怎么销毁，以及各环节涉及的凭据、状态与坑。
> 一次性配置的详细步骤、runbook 与扩展指南见 [README.md](../README.md)。

---

## 1. 架构总览

```
【本地改动】
   │  terraform fmt / validate / plan   （ARM_USE_CLI=true + az login，只读计划）
   ▼
【分支 + PR】─────────► pr-check.yml
   │                     ├─ fmt（仓库根，覆盖 modules/）→ init → validate → plan(tf, tf-test)
   │                     ├─ 计划贴成 PR 评论 + 上传 tfplan 产物
   │                     └─ plan 失败 → 检查真的会红（有失败门）
   ▼ 合并
【push main】─────────► deploy.yml
   ├─ plan (matrix: tf-test, tf)     只读；计划写进 job 摘要 + 上传 tfplan 产物
   ├─ deploy-test                    应用 tf-test 的产物 → 创建 13 个资源
   ├─ deploy-prod   🔒 environment: production
   │        └─ 审批通过后才签发 OIDC 令牌 → 应用 tf 的产物 → 创建 39 个资源
   └─ cleanup-test                   销毁 tf-test（仅当 prod 成功）

【手动触发（任意时刻）】
   ├─ deploy.yml  mode=plan-only     只跑 plan job，不碰任何资源（~1 分钟，无需审批）
   ├─ deploy.yml  mode=full          等同一次 push（+1 次 Approve）
   └─ destroy.yml env/scope/confirm  三道闸门 → terraform destroy → 软删除善后
```

设计要点：

- **一切通过 GitHub Actions 在 runner 上执行**，没有常驻服务、没有容器、没有长期密钥（部署侧走 OIDC）。
- **生产变更必须过审批**：`environment: production` 上的 required reviewer 决定 OIDC 令牌是否签发 —— 审批发生在拿凭据之前。
- **apply 用的永远是流水线早先 plan 出来的产物**，中途若有人改过 state，apply 会以「计划已过期」直接失败，而不是盲目覆盖。

---

## 2. 仓库结构

| 路径 | 角色 |
|---|---|
| `modules/network/` | VNet / 子网 / NSG / NSG 关联 —— **两个环境共用一份实现** |
| `modules/azlandingzone/` | hub VNet + 5 子网 + 3 NSG + Log Analytics + 存储 + Key Vault + 诊断设置 —— **仅生产调用** |
| `tf/` | 生产根模块：backend(`executor.tfstate`) + 变量 + module 调用 + `moved` 块 |
| `tf-test/` | 测试根模块：backend(`terraform-test.tfstate`) + 变量 + `network` module |
| `.github/workflows/deploy.yml` | push main / 手动触发（`mode`: full / plan-only） |
| `.github/workflows/pr-check.yml` | 仅 PR 触发：fmt / validate / plan + 评论 |
| `.github/workflows/destroy.yml` | 仅手动触发：一键销毁（`env` / `scope` / `confirm`） |
| `tf*/.terraform.lock.hcl` | 锁定 azurerm 版本（同时含 `linux_amd64` 与 `darwin_arm64`） |
| `docs/ARCHITECTURE.md` | 本文档 |

**约定**：资源只写在 `modules/` 下；环境目录只保留 backend、变量和 module 调用。这样加资源不会出现"两个环境各改一遍"的漏改问题。

---

## 3. 部署了什么

### `modules/network`（两个环境都部署，13 个资源）

| 资源 | 说明 |
|---|---|
| 资源组 | 生产 `terraform-prod` / 测试 `terraform-test` |
| VNet ×2 | `vnet-01` `172.16.0.0/16`、`vnet-02` `172.17.0.0/16`（`cidrsubnet("172.16.0.0/12", 4, i)`） |
| 子网 ×4 | 每个 VNet 两个：`subnet-01` `.0.0/24`、`subnet-02` `.1.0/24` |
| NSG ×2 | `nsg-01`、`nsg-02`，含 HTTPS 入站规则 |
| NSG 关联 ×4 | 每个 VNet 的 NSG 挂到该 VNet 的所有子网（**不关联 NSG 等于没生效**） |

### `modules/azlandingzone`（仅生产，26 个资源，资源组 `azlandingzone`）

| 组件 | 内容 |
|---|---|
| hub VNet | `vnet-azlz-hub` = `10.0.0.0/16` |
| 子网 ×5 | `GatewaySubnet` `10.0.0.0/27`、`AzureFirewallSubnet` `10.0.0.64/26`（两个保留名，为网关/防火墙预留地址）、`Management` `10.0.1.0/24`、`Shared` `10.0.2.0/24`、`Workload` `10.0.3.0/24` |
| NSG ×3 + 规则 ×9 | Management / Shared / Workload 各一个（两个保留子网按 Azure 建议不挂 NSG），每个含三条基线入站规则：`allow-vnet-inbound` 100、`allow-lb-inbound` 110、`deny-internet-inbound` 4096 |
| 集中日志 | Log Analytics 工作区 `log-azlz-<hash>`（保留 30 天） |
| 审计落地 | 存储账户 `stazlz<hash>`（TLS 1.2、禁止公共 blob） |
| 密钥管理 | Key Vault `kv-azlz-<hash>`（RBAC 授权、网络默认拒绝、软删除 7 天、未开清除保护） |
| 诊断 | Key Vault 的 `AuditEvent` + `AllMetrics` → Log Analytics + 存储账户 |

**`<hash>` 的由来**：Key Vault 与存储账户的名字必须全局唯一，所以用 `sha256(资源组名 + name_prefix)` 的前 6 位生成稳定后缀 —— 不需要引入 `random` provider，也不会每次 plan 都变。可用 `key_vault_name` / `storage_account_name` 覆盖，用 `vnet_address_space` 换地址空间，用 `name_prefix` 换前缀。

**刻意不含**（按需再加）：Azure Firewall（约 $900/月）、Bastion（约 $140/月）、VPN/ExpressRoute 网关、DDoS 防护计划、Private DNS / 私有终结点、NSG/VNet flow logs、Policy 与管理组（后者需要租户级权限）。

---

## 4. 认证与权限

```
GitHub Actions job (id-token: write)
  └─ OIDC 令牌，sub = repo:gtsdrt@75382482/terraform@1343500781:ref:refs/heads/main
                    （绑了 environment 的 job 则是 ...:environment:production）
       └─ Azure AD 联邦凭据（App Registration: shuang.liang-terraform-github-action）
            └─ 服务主体（订阅级 Contributor）
                 ├─ 读写 state 存储账户 gtsdrtterraform2 的 tfstate 容器
                 └─ 创建 / 删除 Azure 资源
```

- **只有 `deploy-prod` 与 destroy 绑 `environment: production`**，因此只有它们能拿到 `environment:production` 这个 subject 的令牌，审批通过前拿不到 —— 「main 分支凭据」与「生产凭据」是两个不同的信任边界。
- **`pr-check.yml` 例外，继续使用 client secret**：`pull_request` 触发的 workflow 内容来自 PR 本身，如果在 PR 流程签发 OIDC 令牌，fork PR 就能改 workflow 去领 Azure 令牌。secret 对 fork PR 不下发，天然 fail-closed。
- `pr-check` 可平滑升级为只读身份：加上 `TERRAFORM_PLAN_AZURE_CLIENT_ID` / `_CLIENT_SECRET` 两个 secret 即自动切换（见 README runbook）。
- 仓库 secrets：`TERRAFORMEXECUTOR_AZURE_{CLIENT_ID,CLIENT_SECRET,SUBSCRIPTION_ID,TENANT_ID}`（名字带历史前缀，功能上仍在用）。

---

## 5. 状态与并发

- 两个环境的 state 都存在存储账户 `gtsdrtterraform2` 的 `tfstate` 容器（资源组 `Terraform`），靠 key 隔离：生产 `executor.tfstate`（**历史遗留名，不要直接改**，迁移见 README runbook）、测试 `terraform-test.tfstate`。
- 三条流水线共用 `concurrency: terraform-deploy`（`cancel-in-progress: false`）：不会并发 apply，也不会边部署边销毁。
- 所有 terraform 命令带 `-lock-timeout=5m`。
- **apply 依赖上游 plan 产物** ⇒ 两次 `init` 必须拿到同一 provider 版本 ⇒ 所以 `.terraform.lock.hcl` 必须提交，且要包含 `linux_amd64`（CI）与 `darwin_arm64`（本地）两个平台的哈希。

---

## 6. 操作流程

### 6.1 日常开发循环

```bash
az login                                   # 本地用 CLI 身份即可
export ARM_USE_CLI=true
export ARM_SUBSCRIPTION_ID=<订阅ID>
export ARM_TENANT_ID=<租户ID>

cd tf-test                                 # 先拿 test 验证更安全
terraform fmt -recursive
terraform init -input=false
terraform validate
terraform plan -var-file=terraform.tfvars  # 确认要建/改/删什么，尤其别出现 destroy
```

然后开分支 → push → 开 PR（`pr-check` 自动跑 fmt/validate/plan 并贴出结果）→ 合并。

### 6.2 自动部署（push main）

触发条件：改动命中 `tf/**`、`tf-test/**`、`modules/**`。

```
plan(tf-test, tf)  →  deploy-test  →  deploy-prod（等你 Approve）  →  cleanup-test
```

预期结果：test 环境 13 个资源、生产 39 个资源，随后 test 被销毁。`deploy-prod` 的 job 摘要里能看到完整计划（审批前就该看这个）。

### 6.3 手动部署 / 只预览

```bash
gh workflow run deploy.yml --ref main -f mode=plan-only   # 只预览（无需审批，不碰资源）
gh workflow run deploy.yml --ref main -f mode=full        # 完整部署（+1 次 Approve）
```

也可在网页 Actions → Terraform Deploy → Run workflow 下拉选 `mode`。

> ⚠️ `workflow_dispatch` 可以在任意分支上跑，但 backend 的 state key 是固定的 —— **在非 main 分支跑 `full` 等于把那个分支的代码直接 apply 到真实 state**。分支试跑请用 `mode=plan-only` 或走 PR。

### 6.4 一键销毁

```bash
# 全部销毁（演示网络 + landing zone，两个资源组都会没）
gh workflow run destroy.yml --ref main -f env=tf -f scope=all -f confirm=DESTROY

# 只删 landing zone / 只删演示网络
gh workflow run destroy.yml --ref main -f env=tf -f scope=azlandingzone -f confirm=DESTROY
gh workflow run destroy.yml --ref main -f env=tf -f scope=network -f confirm=DESTROY
```

三道闸门：**手填确认串 `DESTROY`** → **`production` 审批** → **与部署共用 concurrency 锁**。

销毁后自动善后（见第 8 节的两个软删除坑），结果写入 job 摘要（env / scope / Key Vault / Log Analytics）。

### 6.5 回滚

- **代码回滚**：直接 revert 对应提交；`moved` 块与资源地址不匹配时先本地 `plan` 确认，避免意外的 destroy+create。
- **环境回滚**：`destroy.yml` 只删 → `deploy.yml mode=full` 重建（可反复来回做，已实测）。

---

## 7. 一次性配置清单（已完成）

1. **Azure OIDC 联邦凭据**（2 条，subject 必须完全一致）
   - `repo:<owner>@<id>/<repo>@<id>:ref:refs/heads/main` → plan / deploy-test / cleanup-test
   - `repo:<owner>@<id>/<repo>@<id>:environment:production` → deploy-prod / destroy
   - 注意：新仓库的 sub 里带 **owner/repo 的不可变数字 ID**，用 `gh api repos/<org>/<repo> --jq '"repo:\(.owner.login)@\(.owner.id)/\(.name)@\(.id)"'` 推导。
2. **`production` environment 的 required reviewer**（这是唯一的审批闸门，配置在 GitHub 仓库/组织设置里，不在代码里）。
3. **提交 `.terraform.lock.hcl`**（两个环境、两个平台）。
4. **清理孤儿凭据**：`TERRAFORM_EXECUTOR_API_KEY`（secret）与 `TERRAFORM_EXECUTOR_URL`（variable）已不存在；如再出现请删除。

---

## 8. 坑与对策（本仓库已固化）

| # | 坑 | 症状 | 对策 |
|---|---|---|---|
| 1 | OIDC subject 带**不可变数字 ID** | `AADSTS700213: No matching federated identity record` | 用 `gh api` 推导 `PREFIX` 建凭据；**报错信息里会原样打印真实 assertion，照抄即可** |
| 2 | 跨模块搬迁资源 | 已有资源被 destroy + create | 用 `moved` 块（本仓库曾把 13 个资源零重建迁进 `modules/network`，实测 `0 to add, 0 to change, 0 to destroy`） |
| 3 | 改 `modules/` 不触发部署 | 改了资源实现却没生效 | workflow `paths` 必须包含 `modules/**` |
| 4 | step 排在 `actions/checkout` 之前却继承 `working-directory` | runner 报 `No such file or directory`，job 立即失败 | 该步骤显式 `working-directory: .` |
| 5 | plan 用了 `continue-on-error` | 检查永远是绿的，形同虚设 | 末尾加"plan 失败则失败"的门 |
| 6 | **Key Vault 软删除**（名字占用 7 天） | 误以为必须人工 purge | 实测：vault 随资源组一起删除时已被**永久删除**，`purge` 会返回 `DeletedVaultNotFound` → 识别该错误 = 无需 purge |
| 7 | **Log Analytics 软删除**（名字占用 14 天） | 销毁后重建同名工作区失败 | 自动执行「创建临时 RG → `recover` → `delete --force`（永久删除）→ 删除临时 RG」释放名字（官方文档明确这就是 dev/test 重复部署同名工作区的推荐做法） |
| 8 | apply 依赖上游 plan 产物 | provider 版本不一致导致 apply 失败 | 提交 lock 文件（两个平台），升级 provider 时 `init -upgrade` + `providers lock` 后重新提交 |
| 9 | 新增变量没写 `default` | CI 报 `No value for required variable` | 两个 `terraform.tfvars` 都要给值，或给变量默认值 |
| 10 | 内联 `security_rule` | azurerm 4.x 已弃用，升 5.x 会移除 | 改用独立的 `azurerm_network_security_rule` |
| 11 | 改资源地址 | 悄悄 destroy + create | 改 `count` ↔ `for_each`、改 `for_each` 的 key 计算方式、跨模块搬动前，先本地 `plan` 确认 `0 to destroy` |
| 12 | 敏感值 | 会进仓库与 state | 不要放 `terraform.tfvars`；用 GitHub Secrets + 在 workflow 里注入 `TF_VAR_xxx` |
| 13 | 并发 apply | 争抢 state（backend lease 冲突） | `concurrency` + `-lock-timeout=5m` |

---

## 9. 常用命令速查

```bash
# 只预览（不碰资源）
gh workflow run deploy.yml --ref main -f mode=plan-only
# 完整部署（+1 次 Approve）
gh workflow run deploy.yml --ref main -f mode=full
# 一键销毁（+1 次 Approve）
gh workflow run destroy.yml --ref main -f env=tf -f scope=all -f confirm=DESTROY

# 看运行 / 看日志
gh run list --workflow deploy.yml --limit 3
gh run view <run-id> --log
gh pr checks <PR号>

# 本地校验
terraform fmt -check -recursive
terraform validate
terraform plan -var-file=terraform.tfvars
```

---

## 10. 演进历史（2026-09-24）

| 阶段 | 变化 |
|---|---|
| 起点 | 仓库里有一套已弃用的"Terraform 执行器"（FastAPI + Dockerfile + Azure Container App），Azure 侧已无该容器，代码零引用 |
| 收敛 | **删除执行器代码**；CI 成为唯一交付路径；补 OIDC、审批闸门、`concurrency`、PR 检查失败门、`.gitignore` 与 lock 文件 |
| 修基础设施 | **NSG 真正关联到子网**（此前建了 NSG 却没挂载，等于不生效） |
| 结构重构 | 抽出 `modules/network`，用 `moved` 块把 13 个已有资源**零重建**迁入；CI `paths` 与 fmt 检查覆盖 `modules/` |
| 新增能力 | `modules/azlandingzone`（26 个资源）在生产部署；`deploy.yml` 支持手动触发与 `plan-only` 预览；新增 `destroy.yml` 一键销毁 |
| 实测修正 | destroy 的工作目录顺序 bug；Key Vault 与 Log Analytics 两个软删除坑的处理（含真实验证：销毁 39 → 重建 39 → 再销毁 39，无残留） |

---

## 11. 当前环境状态与后续方向

**当前状态**（截至本文档编写时）：`terraform-prod` 与 `azlandingzone` 均已销毁、无软删除残留，Azure 上只保留订阅原有的资源组；重新部署一条命令即可。

**可选后续**：

1. 增加组件：Azure Firewall、Bastion、Private DNS / 私有终结点、VNet flow logs。
2. 把 landing zone 拆成**独立 state + 独立流水线**，进一步隔离爆炸半径。
3. `pr-check` 切到只读身份；收窄 CI 身份的 RBAC（目前是订阅级 Contributor）。
4. 按需调整 `production` 的 required reviewer（当前每次部署/销毁都需人工点一次 Approve）。
5. 增加 staging 等更多环境（步骤见 README「如何扩展」）。
