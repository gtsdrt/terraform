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
