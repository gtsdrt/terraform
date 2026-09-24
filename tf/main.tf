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

# 网络资源都定义在 modules/network 里，两个环境共用同一份实现
module "network" {
  source = "../modules/network"

  vnet_count          = var.vnet_count
  resource_group_name = var.resource_group_name
  location            = var.location
}

output "deployed_vnets" {
  value = module.network.deployed_vnets
}

output "resource_group" {
  value = module.network.resource_group
}

# ── 把已有资源从根模块迁进 module ──
#    没有这些 moved 块，Terraform 会认为旧地址的资源被删除、新地址是新建，
#    从而把已经存在的 13 个资源 destroy 再 create。apply 成功后这些块就是空操作。
moved {
  from = azurerm_resource_group.main
  to   = module.network.azurerm_resource_group.main
}

moved {
  from = azurerm_virtual_network.main
  to   = module.network.azurerm_virtual_network.main
}

moved {
  from = azurerm_subnet.main
  to   = module.network.azurerm_subnet.main
}

moved {
  from = azurerm_network_security_group.main
  to   = module.network.azurerm_network_security_group.main
}

moved {
  from = azurerm_subnet_network_security_group_association.main
  to   = module.network.azurerm_subnet_network_security_group_association.main
}
