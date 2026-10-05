data "azurerm_client_config" "current" {}

locals {
  # 由资源组名 + 前缀 + 命名世代推导出的稳定后缀，满足 Key Vault / 存储账户"全局唯一"的命名要求。
  # 用 hash 而不是 random provider：不需要额外 provider，也不会每次 plan 都变。
  #
  # name_generation 的作用：Key Vault 已开启 purge protection（不可逆），Log Analytics 也有 14 天软删除，
  # 因此"销毁 → 同名重建"并不可靠。销毁后把 name_generation +1，三个资源会一起换到全新名字。
  suffix = substr(sha256("${var.resource_group_name}-${var.name_prefix}-${var.name_generation}"), 0, 6)

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

  # ── 入站流量矩阵：目标子网 => 允许的源子网 ──
  # 刻意不用 source = "VirtualNetwork"：那等于放行整张 VNet 的任意端口，
  # Management / Shared / Workload 之间会变成横向零隔离。这里按具体子网 CIDR 逐条放行。
  #
  #   Management 只接受来自 GatewaySubnet（运维入口：VPN / ExpressRoute / Bastion）的流量
  #   Shared 接受来自 Management 与 Workload 的访问（共享服务被消费）
  #   Workload 接受来自 Shared 的访问（共享服务回调 / 内部依赖）
  #
  # 有负载均衡 / 应用网关 / spoke peering 之后，需要在矩阵里补对应的源前缀。
  inbound_allow = {
    Management = ["GatewaySubnet"]
    Shared     = ["Management", "Workload"]
    Workload   = ["Shared"]
  }

  # 每个常规子网都有的入站基础规则
  base_inbound = {
    "allow-lb" = {
      priority    = 110
      access      = "Allow"
      source      = "AzureLoadBalancer"
      destination = "*"
      description = "允许 Azure 负载均衡器健康探测"
    }
    "deny-internet" = {
      priority    = 4096
      access      = "Deny"
      source      = "Internet"
      destination = "*"
      description = "显式拒绝来自 Internet 的入站"
    }
  }

  # ── 出站基线：默认拒绝 Internet，只放行 VNet 内部与 Azure Monitor ──
  # 没有这一层，Azure 默认规则 AllowInternetOutbound(65000) 会直接生效，等于没有出站管控。
  # 真正的集中出站检查（FQDN 过滤 / TLS 检查）应由 Azure Firewall + UDR 承担。
  base_outbound = {
    "allow-vnet" = {
      priority    = 100
      access      = "Allow"
      source      = "VirtualNetwork"
      destination = "VirtualNetwork"
      description = "允许 VNet 内部出站"
    }
    "allow-azure-monitor" = {
      priority    = 110
      access      = "Allow"
      source      = "*"
      destination = "AzureMonitor"
      description = "允许出站到 Azure Monitor（日志与指标上报）"
    }
    "deny-internet" = {
      priority    = 4096
      access      = "Deny"
      source      = "*"
      destination = "Internet"
      description = "显式拒绝出站到 Internet（零信任出站基线）"
    }
  }

  # 展开成一张 (子网 × 规则) 的表，key 形如 "Management-in-deny-internet"。
  # 注意：Azure 的优先级只需在同一 NSG 的同一方向上唯一 —— 其内置默认规则在
  # 入站与出站方向上就复用了 65000/65001，所以这里两个方向都从 100 开始是安全的。
  nsg_rules = merge(
    {
      for item in flatten([
        for dst, srcs in local.inbound_allow : [
          for idx, src in srcs : {
            key         = "${dst}-in-allow-from-${lower(src)}"
            subnet      = dst
            priority    = 100 + idx
            direction   = "Inbound"
            access      = "Allow"
            source      = local.subnet_defs[src]
            destination = "*"
            description = "允许来自 ${src} 子网（${local.subnet_defs[src]}）的入站"
          }
        ]
      ]) : item.key => item
    },
    {
      for pair in setproduct(local.nsg_subnet_names, keys(local.base_inbound)) :
      "${pair[0]}-in-${pair[1]}" => {
        subnet      = pair[0]
        priority    = local.base_inbound[pair[1]].priority
        direction   = "Inbound"
        access      = local.base_inbound[pair[1]].access
        source      = local.base_inbound[pair[1]].source
        destination = local.base_inbound[pair[1]].destination
        description = local.base_inbound[pair[1]].description
      }
    },
    {
      for pair in setproduct(local.nsg_subnet_names, keys(local.base_outbound)) :
      "${pair[0]}-out-${pair[1]}" => {
        subnet      = pair[0]
        priority    = local.base_outbound[pair[1]].priority
        direction   = "Outbound"
        access      = local.base_outbound[pair[1]].access
        source      = local.base_outbound[pair[1]].source
        destination = local.base_outbound[pair[1]].destination
        description = local.base_outbound[pair[1]].description
      }
    },
  )
}

