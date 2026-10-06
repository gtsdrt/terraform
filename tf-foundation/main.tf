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
  for_each                         = local.group_names
  scope                            = "${local.subscription_scope}/resourceGroups/${azurerm_resource_group.workload[each.key].name}"
  role_definition_id               = azurerm_role_definition.plan_reader.role_definition_resource_id
  principal_id                     = var.principal_ids.plan
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "apply" {
  for_each                         = local.apply_groups
  scope                            = "${local.subscription_scope}/resourceGroups/${azurerm_resource_group.workload[each.key].name}"
  role_definition_name             = "Contributor"
  principal_id                     = var.principal_ids[each.value]
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

# AzureRM 创建 Key Vault 时会查询同名 deleted vault；仅授予元数据读取，不允许恢复或 purge。
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
  scope                            = local.subscription_scope
  role_definition_id               = azurerm_role_definition.deleted_vault_reader.role_definition_resource_id
  principal_id                     = var.principal_ids.prod
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}
