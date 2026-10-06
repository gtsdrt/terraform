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
    condition     = length(var.name_prefix) >= 2 && length(var.name_prefix) <= 8 && can(regex("^[a-z0-9]+(-[a-z0-9]+)*$", var.name_prefix))
    error_message = "name_prefix 长度 2-8，只含小写字母、数字和单个分隔连字符，不能以连字符开头或结尾。"
  }
}

variable "deployment_generation" {
  description = "重建代号；与新的 state key 一起使用，避免复用旧软删除资源名称"
  type        = string
  default     = "v2"
  validation {
    condition     = can(regex("^[a-z0-9]{1,12}$", var.deployment_generation))
    error_message = "deployment_generation 必须是 1-12 位小写字母或数字。"
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
