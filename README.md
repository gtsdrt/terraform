# Terraform on GitHub Actions

Azure 网络与 Landing Zone 的 v2 从零重建配置。资源组和限定授权现在由独立的 Terraform foundation 创建、管理；业务部署继续使用 GitHub OIDC、生产审批及加密计划。

- 首次初始化：[从零重建说明](docs/FRESH_START.md)
- 日常操作：[部署、审批、销毁与恢复](docs/DEPLOYMENT_GUIDE.md)
- 权限边界：[安全架构](docs/ARCHITECTURE.md)

## 层与资源

| 层 | 目录 | 资源组 | state 容器 / 文件 |
|---|---|---|---|
| foundation | `tf-foundation/` | 创建并保留三个 v2 组及授权 | `tfstate-foundation/foundation-v2.tfstate` |
| prod | `tf/` | `terraform-prod-v2`、`azlandingzone-v2` | `tfstate-prod/executor-v2.tfstate` |
| test | `tf-test/` | `terraform-test-v2` | `tfstate-test/terraform-test-v2.tfstate` |

现有 state 账户 `gtsdrtterraform2`、backend 资源组 `Terraform` 和三个 OIDC 身份保留。旧 state 文件原样保留；新初始化脚本只创建缺失的 v2 文件，禁止覆盖。业务名称使用包含订阅与重建代号的稳定后缀，避开旧软删除名称，不 purge 或自动恢复旧 Key Vault。

foundation 由具备资源组创建及 RBAC 管理权限的 Azure 管理员执行，创建 3 个组、2 个自定义角色和 7 个角色分配。资源组设有 `prevent_destroy`；日常 GitHub 工作流只操作 prod/test state，不删除 foundation 的资源组或授权。

prod 管理 37 个业务资源，test 管理 12 个 network 资源。test 不覆盖 Landing Zone 的实际 apply。NSG 的现有 HTTPS 规则不代表其他内部端口已被拒绝；网络最小权限需按业务需求另行完善。

## 从零初始化

先从功能分支执行管理员初始化，完成后合入 main 并恢复工作流。详细的暂停、登录、验收步骤见 [FRESH_START](docs/FRESH_START.md)。

```bash
# 管理员以 Azure CLI 登录目标订阅；先确保写入工作流暂停且没有活动运行。
python3 scripts/prepare_fresh_start.py
python3 scripts/prepare_fresh_start.py --apply
terraform -chdir=tf-foundation init -reconfigure -input=false -lockfile=readonly
terraform -chdir=tf-foundation plan -var-file=foundation.tfvars.local.json -out=foundation.tfplan
terraform -chdir=tf-foundation apply foundation.tfplan
```

默认脚本只读核对身份并生成本地输入；已有 v2 组及正确的 Contributor 授权会生成本地 import 块，并保留检测到的组区域。`--apply` 仅准备 backend、空 state 和容器数据权限。创建或接管资源组和业务角色由随后执行的 foundation Terraform 完成。仅有 Contributor 的执行者不能创建 RBAC，需要管理员的相应授权能力。

## PR 检查

所有面向 main 的 PR（包括 fork、仅文档改动）均执行：

- `Terraform Validate (tf)`、`Terraform Validate (tf-test)`、`Terraform Validate (tf-foundation)`。
- `Security Checks`：Python 安全/初始化测试、foundation 权限与业务名称的 Terraform mock 测试、固定版本 actionlint、Gitleaks 全历史和工作树扫描。

main 已要求前两个 validate 和 Security Checks；foundation 的 mock 测试纳入 Security Checks，保持必需检查覆盖。PR 不获得 Azure 凭据、OIDC 或生产 state。Actions 固定完整 SHA，工具下载校验固定摘要。

## 部署

```bash
gh workflow run deploy.yml --repo gtsdrt/terraform --ref main -f mode=plan-only
gh workflow run deploy.yml --repo gtsdrt/terraform --ref main -f mode=full
```

只有 main 可部署；命中 `tf/**`、`tf-test/**`、`modules/**` 或 `.github/**` 的 main push 自动 full。仅 README/docs 改动不自动部署。foundation 没有自动 apply 工作流，必须由管理员单独维护。

```text
管理员：准备 v2 backend → Terraform foundation 创建组与授权
GitHub：plan(tf-test, tf) → test apply → test cleanup → production 审批 → prod apply
```

推荐由 `gtsdrt` 合入代码/发起部署，`atea-shuangliang` 审批 production。PR 的 Approve/Merge 和 Actions 的 Review deployments / Approve and deploy 是两种不同操作。production 禁止自行审批及管理员绕过。

计划使用只读身份；两个 apply 身份各自取得对应环境的私钥。计划绑定仓库、提交、run ID、环境、操作目的和销毁范围，24 小时后拒绝执行，密文保留 3 天。公开摘要仅显示资源动作及允许列表中的安全字段，敏感值和任意属性不公开。

完整部署、销毁和恢复使用共享写入队列，生产审批等待也占用队列；plan-only 使用独立只读队列。测试与清理都成功才进入生产审批。取消后的恢复与每 6 小时兜底仅清理 test state、保留组。

## 销毁

```bash
gh workflow run destroy.yml --repo gtsdrt/terraform --ref main -f env=tf -f scope=network -f confirm=DESTROY
gh workflow run destroy.yml --repo gtsdrt/terraform --ref main -f env=tf -f scope=all -f confirm=DESTROY
gh workflow run destroy.yml --repo gtsdrt/terraform --ref main -f env=tf-test -f scope=all -f confirm=DESTROY
```

销毁先展示准确删除清单，生产审批后 apply 同一份加密 destroy plan。它不操作 foundation state。Key Vault/Log Analytics 不永久 purge；诊断 Storage 会随 Landing Zone 被删除，其归档日志不受上述软删除设置保护。具体处理见操作指南。

`Terraform Deploy` 表单只有 `full / plan-only`。出现 `network` 和 `DESTROY` 确认字段时，当前入口是 `Terraform Destroy`，不能用于部署。

## 本地验证

```bash
terraform fmt -check -recursive
terraform -chdir=tf init -backend=false -input=false -lockfile=readonly
terraform -chdir=tf validate
terraform -chdir=tf-test init -backend=false -input=false -lockfile=readonly
terraform -chdir=tf-test validate
terraform -chdir=tf-foundation init -backend=false -input=false -lockfile=readonly
terraform -chdir=tf-foundation validate
terraform -chdir=tf-foundation test
terraform -chdir=tf test
python3 -m unittest discover -s tests -v
python3 .github/scripts/check_workflows.py /path/to/actionlint
gitleaks git --redact
gitleaks dir --redact
```

mock 测试不接触 Azure。加密测试需要 OpenSSL 3，macOS 可指定 `OPENSSL_BIN`。actionlint 1.7.12 尚不识别 GitHub 的 `queue: max`，检查适配器仅注释该确切顶层设置并验证取消冲突，其余工作流照常校验。

历史 `bootstrap_security.py prepare/retire` 用于旧 state 迁移与旧身份停用；v2 从零重建使用新的 `prepare_fresh_start.py`，不重新迁移或清空历史 state。
