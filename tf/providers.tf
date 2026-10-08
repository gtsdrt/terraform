terraform {
  required_version = "~> 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy = false
      # 生产销毁后同名 vault 仍在软删除期；经 production 审批的 apply 恢复它。
      recover_soft_deleted_key_vaults = true
    }
    log_analytics_workspace {
      permanently_delete_on_destroy = false
    }
    storage {
      data_plane_available = false
    }
  }
  resource_provider_registrations = "none"
}
