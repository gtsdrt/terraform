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
}

variable "defender_resource_types" {
  description = <<-EOT
    开启 Microsoft Defender for Cloud **Standard** 计划的资源类型。默认三类数据面 + 订阅控制面。

    ⚠️ 两个重要前提：
      1. 这是**订阅级**设置，会影响订阅内该类型的**所有**资源，包括 landing zone 之外的资源。
      2. 这是**计费**项，不是免费功能。设为 [] 可全部关闭。
    需要的资源提供程序：Microsoft.Security（providers.tf 里是
    resource_provider_registrations = "none"，因此需先执行 az provider register -n Microsoft.Security）。
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
    error_message = "defender_resource_types 含未知取值，合法值见 azurerm_security_center_subscription_pricing 文档。"
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