# ── 资源组 ──
data "azurerm_resource_group" "this" {
  name = var.resource_group_name
}

# ── hub VNet 与子网 ──
resource "azurerm_virtual_network" "hub" {
  name                = "vnet-${var.name_prefix}-hub"
  location            = data.azurerm_resource_group.this.location
  resource_group_name = data.azurerm_resource_group.this.name
  address_space       = var.vnet_address_space
  tags                = var.tags
}

resource "azurerm_subnet" "hub" {
  for_each = local.subnet_defs

  name                 = each.key
  resource_group_name  = data.azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.hub.name
  address_prefixes     = [each.value]
}

# ── NSG：每个常规子网一个，并挂上基线规则 ──
resource "azurerm_network_security_group" "hub" {
  for_each = local.nsg_subnet_names

  name                = "nsg-${var.name_prefix}-${lower(each.key)}"
  location            = data.azurerm_resource_group.this.location
  resource_group_name = data.azurerm_resource_group.this.name
  tags                = var.tags
}

resource "azurerm_network_security_rule" "baseline" {
  for_each = local.nsg_rules

  name                        = each.key
  resource_group_name         = data.azurerm_resource_group.this.name
  network_security_group_name = azurerm_network_security_group.hub[each.value.subnet].name

  priority                   = each.value.priority
  direction                  = each.value.direction
  access                     = each.value.access
  protocol                   = "*"
  source_port_range          = "*"
  source_address_prefix      = each.value.source
  destination_port_range     = "*"
  destination_address_prefix = each.value.destination
  description                = each.value.description
}

resource "azurerm_subnet_network_security_group_association" "hub" {
  for_each = local.nsg_subnet_names

  subnet_id                 = azurerm_subnet.hub[each.key].id
  network_security_group_id = azurerm_network_security_group.hub[each.key].id
}

# ── 集中日志 ──
resource "azurerm_log_analytics_workspace" "this" {
  name                = "log-${var.name_prefix}-${local.suffix}"
  location            = data.azurerm_resource_group.this.location
  resource_group_name = data.azurerm_resource_group.this.name
  retention_in_days   = var.log_analytics_retention_days
  tags                = var.tags
}

# ── 诊断/审计日志落地用的存储账户 ──
# 零信任要求"假设失陷"：审计日志的落脚点本身必须加固，否则拿到密钥的人可以清空审计数据。
#   - 关闭共享密钥 → 只能走 Entra ID 授权，密钥泄漏不再能直接读数据面
#   - network_rules 默认 Deny + AzureServices bypass → 阻断公网访问，同时保留平台诊断写入
resource "azurerm_storage_account" "diagnostics" {
  name                            = local.storage_account_name
  resource_group_name             = data.azurerm_resource_group.this.name
  location                        = data.azurerm_resource_group.this.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false

  network_rules {
    default_action = "Deny"
    bypass         = ["AzureServices"]
  }

  tags = var.tags
}

# 审计落地点自身也要有诊断，否则"谁动过审计日志"不可见
resource "azurerm_monitor_diagnostic_setting" "storage" {
  name                       = "diag-${local.storage_account_name}"
  target_resource_id         = azurerm_storage_account.diagnostics.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log {
    category_group = "audit"
  }
}

# ── Key Vault：RBAC 授权 + 网络默认拒绝（只放行 Azure 可信服务）──
resource "azurerm_key_vault" "this" {
  name                       = local.key_vault_name
  location                   = data.azurerm_resource_group.this.location
  resource_group_name        = data.azurerm_resource_group.this.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = true
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

# ── 删除保护：防止误操作或凭据失陷后删掉密钥库与审计落地 ──
# 用 management lock 而不是 lifecycle.prevent_destroy：锁由 Terraform 管理，
# destroy 时 Terraform 会先删除锁再删除资源（隐式依赖），所以销毁流程仍然可用；
# 而 prevent_destroy 会让 terraform destroy 直接失败。
resource "azurerm_management_lock" "key_vault" {
  name       = "lock-do-not-delete"
  scope      = azurerm_key_vault.this.id
  lock_level = "CanNotDelete"
  notes      = "零信任基线：Key Vault 不可直接删除。terraform destroy 会先移除本锁。"
}

resource "azurerm_management_lock" "storage" {
  name       = "lock-do-not-delete"
  scope      = azurerm_storage_account.diagnostics.id
  lock_level = "CanNotDelete"
  notes      = "零信任基线：审计日志落地点不可直接删除。terraform destroy 会先移除本锁。"
}

resource "azurerm_management_lock" "log_analytics" {
  name       = "lock-do-not-delete"
  scope      = azurerm_log_analytics_workspace.this.id
  lock_level = "CanNotDelete"
  notes      = "零信任基线：集中日志工作区不可直接删除。terraform destroy 会先移除本锁。"
}
