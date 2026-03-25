# SC Custom

Storage-as-Inventory-Dimension system for ERPNext. Tracks warehouse → storage → batch/serial hierarchies through manufacturing, procurement, and delivery workflows.

## Prerequisites

This app requires the following standard ERPNext configuration:

1. **DocType "Storage"** — must exist as a standalone DocType (created manually)
2. **Inventory Dimension "Storage"** — configured in ERPNext with:
   - **Apply to All Inventory Documents** enabled
   - **Validate Negative Stock** enabled
   - **Mandatory Depends On** is not set (not mandatory)

The app builds on top of these standard ERPNext Inventory Dimension features by adding storage-aware logic for serial/batch handling, Pick List allocation, manufacturing workflows, and automated storage inheritance.

## Custom Fields

| DocType | Field | Type | Description |
|---------|-------|------|-------------|
| Pick List Item | `storage` | Link → Storage | Storage location for picked items |
| Delivery Note Item | `default_storage` | Link → Storage | Auto-populated default storage (read-only) |
| Delivery Note Item | `storage` | Link → Storage | Source storage |
| Manufacturing Settings | `default_wip_storage` | Link → Storage | Default WIP storage for manufacturing |
| Manufacturing Settings | `default_fg_storage` | Link → Storage | Default Finished Goods storage |
| Work Order | `wip_storage` | Link → Storage | WIP storage for this Work Order |
| Work Order | `fg_storage` | Link → Storage | Finished Goods storage for this Work Order |
| Serial No | `storage` | Link → Storage | Current storage location (read-only) |
| Serial and Batch Bundle | `storage` | Link → Storage | Storage for the bundle |
| Serial and Batch Entry | `storage` | Link → Storage | Storage per entry row |
| Stock Reservation Entry | `storage` | Link → Storage | Reserved storage |
| Subcontracting Order | `supplier_storage` | Link → Storage | Supplier's storage location |
| Purchase Receipt Item | `storage` | Link → Storage | Target storage |
| Purchase Invoice Item | `storage` | Link → Storage | Target storage (when Update Stock enabled) |
| Sales Invoice Item | `storage` | Link → Storage | Source storage (when Update Stock enabled) |

## Features

### 1. Storage Inheritance & Auto-Population

- **Pick List → Stock Entry**: Storage locations automatically copied to Stock Entry items
- **Stock Entry**: Auto-sets storage from Pick List, FIFO stock, or Manufacturing Settings defaults
- **Delivery Note**: `default_storage` field shows suggested storage based on available stock (FIFO)
- **Work Order**: Auto-populates `wip_storage` and `fg_storage` from Manufacturing Settings defaults
- **Material Consumption/Manufacture**: Inherits storage from transfer Stock Entries or Manufacturing Settings

### 2. Storage-Aware Serial/Batch Handling

Monkey-patches ERPNext's Serial and Batch Bundle system to be storage-aware:
- Batch availability queries filtered by storage
- Serial number availability filtered by storage
- SABB validation respects storage boundaries
- Auto-created bundles inherit storage from source documents
- Serial/Batch dialog enhanced with Storage field for all relevant doctypes

### 3. Pick List Override

Full override of `set_item_locations` to include storage in the dedup key:
- Keeps separate rows for same item + warehouse + batch but different storage
- Respects Work Order source warehouses when allocating storage
- Expands locations by storage: splits qty across multiple storages per FIFO
- Handles serialized, batch-only, and plain items with storage breakdown

### 4. Manufacturing & Subcontracting

- **Material Transfer for Manufacture**: Copies storage from Pick List items
- **Material Consumption**: Inherits storage from transfer STEs or defaults
- **Manufacture**: Raw material storage from transfer, finished goods from WO/defaults
- **Send to Subcontractor**: Uses SCO's `supplier_storage`
- **Subcontracting Receipt**: Auto-populates storage, serial_no, batch_no from Send to Subcontractor STEs' SABBs

### 5. Batch Dashboard

