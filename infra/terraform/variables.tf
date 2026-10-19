variable "organization_name" { type = string }
variable "account_name" { type = string }
variable "user" { type = string }
variable "private_key_path" { type = string }

variable "credit_quota" {
  type    = number
  default = 100 # trial has ~400 credits; cap well below
}

variable "demo_public_key" {
  type        = string
  description = "Public key body for SVC_FUND_DEMO (no BEGIN/END lines)"
}

variable "demo_credit_quota" {
  type    = number
  default = 5 # hard monthly cap for anonymous demo traffic
}
