output "deployed_vnets" {
  value = [for v in azurerm_virtual_network.main : v.name]
}

output "resource_group" {
  value = data.azurerm_resource_group.main.name
}

output "flow_log_ids" {
  value = [for flow_log in azurerm_network_watcher_flow_log.main : flow_log.id]
}
