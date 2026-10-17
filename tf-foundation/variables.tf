variable "subscription_id" {
  type        = string
  description = "目标订阅 ID，由管理员初始化脚本从仓库变量读取"
  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "subscription_id 必须是 UUID。"
  }
}

variable "tenant_id" {
  type        = string
  description = "目标租户 ID；执行身份必须属于该租户"
  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.tenant_id))
    error_message = "tenant_id 必须是 UUID。"
  }
}

variable "principal_ids" {
  description = "已有 plan/test/prod service principal 的 Object ID，不是 Client ID"
  type = object({
    plan = string
    test = string
    prod = string
  })
  validation {
    condition     = length(distinct(values(var.principal_ids))) == 3 && alltrue([for id in values(var.principal_ids) : can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", id))])
    error_message = "必须提供三个不同的 service principal Object ID。"
  }
}

variable "location" {
  type    = string
  default = "norwayeast"
}

variable "defender_resource_types" {
  description = <<-EOT
    开启 Microsoft Defender for Cloud **Standard** 计划的资源类型（订阅级、计费项）。

    默认三类数据面 + 订阅控制面。设为 [] 可全部关闭；关闭时仍保留平台工作区与活动日志。
    要求订阅已注册 Microsoft.Security（providers.tf 为 resource_provider_registrations = "none"）。
  EOT
  type        = set(string)
  default     = ["StorageAccounts", "KeyVaults", "Arm"]

  validation {
    condition = alltrue([
      for t in var.defender_resource_types : contains([
        "AI", "Api", "AppServices", "CloudPosture", "ContainerRegistry", "Containers",
        "CosmosDbs", "Dns", "KeyVaults", "KubernetesService", "OpenSourceRelationalDatabases",
        "SqlServers", "SqlServerVirtualMachines", "StorageAccounts", "VirtualMachines", "Arm",
      ], t)
    ])
    error_message = "defender_resource_types 含未知取值。"
  }
}

variable "platform_log_analytics_name" {
  description = "平台审计工作区名称；留空则按订阅生成稳定名称"
  type        = string
  default     = null

  validation {
    condition     = var.platform_log_analytics_name == null || can(regex("^[A-Za-z0-9][A-Za-z0-9-]{3,62}$", var.platform_log_analytics_name))
    error_message = "platform_log_analytics_name 必须为 4-63 位字母、数字或连字符，且以字母/数字开头。"
  }
}

variable "platform_log_analytics_retention_days" {
  description = "平台审计工作区保留天数（活动日志长期留存）"
  type        = number
  default     = 90

  validation {
    condition     = var.platform_log_analytics_retention_days >= 30 && var.platform_log_analytics_retention_days <= 730
    error_message = "platform_log_analytics_retention_days 必须在 30-730 之间。"
  }
}

variable "platform_log_analytics_daily_quota_gb" {
  description = "平台审计工作区每日摄入上限（GB），-1 表示不限制"
  type        = number
  default     = 10

  validation {
    condition     = var.platform_log_analytics_daily_quota_gb == -1 || var.platform_log_analytics_daily_quota_gb >= 1
    error_message = "platform_log_analytics_daily_quota_gb 必须是 -1 或 >= 1。"
  }
}
