resource "azurerm_virtual_network" "demo" {
  name                = "terraform-executor-demo-vnet"
  location            = "norwayeast"
  resource_group_name = "terraform"
  address_space       = ["10.42.0.0/16"]
}