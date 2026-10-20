# 如何添加新资源

本文说明在这个仓库里新增 Azure 资源时，应该放到哪一层、需要改哪些文件、补哪些权限和测试，以及如何部署与验证。完整流程与当前控制矩阵见[安全基线与流程总结](SECURITY_BASELINE_2026-10-18.md)。

## 1. 先决定放哪一层

| 判断 | 放在 | 原因 |
|---|---|---|
| 订阅级资源（Defender pricing、订阅活动日志、policy assignment、management group） | `tf-foundation/` | prod SP 没有订阅级写权限；foundation 由管理员执行 |
| 角色定义 / 角色分配 / 任何 `Microsoft.Authorization/*` 写操作 | `tf-foundation/` | Contributor 被 notActions 禁止；workload plan 预检会拒绝 |
| 资源组 | `tf-foundation/` | 长期存在、`prevent_destroy`，不属于业务销毁范围 |
| 平台级 Log Analytics / 长期审计基线 | `tf-foundation/` | 不能随 Landing Zone destroy 中断 |
| prod 业务资源（`terraform-prod-v2`、`azlandingzone-v2`） | `tf/` 或 `modules/` | prod 身份在两个组内有 Contributor |
| 可被 prod 与 test 复用的实现 | `modules/` | 通过变量区分环境 |
| 只用于临时测试的网络 | `tf-test/` + `modules/network` | test 每次部署后清理，不放生产专属资源 |

**硬规则**

- 不要在 `tf/`、`tf-test/`、`modules/` 里创建订阅级资源或 role assignment；`preflight_permissions.py` 会在审批前失败，`tests/test_security.py` 也会在 PR 检查里拦截。
- 如果一个新资源需要 prod 身份没有的权限，**先在 foundation 加最小自定义角色并 apply**，再合并业务变更；不要给 prod SP 订阅级 Contributor。

## 2. 目录与命名

| 路径 | 用途 |
|---|---|
| `modules/network/` | 通用 VNet/子网/NSG（prod 与 test 共用） |
| `modules/azlandingzone/` | 生产 Landing Zone：hub VNet、KV、Storage、LA、锁、flow log |
| `tf/` | 生产 root：组合模块、prod 专属参数 |
| `tf-test/` | 临时测试 root：只使用 network 模块 |
| `tf-foundation/` | 资源组、RBAC、订阅级检测、平台工作区 |

- 资源名使用稳定后缀：`local.suffix = substr(sha256("<subscription>-<rg>-<prefix>-<generation>"), 0, 10)`。新增资源复用 `local.suffix` 或 `var.deployment_generation`，避免与软删除保留名称冲突。
- 所有资源加 `tags`（至少 `managed_by`、`env`）。
- 变量加 `description`、`type`，能校验就加 `validation`；**不要在 tfvars 里放密钥**，敏感值用 Key Vault / 环境 secret。
- 不要改 `.terraform.lock.hcl` 之外的 provider 版本；升级走 Dependabot PR 并跑 validate/test。

## 3. 推荐步骤

1. **建分支**，在 PR 描述里说明资源、所属层、权限和销毁影响。
2. **判断是否需要新权限**（见第 4 节）。需要时先做 foundation 变更。
3. **修改模块/root**：变量、资源、输出；复用现有命名和标签。
4. **按第 5 节安全基线**设置默认值。
5. **补测试**：Terraform mock 断言 + 必要时 `plan_summary.py` 允许列表。
6. **本地验证**：`terraform fmt -check -recursive`、三个 root 的 `validate`、`terraform test`、`python3 -m unittest`、actionlint。
7. **PR**：必需检查通过、代码所有者审查、合入 main。
8. **部署**：先 `plan-only` 看摘要，再 `full`；test 清理成功后用非触发账号审批 production。
9. **验证**：Azure 实态 + 下一次 Drift Detection。

## 4. 如果新资源需要新权限

