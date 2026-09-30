# 安全架构与运维边界

## 认证和授权

每个 GitHub OIDC subject 绑定独立 Azure service principal，而非同一身份的多个 federated credentials。

| 身份 | OIDC subject 后缀 | Azure 管理权限 | state 数据权限 |
|---|---|---|---|
| plan | `ref:refs/heads/main` | 三个受管资源组只读，另含 Storage listKeys 与 Log Analytics sharedKeys 的读取 | 两个隔离容器 Blob Data Reader |
| test | `environment:test` | 仅 terraform-test 资源组 Contributor | 仅 tfstate-test 容器 Blob Data Contributor |
| prod | `environment:production` | 仅 terraform-prod、azlandingzone 资源组 Contributor | 仅 tfstate-prod 容器 Blob Data Contributor |

subject 的完整前缀由 GitHub owner/repository 不可变数字 ID 推导，bootstrap 脚本读取实际仓库数据生成。
无订阅级 Contributor，无长期 Azure Client Secret。Key Vault 业务 secret 的数据面角色不由该部署身份获得。
plan 因 Terraform 刷新计算属性需要读取诊断资源密钥，因此它能读取敏感数据；“只读”不代表数据不敏感。
plan 对管理面不能写入，对 state 数据面不能写入，也不能通过账户 listKeys 获得整个 state 账户访问权。

production 的环境审批限制生产身份和私钥获取；main 分支保护限制谁能修改受信任的工作流。
这两个边界必须同时启用。test 环境也限制为 main。
PR 检查完全离线于 Azure，不下发 secrets，不签发 OIDC；fork PR 仍运行格式、校验和密钥扫描。

## state 与计划

原 tfstate 容器保留为迁移备份，新 tfstate-prod 和 tfstate-test 都禁止匿名访问。
backend 显式使用 Microsoft Entra ID；state 存储账户禁用 Shared Key。
apply 的两个身份分别只能操作一个容器，因此测试身份不能修改生产 state。
计划在仓库级 concurrency 内计算且不申请 lease，apply 获取 lease 并拒绝 serial 已变化的旧计划。
人工操作也必须避免与 Actions 写入并发。

公开 plan artifact 仅包含 CMS AuthEnvelopedData，AES-256-GCM 提供加密和完整性验证，RSA 封装会话密钥。
内部文档绑定 repository、commit SHA、GitHub run ID 和 Terraform 环境，解密验证后才创建 mode 0600 的 plan 文件。
两个环境使用不同私钥，生产私钥仅存在 production environment 的 secret。
公钥证书有效期五年；轮换时暂停运行，生成新的证书与环境私钥，同时提交新证书，并丢弃旧运行的计划。
保护环境的访问控制及主分支审查同样保护私钥；获准在生产 job 中运行恶意代码仍能窃取它。

job summary 仅展示资源地址与动作。即使属性未标记 sensitive，也不会被摘要发布。
原始 plan/apply/destroy 输出不进入公开 Actions 日志；runner 临时文件在 always 步骤清理。

## 资源生命周期

资源组在 bootstrap 中创建，是持久的权限边界，不再由 Terraform 删除。
根模块的 removed 块覆盖历史根资源组地址和模块资源组地址，destroy=false；现有组内资源地址不变。
VNet、子网和 NSG 实现仍由 network 模块复用。test 不验证 Landing Zone 的实际 apply。
HTTPS 入站收窄到 VirtualNetwork；未来公网服务需要明确审查允许来源。
诊断存储网络默认 Deny，仅绕过受信任 Azure 服务。Terraform provider 不访问受限存储的数据面。

生产 Key Vault 默认拒绝网络入站、使用 RBAC、开启 purge protection；Terraform provider 也明确禁止 destroy 时 purge。
Log Analytics 保留软删除。destroy workflow 取消恢复后永久删除以及资源组删除步骤。
这会改变原来的“销毁后立刻同名重建”行为：需要管理员恢复软删除的 Key Vault/工作区，再导入 state 或重新 plan。
部署身份没有订阅级 deleted-vault 权限，恢复由管理员单独完成；不要为自动恢复扩大部署身份权限。

## 迁移检查表

1. 确认没有部署或销毁运行，暂停旧 deploy/destroy workflow。
2. 用 bootstrap prepare 创建 OIDC 身份、资源组、容器和环境加密密钥。
3. 逐字节复制两个历史 state，保留 lineage/serial；两个目标文件的 SHA-256 必须与源相同。
4. 提交代码和公钥证书，跑 fmt、两套 validate、加密安全测试、Gitleaks 和 actionlint。
5. 合入后只跑 plan-only，用新的只读 OIDC 身份验证 backend 和资源读取。此步骤不创建资源。
6. bootstrap retire 撤销旧身份角色与 client secrets，禁用 state 共享密钥及匿名 Blob 访问。
7. 启用 main PR/检查/批准保护与 production 审批保护；恢复 deploy/destroy workflow。
8. 本地使用新 backend 需 init -reconfigure。将来 full 部署仍先 test，再等待 production 审批。

如果新身份验证失败，应保持旧写入 workflow 暂停，修复新身份授权；不要回退到订阅级 Contributor 或公开原始 plan。
Azure 角色授权/撤销可能需要数分钟传播。更改存储认证前，确认该 state 账户没有其他使用 Shared Key 的应用。
