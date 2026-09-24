output "deployed_vnets" {
  value = [for v in azurerm_virtual_network.main : v.name]
}

output "resource_group" {
  value = azurerm_resource_group.main.name
}
