output "resource_group_name" {
  value = data.azurerm_resource_group.this.name
}

output "vnet_id" {
  value = azurerm_virtual_network.hub.id
}

output "vnet_name" {
  value = azurerm_virtual_network.hub.name
}

output "subnet_ids" {
  value = { for k, v in azurerm_subnet.hub : k => v.id }
}

output "log_analytics_workspace_id" {
  value = azurerm_log_analytics_workspace.this.id
}

output "storage_account_name" {
  value = azurerm_storage_account.diagnostics.name
}

output "key_vault_id" {
  value = azurerm_key_vault.this.id
}

output "key_vault_name" {
  value = azurerm_key_vault.this.name
}

# 供 root 模块的 mock 测试校验安全基线，不需要 Azure 凭据。
output "security_baseline" {
  value = {
    storage_shared_key_enabled   = azurerm_storage_account.diagnostics.shared_access_key_enabled
    log_analytics_daily_quota_gb = azurerm_log_analytics_workspace.this.daily_quota_gb
    management_lock_levels = {
      key_vault     = azurerm_management_lock.key_vault.lock_level
      storage       = azurerm_management_lock.storage.lock_level
      log_analytics = azurerm_management_lock.log_analytics.lock_level
    }
    inbound_allow_sources = {
      for name, rule in azurerm_network_security_rule.baseline : name => rule.source_address_prefix
      if rule.direction == "Inbound" && rule.access == "Allow" && rule.source_address_prefix != "AzureLoadBalancer"
    }
    outbound_internet_denies = {
      for name, rule in azurerm_network_security_rule.baseline : name => rule.destination_address_prefix
      if rule.direction == "Outbound" && rule.access == "Deny" && rule.destination_address_prefix == "Internet"
    }
  }
}
