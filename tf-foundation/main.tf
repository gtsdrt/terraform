data "azurerm_client_config" "operator" {}

locals {
  subscription_scope = "/subscriptions/${var.subscription_id}"
  group_names = {
    prod     = "terraform-prod-v2"
    test     = "terraform-test-v2"
    platform = "azlandingzone-v2"
  }
  apply_groups = {
    test     = "test"
    prod     = "prod"
    platform = "prod"
  }
  # 允许 prod apply 管理锁的资源组范围；锁本身仍是 CanNotDelete，destroy 先删锁。
  # 目前只有 azlandingzone-v2 里的 KV/Storage/LA 需要加锁，因此只授这一个组。
  lock_manager_groups = {
    platform = local.group_names.platform
  }
}

# 资源组及授权属于长期 foundation state，不属于临时 test 或生产销毁范围。
resource "azurerm_resource_group" "workload" {
  for_each = local.group_names
  name     = each.value
  location = var.location
  tags = {
    managed_by = "terraform"
    layer      = "foundation"
    generation = "v2"
    env        = each.key
  }
  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = data.azurerm_client_config.operator.tenant_id == var.tenant_id
      error_message = "管理员的 Azure 租户与目标租户不一致。"
    }
  }
}

resource "azurerm_role_definition" "plan_reader" {
  name               = "gtsdrt Terraform v2 Plan Reader ${var.subscription_id}"
  role_definition_id = uuidv5("url", "${local.subscription_scope}/terraform-v2/plan-reader")
  scope              = local.subscription_scope
  assignable_scopes  = [local.subscription_scope]
  description        = "Read workload groups and computed diagnostic keys; assigned only at workload group scopes"
  permissions {
    actions = [
      "*/read",
      "Microsoft.Storage/storageAccounts/listKeys/action",
      "Microsoft.OperationalInsights/workspaces/sharedKeys/action",
    ]
  }
}

resource "azurerm_role_assignment" "plan_reader" {
  for_each           = local.group_names
  scope              = "${local.subscription_scope}/resourceGroups/${azurerm_resource_group.workload[each.key].name}"
  role_definition_id = azurerm_role_definition.plan_reader.role_definition_resource_id
  principal_id       = var.principal_ids.plan
  principal_type     = "ServicePrincipal"
}

# principal_type 已明确 SP；省略仅创建时使用的 skip 标志，避免导入后触发不支持的 update。
resource "azurerm_role_assignment" "apply" {
  for_each             = local.apply_groups
  scope                = "${local.subscription_scope}/resourceGroups/${azurerm_resource_group.workload[each.key].name}"
  role_definition_name = "Contributor"
  principal_id         = var.principal_ids[each.value]
  principal_type       = "ServicePrincipal"
}

# 订阅范围仅授予 deleted vault 元数据读取；本组 vault 的恢复使用已有 Contributor 的 vaults/write。
resource "azurerm_role_definition" "deleted_vault_reader" {
  name               = "gtsdrt Terraform v2 Deleted Vault Reader ${var.subscription_id}"
  role_definition_id = uuidv5("url", "${local.subscription_scope}/terraform-v2/deleted-vault-reader")
  scope              = local.subscription_scope
  assignable_scopes  = [local.subscription_scope]
  permissions {
    actions = [
      "Microsoft.KeyVault/locations/deletedVaults/read",
      "Microsoft.KeyVault/locations/operationResults/read",
    ]
  }
}

resource "azurerm_role_assignment" "deleted_vault_reader" {
  scope              = local.subscription_scope
  role_definition_id = azurerm_role_definition.deleted_vault_reader.role_definition_resource_id
  principal_id       = var.principal_ids.prod
  principal_type     = "ServicePrincipal"
}

# ── 删除锁管理：只给 locks/*，不给任何 RBAC 写权限 ──
# prod SP 是资源组 Contributor；Contributor 的 notActions 含
# Microsoft.Authorization/*/Write，无法创建/删除 management lock。
# 这个自定义角色只补 locks 读写删，assignment 范围限定在两个生产组。
resource "azurerm_role_definition" "lock_manager" {
  name               = "gtsdrt Terraform v2 Lock Manager ${var.subscription_id}"
  role_definition_id = uuidv5("url", "${local.subscription_scope}/terraform-v2/lock-manager")
  scope              = local.subscription_scope
  assignable_scopes = [
    "${local.subscription_scope}/resourceGroups/${local.group_names.platform}",
  ]
  description = "Create and delete resource locks only; no role assignment or other Authorization writes"
  permissions {
    actions = [
      "Microsoft.Authorization/locks/read",
      "Microsoft.Authorization/locks/write",
      "Microsoft.Authorization/locks/delete",
    ]
  }
}

resource "azurerm_role_assignment" "lock_manager" {
  for_each           = local.lock_manager_groups
  scope              = "${local.subscription_scope}/resourceGroups/${azurerm_resource_group.workload[each.key].name}"
  role_definition_id = azurerm_role_definition.lock_manager.role_definition_resource_id
  principal_id       = var.principal_ids.prod
  principal_type     = "ServicePrincipal"
}
