terraform {
  backend "azurerm" {
    resource_group_name  = "terraform"
    storage_account_name = "gtsdrtterraform"
    container_name       = "tfstate"
    key                  = "terraform-test.tfstate"
  }
}