1. 在 `tf-foundation/main.tf` 增加**只含所需 action**的 `azurerm_role_definition`，scope/assignable scope 取最小范围。
2. 给 plan 身份分配只读版本、给 prod 身份分配读写版本（如果都需要）。
3. 更新 `tf-foundation/tests/foundation.tftest.hcl`，固定 actions 和 assignment scope/principal。
4. 更新 `docs/ARCHITECTURE.md` 的权限表。
5. 管理员 `tf-foundation plan/apply`（先于业务部署）。
6. 如果该资源属于“预检应拦截”的类别，更新 `.github/scripts/preflight_permissions.py` 和 `tests/test_observability.py`。

> 例子：Flow Log 资源在 `NetworkWatcherRG`，所以 foundation 定义了 `Flow Log Reader`（plan）和 `Flow Log Manager`（prod），scope 为订阅；业务资源仍在 `modules/azlandingzone` / `modules/network` 创建。

## 5. 每个新资源的安全基线

| 类别 | 要求 |
|---|---|
| 网络 | 默认拒绝、显式子网分段、出站管控；新增 VNet 时评估 flow log |
| 身份 | 优先 RBAC/托管身份；不用共享密钥、连接字符串、长期 secret |
| 传输 | 最低 TLS1.2；禁用明文/公共入口（除非有明确理由） |
| 数据 | 静态加密（平台或 CMK）、软删除/版本化、备份/保留期 |
| 审计 | 诊断设置送到集中 LA；数据落地账户自身也要有诊断 |
| 删除保护 | 关键数据存储加 `CanNotDelete` 锁；锁必须 `depends_on` 对应诊断，避免销毁 `ScopeLocked` |
| 标签 | `managed_by`、`env`；需要时加 `purpose` |
| 最小权限 | 新增角色只给实际 action；不要复用 Contributor 扩大范围 |
| 公开暴露 | 默认关闭 public network access；需要时用 Private Endpoint + Private DNS |

## 6. 测试与摘要

- **Terraform mock 测试**：在 `tf/tests/` 或 `tf-foundation/tests/` 增加 `run` 块，断言关键安全字段（如 `shared_access_key_enabled == false`、`daily_quota_gb`、锁、flow log、权限 action）。
- **Python 测试**：
  - `tests/test_security.py` 覆盖 `tf_run` 错误分类、plan 摘要脱敏、`.gitignore`、scope 分离；
  - `tests/test_observability.py` 覆盖 drift 命令和权限预检。
- **计划摘要**：如果希望审批人看到新安全字段，在 `.github/scripts/plan_summary.py` 的 `FIELDS` 增加**枚举/布尔/有界数字/端口**；不要输出任意字符串、IP、ID、标签。
- **本地命令**（Terraform 1.16.6）：

```bash
terraform fmt -check -recursive
terraform -chdir=tf init -backend=false -input=false -lockfile=readonly && terraform -chdir=tf validate
terraform -chdir=tf-test init -backend=false -input=false -lockfile=readonly && terraform -chdir=tf-test validate
terraform -chdir=tf-foundation init -backend=false -input=false -lockfile=readonly && terraform -chdir=tf-foundation validate
terraform -chdir=tf test
terraform -chdir=tf-foundation test
python3 -m unittest discover -s tests -v
```

## 7. 部署与验证

1. `gh workflow run deploy.yml --ref main -f mode=plan-only`，检查脱敏摘要：应只包含预期资源动作。
2. `gh workflow run deploy.yml --ref main -f mode=full`。
3. test apply + cleanup 成功。
4. 用**非触发账号**批准 production。
5. apply 后检查 Azure 实态（名称、网络、诊断、锁、标签）。
6. 下一次 Drift Detection 应 no drift（环境已部署时）。

## 8. 销毁影响

- `destroy` 只操作 `tf/`、`tf-test/` state，不删除 foundation 的资源组/授权/平台工作区/活动日志/Defender。
- 数据资源可能软删除或保留：Key Vault（软删除 7 天 + purge protection）、Log Analytics（软删除）、Storage（随资源删除）。
- 删除顺序：**锁 → 诊断 → 资源**；新增锁时务必 `depends_on` 对应诊断。
- 需要在 destroy 后保留的资源，应放入 foundation 或独立 state，而不是 `tf/`。

