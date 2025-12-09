"""
Installation and Setup for SC Custom App
"""

import frappe
from frappe import _


def after_install():
    """Run after app installation"""
    try:
        # Create custom fields
        from sc_custom.custom_fields import create_sc_custom_fields
        create_sc_custom_fields()

        frappe.logger().info("SC Custom: Custom fields created successfully")
        print("✅ SC Custom: Custom fields created successfully")

    except Exception as e:
        frappe.log_error(f"Error during SC Custom installation: {e!s}")
        frappe.throw(f"Installation failed: {e!s}")
