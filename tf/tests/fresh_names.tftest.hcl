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

run "hardened_network_and_storage_baseline" {
  command = plan

  assert {
    condition     = output.azlandingzone.security_baseline.storage_shared_key_enabled == false
    error_message = "The audit storage account must disable shared key access."
  }
  assert {
    condition     = output.azlandingzone.security_baseline.log_analytics_daily_quota_gb == 10
    error_message = "Log Analytics must keep a daily ingestion quota to bound cost and log-flooding risk."
  }
  assert {
    condition     = alltrue([for source in values(output.azlandingzone.security_baseline.inbound_allow_sources) : can(cidrnetmask(source))])
    error_message = "Inbound allows must segment by subnet CIDR, not the whole-VNet VirtualNetwork tag."
  }
  assert {
    condition     = length(output.azlandingzone.security_baseline.outbound_internet_denies) == 3
    error_message = "Every workload NSG must explicitly deny Internet egress."
  }
  assert {
    condition     = output.azlandingzone.security_baseline.management_lock_levels.key_vault == "CanNotDelete" && output.azlandingzone.security_baseline.management_lock_levels.storage == "CanNotDelete" && output.azlandingzone.security_baseline.management_lock_levels.log_analytics == "CanNotDelete"
    error_message = "Key Vault, audit storage and Log Analytics must all carry a CanNotDelete lock."
  }
}
