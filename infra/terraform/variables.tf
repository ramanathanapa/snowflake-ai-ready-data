variable "organization_name" { type = string }
variable "account_name" { type = string }
variable "user" { type = string }
variable "private_key_path" { type = string }

variable "credit_quota" {
  type    = number
  default = 100 # trial has ~400 credits; cap well below
}
