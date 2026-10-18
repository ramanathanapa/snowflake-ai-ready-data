terraform {
  required_version = ">= 1.6"
  required_providers {
    snowflake = {
      source  = "snowflakedb/snowflake"
      version = "~> 2.0" # ponytail: pin exact after first successful apply; 2.x has had breaking renames
    }
  }
}

provider "snowflake" {
  organization_name = var.organization_name
  account_name      = var.account_name
  user              = var.user
  role              = "ACCOUNTADMIN" # bootstrap only; move to a least-privilege TF role later
  authenticator     = "SNOWFLAKE_JWT"
  private_key       = file(var.private_key_path)
}

# --- cost guardrail: hard stop before the trial credits are gone ---
resource "snowflake_resource_monitor" "fund" {
  name            = "FUND_RM"
  credit_quota    = var.credit_quota
  frequency       = "MONTHLY"
  start_timestamp = "IMMEDIATELY"
  notify_triggers = [50, 80]
  suspend_trigger = 100
}

resource "snowflake_warehouse" "fund" {
  name                = "FUND_WH"
  warehouse_size      = "XSMALL"
  auto_suspend        = 60
  auto_resume         = true
  initially_suspended = true
  resource_monitor    = snowflake_resource_monitor.fund.fully_qualified_name
}

resource "snowflake_database" "fund" {
  name = "FUND_AI"
}

resource "snowflake_schema" "s" {
  for_each = toset(["RAW", "CURATED", "ANALYTICS", "APP"])
  database = snowflake_database.fund.name
  name     = each.key
}

# --- roles: engineer builds, PM reads analytics + uses the app ---
resource "snowflake_account_role" "engineer" { name = "FUND_ENGINEER" }
resource "snowflake_account_role" "pm" { name = "FUND_PM" }

resource "snowflake_grant_privileges_to_account_role" "wh" {
  for_each          = { eng = snowflake_account_role.engineer.name, pm = snowflake_account_role.pm.name }
  account_role_name = each.value
  privileges        = ["USAGE"]
  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.fund.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "db" {
  for_each          = { eng = snowflake_account_role.engineer.name, pm = snowflake_account_role.pm.name }
  account_role_name = each.value
  privileges        = ["USAGE"]
  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.fund.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "eng_schema" {
  for_each          = snowflake_schema.s
  account_role_name = snowflake_account_role.engineer.name
  all_privileges    = true
  on_schema {
    schema_name = each.value.fully_qualified_name
  }
}

resource "snowflake_grant_privileges_to_account_role" "pm_schema" {
  for_each          = { for k, v in snowflake_schema.s : k => v if contains(["ANALYTICS", "APP"], k) }
  account_role_name = snowflake_account_role.pm.name
  privileges        = ["USAGE"]
  on_schema {
    schema_name = each.value.fully_qualified_name
  }
}

# engineer inherits to ACCOUNTADMIN's user so you can work as FUND_ENGINEER
resource "snowflake_grant_account_role" "eng_to_user" {
  role_name = snowflake_account_role.engineer.name
  user_name = var.user
}

# PM reads the ANALYTICS views only. Views run with owner (FUND_ENGINEER) rights, so no access to RAW/CURATED is needed.
# Future grant covers views created or CREATE OR REPLACEd later; the "all" grant covers ones that already exist.
resource "snowflake_grant_privileges_to_account_role" "pm_views_future" {
  account_role_name = snowflake_account_role.pm.name
  privileges        = ["SELECT"]
  on_schema_object {
    future {
      object_type_plural = "VIEWS"
      in_schema          = snowflake_schema.s["ANALYTICS"].fully_qualified_name
    }
  }
}

resource "snowflake_grant_privileges_to_account_role" "pm_views_all" {
  account_role_name = snowflake_account_role.pm.name
  privileges        = ["SELECT"]
  on_schema_object {
    all {
      object_type_plural = "VIEWS"
      in_schema          = snowflake_schema.s["ANALYTICS"].fully_qualified_name
    }
  }
}

# PM may open the Streamlit app(s) in APP. The app runs with owner (FUND_ENGINEER) rights.
resource "snowflake_grant_privileges_to_account_role" "pm_streamlit_future" {
  account_role_name = snowflake_account_role.pm.name
  privileges        = ["USAGE"]
  on_schema_object {
    future {
      object_type_plural = "STREAMLITS"
      in_schema          = snowflake_schema.s["APP"].fully_qualified_name
    }
  }
}
