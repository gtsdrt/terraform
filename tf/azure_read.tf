
data "azurerm_client_config" "current" {}

data "azurerm_subscription" "current" {}


data "azurerm_resource_group" "demo" {
name = "terraform"
}

data "azurerm_resources" "vnets" {
resource_group_name = "terraform"
  type                = "Microsoft.Network/virtualNetworks"
}


data "azurerm_virtual_network" "demo" {
  name                = "terraform-executor-demo-vnet"
  resource_group_name = "terraform"
}

data "azurerm_subnet" "demo" {
  name                 = "demo-subnet"
  virtual_network_name = "terraform-executor-demo-vnet"
  resource_group_name  = "terraform"
}

data "azurerm_public_ip" "demo" {
  name                = "my-pip"
  resource_group_name = "terraform"
}

data "azurerm_network_security_group" "demo" {
  name                = "my-nsg"
  resource_group_name = "terraform"
}

data "azurerm_network_interface" "demo" {
  name                = "my-nic"
  resource_group_name = "terraform"
}


data "azurerm_virtual_machine" "demo" {
  name                = "my-vm"
  resource_group_name = "terraform"
}

data "azurerm_managed_disk" "demo" {
  name                = "my-disk"
  resource_group_name = "terraform"
}


data "azurerm_storage_account" "demo" {
  name                = "mystorageaccount"
  resource_group_name = "terraform"
}


data "azurerm_key_vault" "demo" {
  name                = "my-keyvault"
  resource_group_name = "terraform"
}

data "azurerm_key_vault_secret" "demo" {
  name         = "my-secret"
  key_vault_id = data.azurerm_key_vault.demo.id
}


data "azurerm_kubernetes_cluster" "demo" {
  name                = "my-aks"
  resource_group_name = "terraform"
}

data "azurerm_container_registry" "demo" {
  name                = "myacr"
  resource_group_name = "terraform"
}


data "azurerm_mssql_server" "demo" {
  name                = "my-sqlserver"
  resource_group_name = "terraform"
}

data "azurerm_cosmosdb_account" "demo" {
  name                = "my-cosmos"
  resource_group_name = "terraform"
}


data "azurerm_log_analytics_workspace" "demo" {
  name                = "my-law"
  resource_group_name = "terraform"
}


output "current_subscription" {
  value = data.azurerm_subscription.current.display_name
}

output "current_tenant_id" {
  value = data.azurerm_client_config.current.tenant_id
}

output "vnet_address_space" {
 value = data.azurerm_virtual_network.demo.address_space
}