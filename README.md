# Terraform on GitHub Actions

Azure 网络与 Landing Zone，使用 GitHub OIDC、生产环境审批和加密计划产物。
详细权限模型和迁移步骤见 [安全架构](docs/ARCHITECTURE.md)。

## 环境与资源

| 环境 | 资源组 | Terraform state | Azure 身份 |
|---|---|---|---|
| prod (`tf/`) | `terraform-prod`、`azlandingzone` | `tfstate-prod/executor.tfstate` | `gtsdrt-terraform-prod` |
| test (`tf-test/`) | `terraform-test` | `tfstate-test/terraform-test.tfstate` | `gtsdrt-terraform-test` |
| plan | 只读上述三个资源组及两个 state 容器 | 不写入、不申请 lease | `gtsdrt-terraform-plan` |

资源组由管理员一次性创建并保留，作为 RBAC 边界；Terraform 仅管理组内资源。
`removed` 块把历史上的资源组地址从 state 中移除，`destroy = false` 保证迁移不删除资源组。
网络模块创建两个 VNet、四个子网、两个 NSG、两条独立的 HTTPS 入站规则和四个关联（14 个资源）。
生产还创建 Landing Zone 的 39 个资源，合计 53 个；资源组不计入 managed resources。
网络模块的 HTTPS 入站只接受 `VirtualNetwork` 来源。

Landing Zone 的三层网络策略：

- **入站按子网矩阵分段**：`Management ← GatewaySubnet`、`Shared ← Management/Workload`、`Workload ← Shared`，源使用具体子网 CIDR 而非整张 `VirtualNetwork`，避免同 VNet 内横向无隔离。
- **出站默认拒绝 Internet**，只放行 VNet 内部与 `AzureMonitor`。没有这一层，Azure 默认规则 `AllowInternetOutBound(65000)` 会直接生效，等于没有出站管控。集中出站检查（FQDN 过滤）仍需 Azure Firewall + UDR，当前未部署。
- 规则使用独立的 `azurerm_network_security_rule`（内联 `security_rule` 块在 azurerm 4.x 已弃用、5.x 会移除）。

其余加固：审计存储关闭 Shared Key（只能走 Entra ID 授权）并带自身诊断；Key Vault / 审计存储 / Log Analytics 各有一把 `CanNotDelete` 管理锁，防止误删或凭据失陷后被清空。锁由 Terraform 管理，destroy 时会先删锁再删资源。

## PR 检查

所有面向 main 的 PR（包括 fork 和只改文档的 PR）都运行三个固定检查：

- `Terraform Validate (tf)`
- `Terraform Validate (tf-test)`
- `Security Checks`：全历史及工作树 Gitleaks 扫描、加密/防重放/输入处理测试。

PR 不接触 Azure 凭据、不读取生产 state、不获得 OIDC 权限、不上传 plan。
Actions 固定完整 commit SHA，checkout 不保留 Git 凭据，Gitleaks 下载校验固定 SHA-256。

## 部署与预览

```bash
gh workflow run deploy.yml --ref main -f mode=plan-only
gh workflow run deploy.yml --ref main -f mode=full
```

仅 main 能执行。full 的流程为：

```text
plan (tf-test, tf) → 加密并上传准确的计划
                  → deploy-test（test 身份与私钥）
                  → deploy-prod（production 审批后获得生产身份与私钥）
                  → cleanup-test（保留空资源组）
```

plan-only 仅发布资源地址和动作摘要，不发布任何属性、变量或输出值，也不上传 plan。
plan 使用读权限且 `-lock=false`；整个写入流程共用 concurrency，apply 自身申请 state lease，
并由 Terraform 拒绝过期计划。state 的两个文件在 bootstrap 时已存在。

完整 plan 使用 RSA 公钥封装和 AES-256-GCM 认证加密。公钥证书在 `.github/plan-keys/`；
私钥只存于对应 GitHub environment 的 `TF_PLAN_PRIVATE_KEY` secret。
解密同时检查 repository、commit、run ID、环境和文件摘要，禁止跨运行或跨环境重放。
公开日志与 job summary 不输出资源属性；原始 plan/apply 日志仅临时保存在 runner，步骤结束即删除。
失败诊断需用有权限的本地身份复现，不应把 state、原始计划或日志贴到公开 issue。

## 销毁与恢复