Custom batch dashboard displays stock levels grouped by warehouse + storage with "Move" and "Split" buttons per row.

### 6. Storage Validations (from 01.01.2026)

> **Note:** Storage validation only applies to items where `is_stock_item = 1`. Non-stock items are skipped.
> Documents with posting_date before 2026-01-01 are exempt (backward compatibility).

> **Note on field names:** Different DocTypes use different field names for storage:
> - `Stock Entry Detail`: `storage` (source), `to_storage` (target)
> - `Purchase Receipt Item`: `storage` (target - despite the name)
> - `Delivery Note Item`: `storage` (source)
> - `Purchase Invoice Item`: `storage` (target - despite the name)
> - `Sales Invoice Item`: `storage` (source)

#### Stock Entry

| Purpose | Source Storage | Target Storage |
|---------|---------------|----------------|
| Material Receipt | - | Required |
| Material Issue | Required | - |
| Material Transfer | Required | Required |
| Material Transfer for Manufacture | Required | Required |
| Material Consumption for Manufacture | Required | - |
| Send to Subcontractor | Required | Required |
| Disassemble | Required | Required |
| Repack | Raw: Required | Finished: Required |
| Manufacture | Raw: Required | Finished: Required |

#### Other Documents

| Document | Source Storage | Target Storage |
|----------|---------------|----------------|
| Purchase Receipt | - | Required |
| Delivery Note | Required | - |
| Purchase Invoice (Update Stock) | - | Required |
| Sales Invoice (Update Stock) | Required | - |

### 7. Storage Synchronization

- SLE.storage → SABB.storage → SABE.storage (on SLE after_insert)
- SABB.storage → Serial No.storage (inward: set, outward: clear)
- Pick List.storage → Stock Entry.storage (cascading from source items)
- Warehouse change → clears storage and batch; Storage change → clears batch

### 8. STE vs Pick List Comparison

Before-submit warning if Stock Entry items differ from Pick List source (warns on storage/batch/serial changes).

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `stock_entry.get_transfer_inward_items` | Get inward data from Material Transfer for Manufacture STEs |
| `stock_entry.check_ste_pl_differences` | Compare STE items vs Pick List source |
| `batch_storage.get_batch_qty_by_storage` | Batch stock levels grouped by warehouse + storage |
| `delivery_note_storage.get_default_storage_for_item` | FIFO storage selector (excludes WIP) |
| `delivery_note_storage.get_default_storage_for_items` | Batch version of above |
| `pick_list_storage.get_pick_list_items_storage` | Fetch PL items with storage |
| `pick_list_storage.get_available_stock_for_items` | Allocate stock by warehouse + storage + batch/serial |
| `queries.get_batch_no` | Batch dropdown filtered by storage |
| `queries.get_storage` | Storage dropdown with available qty |
| `queries.get_storage_for_autocomplete` | Storage autocomplete for dialogs |
| `queries.get_auto_batch_nos_with_storage` | Auto-fetch batches by storage |
| `queries.get_serial_no` | Serial dropdown filtered by storage |
| `queries.get_auto_serial_nos_with_storage` | Auto-fetch serials by storage |
| `queries.set_bundle_storage` | Direct SABB storage setter |
| `queries.get_default_storage` | Manufacturing Settings default storages |

## Overrides

| Type | Target | Purpose |
|------|--------|---------|
| `override_doctype_class` | Stock Ledger Entry | Enhanced error messages with row numbers |
| Monkey-patch | Pick List `set_item_locations` | Storage-aware item allocation |
| Monkey-patch | SABB batch/serial availability queries | Storage-filtered availability |
| Monkey-patch | SABB validation methods | Storage-aware inventory validation |
| Monkey-patch | SABB creation methods | Storage injection at bundle creation |
| Monkey-patch | SRE reservation queries | Storage-filtered reservations |

## Installation

```bash
bench get-app https://github.com/DeshchenkoHanna/sc_custom.git
bench --site [site-name] install-app sc_custom
bench --site [site-name] migrate
```

## License

MIT
