terraform {
  backend "azurerm" {
    resource_group_name  = "terraform"
    storage_account_name = "gtsdrtterraform"
    container_name       = "tfstate"
    key                  = "executor.tfstate"
    use_azuread_auth     = true
    use_msi              = true
  }
}
