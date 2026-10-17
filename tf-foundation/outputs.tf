output "resource_groups" {
  value = { for key, group in azurerm_resource_group.workload : key => group.name }
}

output "platform_log_analytics_workspace_id" {
  value = azurerm_log_analytics_workspace.platform.id
}

output "platform_log_analytics_workspace_name" {
  value = azurerm_log_analytics_workspace.platform.name
}

output "subscription_activity_log_target" {
  value = local.subscription_scope
}

output "lock_manager_role_definition_id" {
  value = azurerm_role_definition.lock_manager.role_definition_resource_id
}

output "defender_plans" {
  value = sort([for key, plan in azurerm_security_center_subscription_pricing.this : key])
}