```bash
gh workflow run destroy.yml --ref main -f env=tf -f scope=all -f confirm=DESTROY
gh workflow run destroy.yml --ref main -f env=tf -f scope=network -f confirm=DESTROY
gh workflow run destroy.yml --ref main -f env=tf-test -f scope=all -f confirm=DESTROY
```

确认串作为环境变量按字面比较，不拼接为 Shell 代码。
资源组保留；Key Vault 和 Log Analytics 仅软删除，不自动 purge，也不自动删除恢复用资源组。
生产 Key Vault 开启 purge protection，开启后不能关闭。销毁之后同名重建可能需要管理员先恢复资源；
不要用 purge 作为日常回滚。Key Vault 使用默认拒绝的网络 ACL，RBAC 的业务访问角色需按应用另行授权。

### 销毁后重建（换名，推荐）

`purge protection` 不可逆，Log Analytics 的 14 天软删除也不会释放名字，而 provider 的
`recover_soft_deleted_key_vaults = true` 只能把**旧 vault 连数据一起拿回来** —— 拿不到一个干净的环境。
想得到全新环境，递增命名世代即可，三个全局唯一的名字会一起换掉：

```hcl
# tf/terraform.tfvars
azlandingzone_name_generation = "v2"   # 上一次是 v1；每次销毁后 +1
```

`kv-azlz-<hash>` / `stazlz<hash>` / `log-azlz-<hash>` 的 hash 由
`sha256(资源组名 + name_prefix + name_generation)` 推导，所以换世代不引入任何随机 provider，
plan 也不会每次变化。旧的软删除资源留在 Azure 中，按需由管理员自行恢复或清理。

## 一次性安全迁移

先完成代码审查和本地验证，再由管理员执行：

```bash
az login
gh workflow disable deploy.yml --repo gtsdrt/terraform
gh workflow disable destroy.yml --repo gtsdrt/terraform
python3 scripts/bootstrap_security.py prepare
```

该脚本不生成 Client Secret。它创建三个独立 OIDC 身份，给 apply 身份指定资源组 Contributor、
给 plan 指定资源组只读权限（包含刷新诊断资源所需的密钥读取操作），并隔离 state 数据权限。
原 state 从 `tfstate` 容器逐字节复制到新容器，校验 SHA-256，不覆盖不同的已有目标文件；原文件保留。
执行者临时获得 state 账户的数据面权限，复制完成或失败时撤销该临时授权。

脚本会设置 GitHub repository variables：

- `TERRAFORM_AZURE_SUBSCRIPTION_ID`、`TERRAFORM_AZURE_TENANT_ID`
- `TERRAFORM_PLAN_AZURE_CLIENT_ID`、`TERRAFORM_TEST_AZURE_CLIENT_ID`、`TERRAFORM_PROD_AZURE_CLIENT_ID`

它还生成两个公钥证书并把私钥直接写到 GitHub 的 production/test 环境 secret；私钥临时文件随后删除。
公钥证书需与代码一同提交。重复执行会保留已有证书，不自动轮换私钥。

代码合入、plan-only 验证通过后：

```bash
python3 scripts/bootstrap_security.py retire
gh workflow enable deploy.yml --repo gtsdrt/terraform
gh workflow enable destroy.yml --repo gtsdrt/terraform
```

retire 撤销旧专用执行身份的所有 Azure 角色与 Client Secret，删除旧 repository secrets，
并禁用 state 账户的 Shared Key 和匿名 Blob 访问。它不会删除原 state 或 App Registration。
GitHub main 应要求 PR、上述三个检查、一次批准及讨论解决；production 保留审批人，禁止自行审批与管理员绕过。
production/test 的部署分支均只允许 main。

## 本地验证

```bash
terraform fmt -check -recursive
terraform -chdir=tf init -backend=false -input=false -lockfile=readonly
terraform -chdir=tf validate
terraform -chdir=tf-test init -backend=false -input=false -lockfile=readonly
terraform -chdir=tf-test validate
python3 -m unittest discover -s tests -v
gitleaks git --redact
gitleaks dir --redact
```

加密测试需要 OpenSSL 3（macOS 可指定 `OPENSSL_BIN`）。
读取远程 state 时先获得相应数据面权限，使用 `terraform init -reconfigure` 切换到已迁移的新 backend；
不要仅改 state key，也不要把本地空 state 覆盖到远程。
