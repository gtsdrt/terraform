terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}
  # 认证靠容器环境变量 ARM_USE_MSI / ARM_CLIENT_ID 等，这里不用写凭据
}