## 9. 常见失败

| 错误 | 含义 | 处理 |
|---|---|---|
| `authorization` | apply 身份缺少写权限 | 检查 foundation 角色/scope；不要用 plan 身份 apply |
| `scope-locked` | 父资源锁阻止删除子资源 | 确认锁先于诊断删除；新建 destroy plan |
| `stale-plan` | state serial 已变 | 新建运行生成新计划，不重跑旧 apply |
| `api-conflict` / `AnotherOperationInProgress` | Azure 并发操作 | 等待后新建 full 运行；复发时降低并行度/串行化 |
| `name-conflict` | 名称占用/软删除/409 | 检查软删除保留期与命名（`deployment_generation`） |
| 预检失败 | 订阅级资源/未授权锁/身份相同 | 移到 foundation 或补最小角色 |
| 计划过期 | 超过 24h | 新建 full/destroy 运行 |
| drift 报“未部署”（销毁后） | state 为空、计划全部是 create | 输出 notice、运行保持绿色；这是未部署，不是漂移。部分资源仍在时报错才是漂移 |

## 10. 示例：新增一条 NSG 规则（`modules/network`）

```hcl
# modules/network/main.tf
resource "azurerm_network_security_rule" "allow_https" {
  count = var.vnet_count

  name                        = "allow-https-inbound"
  resource_group_name         = data.azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.main[count.index].name

  priority                   = 100
  direction                  = "Inbound"
  access                     = "Allow"
  protocol                   = "Tcp"
  source_port_range          = "*"
  destination_port_range     = "443"
  source_address_prefix      = "VirtualNetwork"
  destination_address_prefix = "*"
}
```

再加 mock 断言（`tf/tests/fresh_names.tftest.hcl`）并通过 `terraform test`。

## 11. 示例：新增数据存储（`modules/azlandingzone`）

```hcl
resource "azurerm_storage_account" "newdata" {
  name                            = "st${replace(lower(var.name_prefix), "-", "")}${local.suffix}data"
  resource_group_name             = data.azurerm_resource_group.this.name
  location                        = data.azurerm_resource_group.this.location
  account_tier                    = "Standard"
  account_replication_type        = "GRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false

  network_rules {
    default_action = "Deny"
    bypass         = ["AzureServices"]
  }

  tags = var.tags
}

resource "azurerm_monitor_diagnostic_setting" "newdata" {
  name                       = "diag-${azurerm_storage_account.newdata.name}"
  target_resource_id         = "${azurerm_storage_account.newdata.id}/blobServices/default"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log { category = "StorageRead" }
  enabled_log { category = "StorageWrite" }
  enabled_log { category = "StorageDelete" }
  enabled_metric { category = "Transaction" }
}

# 锁必须在诊断之后创建、之前销毁
resource "azurerm_management_lock" "newdata" {
  name       = "lock-do-not-delete"
  scope      = azurerm_storage_account.newdata.id
  lock_level = "CanNotDelete"
  depends_on = [azurerm_monitor_diagnostic_setting.newdata]
}
```

> 注意：Storage 账户级诊断只有 `Capacity`/`Transaction` 指标；`StorageRead/Write/Delete` 必须配置在 `blobServices/default` scope。

## 12. PR 检查清单

- [ ] 资源放在正确层级；没有订阅级资源进入 `tf`/`modules`
- [ ] 命名/标签/变量校验；没有明文 secret
- [ ] 安全基线（网络、TLS、公共访问、诊断、加密、锁）
- [ ] 锁 `depends_on` 诊断；销毁顺序正确
- [ ] Terraform mock 断言 + Python 测试
- [ ] `fmt` / 三个 root `validate` / `terraform test`
- [ ] 需要新权限时，foundation 变更已单独 PR 并由管理员 apply
- [ ] PR 描述写明部署影响与 destroy 影响
- [ ] 部署后验证 Azure 实态与 drift 结果
