# Public demo (Streamlit Community Cloud): a key-pair-only service user with a read-only role
# on its own capped warehouse, so anonymous visitors can only read ANALYTICS and spend at most
# demo_credit_quota a month. Delete this file and apply to take the demo down.

resource "snowflake_resource_monitor" "demo" {
  name            = "FUND_DEMO_RM"
  credit_quota    = var.demo_credit_quota
  frequency       = "MONTHLY"
  start_timestamp = "IMMEDIATELY"
  notify_triggers = [50, 80]
  suspend_trigger = 100
}

resource "snowflake_warehouse" "demo" {
  name                         = "FUND_DEMO_WH"
  warehouse_size               = "XSMALL"
  auto_suspend                 = 60
  auto_resume                  = true
  initially_suspended          = true
  statement_timeout_in_seconds = 60
  resource_monitor             = snowflake_resource_monitor.demo.fully_qualified_name
}

resource "snowflake_account_role" "public" { name = "FUND_PUBLIC" }

resource "snowflake_grant_privileges_to_account_role" "public_wh" {
  account_role_name = snowflake_account_role.public.name
  privileges        = ["USAGE"]
  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.demo.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "public_db" {
  account_role_name = snowflake_account_role.public.name
  privileges        = ["USAGE"]
  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.fund.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "public_schema" {
  for_each          = toset(["ANALYTICS", "APP"])
  account_role_name = snowflake_account_role.public.name
  privileges        = ["USAGE"]
  on_schema {
    schema_name = snowflake_schema.s[each.key].fully_qualified_name
  }
}

resource "snowflake_grant_privileges_to_account_role" "public_views_all" {
  account_role_name = snowflake_account_role.public.name
  privileges        = ["SELECT"]
  on_schema_object {
    all {
      object_type_plural = "VIEWS"
      in_schema          = snowflake_schema.s["ANALYTICS"].fully_qualified_name
    }
  }
}

resource "snowflake_grant_privileges_to_account_role" "public_views_future" {
  account_role_name = snowflake_account_role.public.name
  privileges        = ["SELECT"]
  on_schema_object {
    future {
      object_type_plural = "VIEWS"
      in_schema          = snowflake_schema.s["ANALYTICS"].fully_qualified_name
    }
  }
}

# Cortex Analyst reads the semantic model file from this stage (created by migration V009).
# Cortex itself comes from SNOWFLAKE.CORTEX_USER, which PUBLIC holds by default.
resource "snowflake_grant_privileges_to_account_role" "public_semantic_stage" {
  account_role_name = snowflake_account_role.public.name
  privileges        = ["READ"]
  on_schema_object {
    object_type = "STAGE"
    object_name = "${snowflake_schema.s["APP"].fully_qualified_name}.\"SEMANTIC_STAGE\""
  }
}

resource "snowflake_service_user" "demo" {
  name              = "SVC_FUND_DEMO"
  default_role      = snowflake_account_role.public.name
  default_warehouse = snowflake_warehouse.demo.name
  rsa_public_key    = var.demo_public_key
  comment           = "Public Streamlit demo; key-pair only, read-only role"
}

resource "snowflake_grant_account_role" "public_to_demo" {
  role_name = snowflake_account_role.public.name
  user_name = snowflake_service_user.demo.name
}
