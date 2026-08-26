variable "vnet_count" {
  description = "要创建的 VNet 数量"
  type        = number
}

variable "resource_group_name" {
  description = "资源组名称"
  type        = string
}

variable "location" {
  description = "部署区域"
  type        = string
  default     = "norwayeast"
}

# 资源组
resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
}

# VNet
resource "azurerm_virtual_network" "main" {
  count               = var.vnet_count
  name                = format("vnet-%02d", count.index + 1)
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  address_space       = [cidrsubnet("172.16.0.0/12", 4, count.index)]
}

# 子网
locals {
  subnet_defs = merge([
    for i in range(var.vnet_count) : {
      for j in range(2) : "${i}-${j}" => { vnet = i, sub = j }
    }
  ]...)
}

resource "azurerm_subnet" "main" {
  for_each             = local.subnet_defs
  name                 = format("subnet-%02d", each.value.sub + 1)
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main[each.value.vnet].name
  address_prefixes     = [cidrsubnet(cidrsubnet("172.16.0.0/12", 4, each.value.vnet), 8, each.value.sub)]
}

# NSG
resource "azurerm_network_security_group" "main" {
  count               = var.vnet_count
  name                = format("nsg-%02d", count.index + 1)
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  security_rule {
    name                       = "allow-https-inbound"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

output "deployed_vnets" {
  value = [for v in azurerm_virtual_network.main : v.name]
}

output "resource_group" {
  value = azurerm_resource_group.main.name
}