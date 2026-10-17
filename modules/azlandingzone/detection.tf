# ── 检测与可见性基线 ──
#
# ⚠️ 这个文件里的资源都是**订阅级**的，不是资源组级 —— 这是刻意的，也必须知道：
#   * 订阅活动日志只有订阅级能配；资源组级的诊断设置看不到角色分配、资源组与策略变更
#   * Defender for Cloud 的定价计划天然是订阅级，无法只对某个资源组生效
#
# 副作用：销毁 landing zone（destroy scope = azlandingzone 或 all）会把它们一起移除 ——
# Defender 退回 Free、订阅活动日志停止导出。如果希望安全基线独立于 landing zone 的
# 生命周期，应当把它们拆进独立的 root module 与独立 state。

# ── 订阅活动日志 → Log Analytics ──
# 没有这一条，"谁改了角色分配、谁删了资源、谁动了网络"在 Log Analytics 里完全不可见：
# Key Vault 的审计很完整，但平台自身的控制面操作没有任何记录。
# 类别按 CIS Azure Foundations 6.1.2 选取（Administrative / Alert / Policy / Security）。
#
# 注意：一个订阅只能有一个 activity log 诊断设置。若订阅上已存在（手工创建或由策略部署），
# 需要先移除，或改用 terraform import 接管，否则 apply 会失败。
resource "azurerm_monitor_diagnostic_setting" "subscription_activity" {
  name                       = "diag-subscription-activity"
  target_resource_id         = "/subscriptions/${data.azurerm_client_config.current.subscription_id}"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

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

# ── Defender for Cloud：对关键资源类型开启威胁检测 ──
# 只开数据面资源与订阅控制面，不默认开启 VirtualMachines 等当前未使用的类型。
# tier = Standard 会影响订阅内该类型的**所有**资源，包括 landing zone 之外的资源。
resource "azurerm_security_center_subscription_pricing" "this" {
  for_each = var.defender_resource_types

  resource_type = each.value
  tier          = "Standard"
}
