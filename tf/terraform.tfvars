vnet_count          = 2
resource_group_name = "terraform-prod"

# landing zone 命名世代：销毁后重建前 +1（v1 → v2），避开 Key Vault(purge protection)
# 与 Log Analytics 软删除期间被占用的名字。见 README「销毁后如何重建」。
azlandingzone_name_generation = "v1"
