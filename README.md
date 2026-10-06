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
网络模块创建两个 VNet、四个子网、两个 NSG 和四个关联（12 个资源）。
生产还创建 Landing Zone 的 25 个资源，合计 37 个；资源组不计入 managed resources。
HTTPS 入站只接受 `VirtualNetwork` 来源。

## PR 检查

所有面向 main 的 PR（包括 fork 和只改文档的 PR）都运行三个固定检查：

- `Terraform Validate (tf)`
- `Terraform Validate (tf-test)`
- `Security Checks`：全历史及工作树 Gitleaks 扫描、加密/防重放/过期/摘要脱敏测试，以及 actionlint 工作流检查。

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
                  → cleanup-test（测试成功或失败后清理，保留空资源组）
                  → deploy-prod（测试与清理均成功，production 审批后获得生产身份与私钥）
```

公开摘要显示资源地址、动作和明确允许的安全字段：NSG 方向/允许拒绝/协议/端口/已知服务标签，
Key Vault 与 Storage 保护开关及网络默认策略，日志保留天数等。具体 IP、任意字符串、资源 ID、
标签、变量、输出值及 sensitive 字段不发布；未知字段明确标注等待 apply，不能据此推断安全。
plan-only 不上传 plan，使用独立预览队列，可在生产等待审批时运行；与写入并发的预览仅供参考。
plan 使用读权限且 `-lock=false`；部署、销毁和恢复清理共用写入队列，`queue: max` 最多保留 100 个待运行请求。
写入流程（包括生产审批等待）仍保持互斥，避免审批期间另一次写入改变受审计划；apply 获取 state lease。
state serial 已变化时 Terraform 拒绝旧计划，但这不能替代云端漂移检查。state 的两个文件在 bootstrap 时已存在。

完整 plan 使用 RSA 公钥封装和 AES-256-GCM 认证加密。公钥证书在 `.github/plan-keys/`；
私钥只存于对应 GitHub environment 的 `TF_PLAN_PRIVATE_KEY` secret。
解密同时检查 repository、commit、run ID、环境、操作目的、销毁范围和文件摘要，禁止跨运行、环境或目的重放。
计划自加密起有效 24 小时，截止时间显示在摘要中；密文保留 3 天，过期仍拒绝 apply，并提示重新发起完整运行。
重跑整个 workflow 会生成新摘要，必须审查新计划；仅重跑 apply 不会延长有效期。
Terraform 原始输出不进入公开日志，失败只显示固定错误类别（权限、锁、旧计划、配额、名称冲突、网络或 provider 注册）。
临时日志在步骤结束时删除；仍需详细诊断时用有权限的本地身份复现，不应公开 state 或原始计划/日志。

测试资源不等待生产审批：测试结束后立即清理；测试或清理失败都会阻止生产部署。
`Terraform Test Recovery` 在部署运行结束（含取消）后检查是否尝试过 test 部署，并每 6 小时兜底清理。
恢复只 checkout main，不读取触发运行的代码或 artifact，只使用 test 身份和 test state；保留测试资源组。
恢复也参与写入队列，不会在另一次完整部署/销毁期间清理资源；如果运行仍等待审批，需批准或取消后才释放队列。

## 销毁与恢复

```bash
gh workflow run destroy.yml --ref main -f env=tf -f scope=all -f confirm=DESTROY
gh workflow run destroy.yml --ref main -f env=tf -f scope=network -f confirm=DESTROY
gh workflow run destroy.yml --ref main -f env=tf-test -f scope=all -f confirm=DESTROY
```

销毁先由只读 plan 身份生成准确的删除清单并加密，之后才进入 production/test 环境。
生产审批人先检查 plan job 的摘要；审批后仅执行已保存的 destroy plan，不重新计算销毁范围。
确认串作为环境变量按字面比较，不拼接为 Shell 代码。
**销毁 Landing Zone 会删除诊断存储账户和其中的归档日志**，当前模块没有独立的长期审计存储保护。
Key Vault/Log Analytics 的软删除不会保护这个存储账户；重要日志应独立保留后再审批销毁。
资源组保留；Key Vault 和 Log Analytics 仅软删除，不自动 purge，也不自动删除恢复用资源组。
生产 Key Vault 开启 purge protection，开启后不能关闭。销毁之后同名重建可能需要管理员先恢复资源；
不要用 purge 作为日常回滚。Key Vault 使用默认拒绝的网络 ACL，RBAC 的业务访问角色需按应用另行授权。

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
python3 .github/scripts/check_workflows.py /path/to/actionlint
gitleaks git --redact
gitleaks dir --redact
```

加密测试需要 OpenSSL 3（macOS 可指定 `OPENSSL_BIN`）。CI 固定 actionlint 1.7.12 并校验下载摘要。
GitHub 已支持 `queue: max`，该 actionlint 版本尚未支持；检查脚本仅将顶层 concurrency 中完全匹配的
`queue: max` 行改为注释再校验，其余内容和行号保留，其他 queue 写法仍会报错。
读取远程 state 时先获得相应数据面权限，使用 `terraform init -reconfigure` 切换到已迁移的新 backend；
不要仅改 state key，也不要把本地空 state 覆盖到远程。
