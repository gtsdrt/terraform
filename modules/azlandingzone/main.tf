data "azurerm_client_config" "current" {}

locals {
  # 由资源组名 + 前缀推导出的稳定后缀，满足 Key Vault / 存储账户"全局唯一"的命名要求。
  # 用 hash 而不是 random provider：不需要额外 provider，也不会每次 plan 都变。
  suffix = substr(sha256("${var.resource_group_name}-${var.name_prefix}"), 0, 6)

  key_vault_name       = coalesce(var.key_vault_name, "kv-${var.name_prefix}-${local.suffix}")
  storage_account_name = coalesce(var.storage_account_name, "st${replace(lower(var.name_prefix), "-", "")}${local.suffix}")

  # hub VNet 的标准子网划分：
  #   GatewaySubnet / AzureFirewallSubnet 是保留名，先把地址空间留出来（/27 和 /26 是 Azure 的最低要求）
  #   Management / Shared / Workload 是给后续资源用的常规网段
  subnet_defs = {
    GatewaySubnet       = cidrsubnet(var.vnet_address_space[0], 11, 0) # 例：10.0.0.0/27
    AzureFirewallSubnet = cidrsubnet(var.vnet_address_space[0], 10, 1) # 例：10.0.0.64/26
    Management          = cidrsubnet(var.vnet_address_space[0], 8, 1)  # 例：10.0.1.0/24
    Shared              = cidrsubnet(var.vnet_address_space[0], 8, 2)  # 例：10.0.2.0/24
    Workload            = cidrsubnet(var.vnet_address_space[0], 8, 3)  # 例：10.0.3.0/24
  }

  # 这两个保留子网按 Azure 建议不挂 NSG
  nsg_subnet_names = toset(["Management", "Shared", "Workload"])

  # NSG 基线规则（Inbound）：放行 VNet 内与负载均衡器探测，显式拒绝 Internet 入站
  baseline_rules = {
    "allow-vnet-inbound" = {
      priority    = 100
      access      = "Allow"
      source      = "VirtualNetwork"
      description = "允许来自 VNet 的入站"
    }
    "allow-lb-inbound" = {
      priority    = 110
      access      = "Allow"
      source      = "AzureLoadBalancer"
      description = "允许 Azure 负载均衡器健康探测"
    }
    "deny-internet-inbound" = {
      priority    = 4096
      access      = "Deny"
      source      = "Internet"
      description = "显式拒绝来自 Internet 的入站"
    }
  }

  # 子网 × 规则 的展开成一张表，key 形如 "Management-allow-vnet-inbound"
  rule_matrix = {
    for pair in setproduct(local.nsg_subnet_names, keys(local.baseline_rules)) :
    "${pair[0]}-${pair[1]}" => { subnet = pair[0], rule = pair[1] }
  }
}

# ── 资源组 ──
resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

# ── hub VNet 与子网 ──
resource "azurerm_virtual_network" "hub" {
  name                = "vnet-${var.name_prefix}-hub"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  address_space       = var.vnet_address_space
  tags                = var.tags
}

resource "azurerm_subnet" "hub" {
  for_each = local.subnet_defs

  name                 = each.key
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.hub.name
  address_prefixes     = [each.value]
}

# ── NSG：每个常规子网一个，并挂上基线规则 ──
resource "azurerm_network_security_group" "hub" {
  for_each = local.nsg_subnet_names

  name                = "nsg-${var.name_prefix}-${lower(each.key)}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = var.tags
}

resource "azurerm_network_security_rule" "baseline" {
  for_each = local.rule_matrix

  name                        = each.value.rule
  resource_group_name         = azurerm_resource_group.this.name
  network_security_group_name = azurerm_network_security_group.hub[each.value.subnet].name

  priority                   = local.baseline_rules[each.value.rule].priority
  direction                  = "Inbound"
  access                     = local.baseline_rules[each.value.rule].access
  protocol                   = "*"
  source_port_range          = "*"
  source_address_prefix      = local.baseline_rules[each.value.rule].source
  destination_port_range     = "*"
  destination_address_prefix = "*"
  description                = local.baseline_rules[each.value.rule].description
}

resource "azurerm_subnet_network_security_group_association" "hub" {
  for_each = local.nsg_subnet_names

  subnet_id                 = azurerm_subnet.hub[each.key].id
  network_security_group_id = azurerm_network_security_group.hub[each.key].id
}

# ── 集中日志 ──
resource "azurerm_log_analytics_workspace" "this" {
  name                = "log-${var.name_prefix}-${local.suffix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  retention_in_days   = var.log_analytics_retention_days
  tags                = var.tags
}

# ── 诊断/审计日志落地用的存储账户 ──
resource "azurerm_storage_account" "diagnostics" {
  name                            = local.storage_account_name
  resource_group_name             = azurerm_resource_group.this.name
  location                        = azurerm_resource_group.this.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  tags                            = var.tags
}

# ── Key Vault：RBAC 授权 + 网络默认拒绝（只放行 Azure 可信服务）──
resource "azurerm_key_vault" "this" {
  name                       = local.key_vault_name
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = false
  soft_delete_retention_days = 7
  tags                       = var.tags

  network_acls {
    default_action = "Deny"
    bypass         = "AzureServices"
  }
}

# ── 把 Key Vault 的审计日志同时送到 Log Analytics 和存储账户 ──
resource "azurerm_monitor_diagnostic_setting" "key_vault" {
  name                       = "diag-${local.key_vault_name}"
  target_resource_id         = azurerm_key_vault.this.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  storage_account_id         = azurerm_storage_account.diagnostics.id

  enabled_log {
    category = "AuditEvent"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}
