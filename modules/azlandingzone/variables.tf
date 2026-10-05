variable "resource_group_name" {
  description = "landing zone 所在资源组名称"
  type        = string
  default     = "azlandingzone"
}

variable "location" {
  description = "部署区域"
  type        = string
  default     = "norwayeast"
}

variable "name_prefix" {
  description = "资源名前缀，只允许小写字母/数字/连字符"
  type        = string
  default     = "azlz"

  validation {
    condition     = can(regex("^[a-z0-9-]{2,8}$", var.name_prefix))
    error_message = "name_prefix 只能包含小写字母、数字和连字符，长度 2-8。"
  }
}

variable "name_generation" {
  description = <<-EOT
    命名世代，参与 Key Vault / 存储账户 / Log Analytics 的名字 hash。
    销毁 landing zone 后、重建之前把它 +1（v1 → v2 → v3…），即可避开软删除期间被占用的名字。

    为什么需要它：Key Vault 已开启 purge protection（不可逆），无法 purge；
    Log Analytics 有 14 天软删除且 provider 不会自动恢复，同名重建会直接失败。
    provider 的 recover_soft_deleted_key_vaults = true 只能把**旧 vault 连数据一起拿回来**，
    拿不到一个干净的新 vault。换名是唯一确定可行、且能得到干净环境的手段。
  EOT
  type        = string
  default     = "v1"

  validation {
    condition     = can(regex("^[a-z0-9]{1,8}$", var.name_generation))
    error_message = "name_generation 只能包含小写字母和数字，长度 1-8。"
  }
}

variable "vnet_address_space" {
  description = "hub VNet 的地址空间"
  type        = list(string)
  default     = ["10.0.0.0/16"]
}

variable "log_analytics_retention_days" {
  description = "Log Analytics 工作区保留天数"
  type        = number
  default     = 30
}

variable "key_vault_name" {
  description = "Key Vault 名称（全局唯一，3-24 位字母数字连字符）；留空则自动生成"
  type        = string
  default     = null
}

variable "storage_account_name" {
  description = "诊断用存储账户名称（全局唯一，3-24 位小写字母数字）；留空则自动生成"
  type        = string
  default     = null
}

variable "tags" {
  description = "附加到所有资源的标签"
  type        = map(string)
  default     = {}
}
