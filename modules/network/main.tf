# 资源组
data "azurerm_resource_group" "main" {
  name = var.resource_group_name
}

# VNet
resource "azurerm_virtual_network" "main" {
  count               = var.vnet_count
  name                = format("vnet-%02d", count.index + 1)
  location            = data.azurerm_resource_group.main.location
  resource_group_name = data.azurerm_resource_group.main.name
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
  resource_group_name  = data.azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main[each.value.vnet].name
  address_prefixes     = [cidrsubnet(cidrsubnet("172.16.0.0/12", 4, each.value.vnet), 8, each.value.sub)]
}

# NSG
resource "azurerm_network_security_group" "main" {
  count               = var.vnet_count
  name                = format("nsg-%02d", count.index + 1)
  location            = data.azurerm_resource_group.main.location
  resource_group_name = data.azurerm_resource_group.main.name
}

# 规则用独立的 azurerm_network_security_rule：内联 security_rule 块在 azurerm 4.x 已弃用、5.x 会移除。
resource "azurerm_network_security_rule" "allow_https_inbound" {
  count = var.vnet_count

  name                        = "allow-https-inbound"
  resource_group_name         = data.azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.main[count.index].name

  priority                   = 100
  direction                  = "Inbound"
  access                     = "Allow"
  protocol                   = "Tcp"
  source_port_range          = "*"
  destination_port_range     = "443"
  source_address_prefix      = "VirtualNetwork"
  destination_address_prefix = "*"
  description                = "允许 VNet 内的 HTTPS 入站（演示网络）"
}

# NSG 不关联到子网就不生效，所以每个 VNet 的 NSG 挂到该 VNet 下的所有子网
resource "azurerm_subnet_network_security_group_association" "main" {
  for_each = local.subnet_defs

  subnet_id                 = azurerm_subnet.main[each.key].id
  network_security_group_id = azurerm_network_security_group.main[each.value.vnet].id
}
