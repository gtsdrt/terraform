variable "vnet_count" {
  description = "要创建的 VNet 数量"
  type        = number
}

variable "resource_group_name" {
  description = "资源组名称"
  type        = string
}

variable "location" {
  description = "部署区域"
  type        = string
  default     = "norwayeast"
}
