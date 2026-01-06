# SC Custom

Custom ERPNext app for storage location tracking in stock transactions.

## Custom Fields

| DocType | Field | Type | Description |
|---------|-------|------|-------------|
| Pick List Item | `storage` | Link → Storage | Storage location for picked items |
| Delivery Note Item | `default_storage` | Link → Storage | Auto-populated default storage (read-only) |
| Manufacturing Settings | `default_wip_storage` | Link → Storage | Default WIP storage for manufacturing |
| Manufacturing Settings | `default_fg_storage` | Link → Storage | Default Finished Goods storage |

## Features

### 1. Storage Inheritance (Pick List → Stock Entry)
When creating Stock Entry from Pick List, storage locations are automatically copied to Stock Entry items.

### 2. Default Storage Auto-Population
- **Stock Entry**: Auto-sets storage from Pick List, FIFO stock, or Manufacturing Settings
- **Delivery Note**: `default_storage` field shows suggested storage based on available stock (FIFO)

### 3. Storage Validations (from 01.01.2026)

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

## Installation

```bash
bench get-app https://github.com/DeshchenkoHanna/sc_custom.git
bench --site [site-name] install-app sc_custom
bench --site [site-name] migrate
```

## Contributing

This app uses `pre-commit` for code formatting and linting:

```bash
cd apps/sc_custom
pre-commit install
```

## License

MIT
