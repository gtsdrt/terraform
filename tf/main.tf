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

# ── Landing Zone ──
# 只放在生产环境：资源组名固定为 azlandingzone，且 Key Vault 删除后有软删除保护，
# 放进"每次 push 都建了又删"的 tf-test 环境第二次就会因为名字被占用而失败。
module "azlandingzone" {
  source = "../modules/azlandingzone"

  resource_group_name = "azlandingzone"
  location            = var.location
  name_prefix         = "azlz"

  tags = {
    managed_by = "terraform"
    env        = "prod"
  }
}

output "azlandingzone" {
  value = {
    resource_group_name     = module.azlandingzone.resource_group_name
    vnet_name               = module.azlandingzone.vnet_name
    subnet_ids              = module.azlandingzone.subnet_ids
    log_analytics_workspace = module.azlandingzone.log_analytics_workspace_id
    key_vault_name          = module.azlandingzone.key_vault_name
    storage_account_name    = module.azlandingzone.storage_account_name
  }
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
