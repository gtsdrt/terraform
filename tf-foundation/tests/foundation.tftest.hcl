mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id = "22222222-2222-2222-2222-222222222222"
    }
  }
}

variables {
  subscription_id = "11111111-1111-1111-1111-111111111111"
  tenant_id       = "22222222-2222-2222-2222-222222222222"
  principal_ids = {
    plan = "33333333-3333-3333-3333-333333333333"
    test = "44444444-4444-4444-4444-444444444444"
    prod = "55555555-5555-5555-5555-555555555555"
  }
}

run "fresh_groups_and_scoped_roles" {
  command = plan

  override_resource {
    target = azurerm_resource_group.workload["prod"]
    values = { id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/terraform-prod-v2" }
  }
  override_resource {
    target = azurerm_resource_group.workload["test"]
    values = { id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/terraform-test-v2" }
  }
  override_resource {
    target = azurerm_resource_group.workload["platform"]
    values = { id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/azlandingzone-v2" }
  }
  override_resource {
    target = azurerm_role_definition.plan_reader
    values = { role_definition_resource_id = "/subscriptions/11111111-1111-1111-1111-111111111111/providers/Microsoft.Authorization/roleDefinitions/66666666-6666-6666-6666-666666666666" }
  }
  override_resource {
    target = azurerm_role_definition.deleted_vault_reader
    values = { role_definition_resource_id = "/subscriptions/11111111-1111-1111-1111-111111111111/providers/Microsoft.Authorization/roleDefinitions/77777777-7777-7777-7777-777777777777" }
  }

  assert {
    condition     = toset(values(output.resource_groups)) == toset(["terraform-prod-v2", "terraform-test-v2", "azlandingzone-v2"])
    error_message = "Foundation must create all three fresh groups with v2 names."
  }
  assert {
    condition     = alltrue([for key, role in azurerm_role_assignment.plan_reader : role.scope == "/subscriptions/${var.subscription_id}/resourceGroups/${azurerm_resource_group.workload[key].name}" && role.principal_id == var.principal_ids.plan])
    error_message = "Plan key-reading roles must stay at the three workload group scopes."
  }
  assert {
    condition     = azurerm_role_assignment.apply["test"].principal_id == var.principal_ids.test && alltrue([for key in ["prod", "platform"] : azurerm_role_assignment.apply[key].principal_id == var.principal_ids.prod]) && alltrue([for key, role in azurerm_role_assignment.apply : role.scope == "/subscriptions/${var.subscription_id}/resourceGroups/${azurerm_resource_group.workload[key].name}" && role.role_definition_name == "Contributor"])
    error_message = "Apply identities must be isolated and must not get subscription-wide Contributor."
  }
  assert {
    condition     = toset(azurerm_role_definition.plan_reader.permissions[0].actions) == toset(["*/read", "Microsoft.Storage/storageAccounts/listKeys/action", "Microsoft.OperationalInsights/workspaces/sharedKeys/action"]) && length(coalesce(azurerm_role_definition.plan_reader.permissions[0].data_actions, [])) == 0
    error_message = "Plan role must only contain the reviewed resource refresh operations."
  }
  assert {
    condition     = toset(azurerm_role_definition.deleted_vault_reader.permissions[0].actions) == toset(["Microsoft.KeyVault/locations/deletedVaults/read", "Microsoft.KeyVault/locations/operationResults/read"]) && azurerm_role_assignment.deleted_vault_reader.principal_id == var.principal_ids.prod
    error_message = "Deleted-vault lookup must grant metadata reads only; never purge or recovery writes."
  }
}
