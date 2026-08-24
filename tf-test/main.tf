variable "vnet_count" {
  description = "要创建的 VNet 数量"
  type        = number
  default     = 9
}

# 目标资源组
data "azurerm_resource_group" "test" {
  name     = "terraform-test"
}

# N 个 VNet：172.16.0.0/16 ~ 172.30.0.0/16
resource "azurerm_virtual_network" "test" {
  count               = var.vnet_count
  name                = format("test-vnet-%02d", count.index + 1)
  location            = data.azurerm_resource_group.test.location
  resource_group_name = data.azurerm_resource_group.test.name
  address_space       = [cidrsubnet("172.16.0.0/12", 4, count.index)]
}

# 每个 VNet 2 个子网（vnet 索引 × 子网索引 的笛卡尔积）
locals {
  subnet_defs = merge([
    for i in range(var.vnet_count) : {
      for j in range(2) : "${i}-${j}" => { vnet = i, sub = j }
    }
  ]...)
}

resource "azurerm_subnet" "test" {
  for_each             = local.subnet_defs
  name                 = format("subnet-%02d", each.value.sub + 1)
  resource_group_name  = data.azurerm_resource_group.test.name
  virtual_network_name = azurerm_virtual_network.test[each.value.vnet].name
  address_prefixes     = [cidrsubnet(cidrsubnet("172.16.0.0/12", 4, each.value.vnet), 8, each.value.sub)]
}

# 每个 VNet 一个 NSG
resource "azurerm_network_security_group" "test" {
  count               = var.vnet_count
  name                = format("test-nsg-%02d", count.index + 1)
  location            = data.azurerm_resource_group.test.location
  resource_group_name = data.azurerm_resource_group.test.name

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
  value = [for v in azurerm_virtual_network.test : v.name]
}

output "resource_count" {
  value = "资源组 1 + VNet ${var.vnet_count} + 子网 ${var.vnet_count * 2} + NSG ${var.vnet_count}"
}