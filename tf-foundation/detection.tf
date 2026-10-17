# ── 订阅级检测基线（管理员执行、长期存在）──
#
# 这些资源放在 foundation 而不是工作负载 root，原因有两个：
#   1. 订阅活动日志与 Defender pricing 需要**订阅级写权限**，而 prod SP 只有
#      资源组 Contributor；放 `tf` 会让生产 apply 必然 AuthorizationFailed。
#   2. Landing Zone 会被日常 destroy/重建；检测基线不应随其中断。
#
# 平台工作区放在 foundation 管理的 azlandingzone-v2 资源组内；`tf` 的 destroy 不会触碰它。

locals {
  # 名称按订阅生成稳定后缀；可用 platform_log_analytics_name 覆盖。
  platform_log_analytics_name = coalesce(
    var.platform_log_analytics_name,
    "log-platform-${substr(sha256("${var.subscription_id}-platform-audit"), 0, 8)}",
  )
}

resource "azurerm_log_analytics_workspace" "platform" {
  name                         = local.platform_log_analytics_name
  location                     = var.location
  resource_group_name          = azurerm_resource_group.workload["platform"].name
  sku                          = "PerGB2018"
  retention_in_days            = var.platform_log_analytics_retention_days
  daily_quota_gb               = var.platform_log_analytics_daily_quota_gb
  local_authentication_enabled = false
  tags = {
    managed_by = "terraform"
    layer      = "foundation"
    purpose    = "platform-audit"
    generation = "v2"
  }
}

# 一个订阅只能有一个 activity log 诊断设置。若订阅上已存在，先移除或 import 接管。
# 类别按 CIS Azure Foundations 6.1.2：Administrative / Alert / Policy / Security。
resource "azurerm_monitor_diagnostic_setting" "subscription_activity" {
  name                       = "diag-subscription-activity"
  target_resource_id         = local.subscription_scope
  log_analytics_workspace_id = azurerm_log_analytics_workspace.platform.id

  enabled_log {
    category = "Administrative"
  }

  enabled_log {
    category = "Alert"
  }

  enabled_log {
    category = "Policy"
  }

  enabled_log {
    category = "Security"
  }
}

# Defender for Cloud 的计划是订阅级、且计费。默认只开数据面三类 + 订阅控制面；
# defender_resource_types = [] 可全部关闭而不影响活动日志。
resource "azurerm_security_center_subscription_pricing" "this" {
  for_each = var.defender_resource_types

  resource_type = each.value
  tier          = "Standard"
}
