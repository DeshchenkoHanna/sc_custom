"""
Patch: Create/Update SC Custom Fields

This patch ensures custom fields are created during app upgrade
"""

import frappe
from frappe import _


def execute():
    """
    Create custom fields for SC Custom app
    Runs during bench migrate
    """
    frappe.logger().info("Creating/updating SC Custom fields...")

    # Import and run custom field creation
    from sc_custom.custom_fields import create_sc_custom_fields

    create_sc_custom_fields()

    frappe.logger().info("SC Custom fields created/updated successfully")
    print("✅ SC Custom: Custom fields created/updated successfully")
