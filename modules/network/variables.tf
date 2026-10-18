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

variable "flow_log_storage_account_id" {
  description = "VNet Flow Log 目标存储账户 ID；enable_flow_logs 时必须提供"
  type        = string
  default     = null
}

variable "enable_flow_logs" {
  description = "是否为每个 VNet 创建 Flow Log（计数必须是 plan 时已知的布尔值）"
  type        = bool
  default     = false
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
