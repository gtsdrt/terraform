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

variable "log_analytics_daily_quota_gb" {
  description = <<-EOT
    Log Analytics 每日摄入上限（GB），-1 表示不限制。

    为什么要有上限：没有上限时，任何能向工作区写入的主体都可以无限推高账单 ——
    这是一个成本 / 可用性攻击面，不是单纯的省钱设置。

    ⚠️ 代价：达到上限后**当日停止摄入**（会丢日志，工作区会记录一个操作事件）。
    这是安全与可用性的取舍：配额太低会在真实事件发生时丢掉审计证据。
    请按实际摄入量留出足够余量；不确定时调高而不是调低。
  EOT
  type        = number
  default     = 10

  validation {
    condition     = var.log_analytics_daily_quota_gb == -1 || var.log_analytics_daily_quota_gb >= 1
    error_message = "log_analytics_daily_quota_gb 必须是 -1（不限制）或 >= 1。"
  }
}

variable "flow_log_retention_days" {
  description = "VNet Flow Log 保留天数（1-365）"
  type        = number
  default     = 90

  validation {
    condition     = var.flow_log_retention_days >= 1 && var.flow_log_retention_days <= 365
    error_message = "flow_log_retention_days 必须在 1-365 之间。"
  }
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
