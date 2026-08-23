# ---------- 身份与订阅（已验证可用） ----------
data "azurerm_client_config" "current" {}
data "azurerm_subscription" "current" {}

# ---------- 资源组（已验证可用） ----------
data "azurerm_resource_group" "demo" {
  name = "terraform"
}

# ---------- 万能查询：列出资源组里所有资源（用于发现真实资源名） ----------
data "azurerm_resources" "all" {
  resource_group_name = "terraform"
}

# ---------- 所有 VNet（已验证可用） ----------
data "azurerm_resources" "vnets" {
  resource_group_name = "terraform"
  type                = "Microsoft.Network/virtualNetworks"
}

# ---------- VNet（已验证可用） ----------
data "azurerm_virtual_network" "demo" {
  name                = "terraform-executor-demo-vnet"
  resource_group_name = "terraform"
}

# ---------- ACR（真实存在：来自 workflow 的 registryUrl） ----------
data "azurerm_container_registry" "demo" {
  name                = "gtsdrtterraform"
  resource_group_name = "terraform"
}

# ---------- Container App（真实存在：executor 自己） ----------
data "azurerm_container_app" "executor" {
  name                = "terraform-executor"
  resource_group_name = "terraform"
}

# ---------- 以下资源上次 plan 确认不存在，保留注释，等你创建后再启用 ----------
# data "azurerm_subnet" "demo" {                  # demo-subnet 不存在
# data "azurerm_public_ip" "demo" {               # my-pip 不存在
# data "azurerm_network_security_group" "demo" {  # my-nsg 不存在
# data "azurerm_network_interface" "demo" {       # my-nic 不存在
# data "azurerm_virtual_machine" "demo" {         # my-vm 不存在
# data "azurerm_managed_disk" "demo" {            # my-disk 不存在
# data "azurerm_storage_account" "demo" {         # mystorageaccount 不存在
# data "azurerm_key_vault" "demo" {               # my-keyvault 不存在
# data "azurerm_key_vault_secret" "demo" {        # 依赖 key_vault
# data "azurerm_kubernetes_cluster" "demo" {      # my-aks 不存在
# data "azurerm_mssql_server" "demo" {            # my-sqlserver 不存在
# data "azurerm_cosmosdb_account" "demo" {        # my-cosmos 不存在
# data "azurerm_log_analytics_workspace" "demo" { # my-law 不存在

# ---------- 输出 ----------
output "current_subscription" {
  value = data.azurerm_subscription.current.display_name
}

output "current_tenant_id" {
  value = data.azurerm_client_config.current.tenant_id
}

output "vnet_address_space" {
  value = data.azurerm_virtual_network.demo.address_space
}

# 关键输出：列出资源组里所有真实资源的名字和类型
output "all_resources_in_rg" {
  value = [for r in data.azurerm_resources.all.resources : "${r.type}  ->  ${r.name}"]
}

output "acr_login_server" {
  value = data.azurerm_container_registry.demo.login_server
}

output "container_app_fqdn" {
  value = data.azurerm_container_app.executor.latest_revision_fqdn
}