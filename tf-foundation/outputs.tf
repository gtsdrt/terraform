output "resource_groups" {
  value = { for key, group in azurerm_resource_group.workload : key => group.name }
}
