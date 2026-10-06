mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      subscription_id = "11111111-1111-1111-1111-111111111111"
      tenant_id       = "22222222-2222-2222-2222-222222222222"
    }
  }
  mock_data "azurerm_resource_group" {
    defaults = { location = "norwayeast" }
  }
}

variables {
  vnet_count          = 2
  resource_group_name = "terraform-prod-v2"
}

run "fresh_namespace_avoids_deleted_names" {
  command = plan
  assert {
    condition     = output.azlandingzone.resource_group_name == "azlandingzone-v2" && output.resource_group == "terraform-prod-v2"
    error_message = "Workloads must use the fresh foundation resource groups."
  }
  assert {
    condition     = output.azlandingzone.key_vault_name != "kv-azlz-9ee601" && length(output.azlandingzone.key_vault_name) <= 24 && can(regex("^[a-z0-9]+(-[a-z0-9]+)*$", output.azlandingzone.key_vault_name))
    error_message = "The new vault must use a valid name different from the deleted vault."
  }
  assert {
    condition     = output.azlandingzone.storage_account_name != "stazlz9ee601" && length(output.azlandingzone.storage_account_name) <= 24 && can(regex("^[a-z0-9]+$", output.azlandingzone.storage_account_name))
    error_message = "The diagnostic account must avoid the deleted account name and respect Azure constraints."
  }
}
