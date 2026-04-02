"""
Patch: Add Used In BOMs tab to Item doctype
"""

def execute():
    from sc_custom.custom_fields import create_sc_custom_fields
    create_sc_custom_fields()
