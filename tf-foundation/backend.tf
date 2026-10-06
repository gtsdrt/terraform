terraform {
  backend "azurerm" {
    resource_group_name  = "terraform"
    storage_account_name = "gtsdrtterraform2"
    container_name       = "tfstate-foundation"
    use_azuread_auth     = true
    key                  = "foundation-v2.tfstate"
  }
}
