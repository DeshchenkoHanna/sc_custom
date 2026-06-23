# Fibery Sync module

How the **Item → Fibery** synchronization works in this customization.

## General idea

The synchronization uses a reliable-queue pattern: ERPNext **never** talks
to Fibery at the moment an Item is saved. Instead, the change is recorded
locally in a dedicated queue table, and the actual delivery to Fibery is
done by a separate background processor on a schedule. If Fibery is
unreachable, no data is lost — it stays in the queue and is delivered
later.

## Where the code lives

Everything is in the custom app `sc_custom`, inside a new module
**Fibery Sync** at `apps/sc_custom/sc_custom/fibery_sync/`:

- `sync.py` — all business logic: reading configuration, building the
  Fibery request, sending it, populating the queue, handling errors,
  nightly reconciliation.
- `item_events.py` — the handler for the “Item saved” event.
- `doctype/fibery_sync_queue/` — definition of the custom DocType
  **Fibery Sync Queue** (the queue itself).

Wiring in `apps/sc_custom/sc_custom/hooks.py`:

- `doc_events` — the Item `on_update` event calls our handler.
- `scheduler_events` — two cron tasks: process the queue every 5 minutes
  and run a full reconciliation nightly at 02:00.

Configuration (in the site’s `site_config.json`): `fibery_host`,
`fibery_token`, plus the optional `fibery_space`, `fibery_db`. The
Fibery field **names** are NOT in site_config — they live as module
constants `FIBERY_ITEM_CODE_FIELD`, `FIBERY_MODIFIED_FIELD`,
`FIBERY_DESCRIPTION_FIELD` at the top of `sync.py`. Renaming a field in
Fibery is a one-line edit in code.

## The “Fibery Sync Queue” DocType — the queue table

Fields:

- `item_code` — the item code; also the document name (used as a unique
  key — there cannot be two open rows for the same Item).
- `status` — state: `Not Sent` (waiting), `Sending` (in flight),
  `Error` (the last attempt failed).
- `retry` — count of failed attempts.
- `error_code` — short error code (`HTTP_401`, `TIMEOUT`, `CONN`,
  `FIBERY:<Fibery error name>`, `CONFIG`).
- `last_error` — text of the last error (only populated for rows that
  are not yet delivered).
- `last_attempt` — time of the last attempt (used for retry backoff).

When a row is successfully delivered to Fibery, it is **deleted**. So
the table only ever holds work that is still pending or currently
failing. It is a queue, not a log.

## Which Items are eligible for sync

Only Items whose `item_group` is a descendant (recursively) of one of
the roots in `SYNC_ITEM_GROUP_ROOTS` (top of `sync.py`) are pushed to
Fibery — regardless of whether the group is itself a folder or a leaf
(`is_group` is not consulted). Items outside that tree are silently
ignored: not enqueued, not reconciled, not seeded by `enqueue_all`, not
returned by the `sync_items` diagnostic. The resolved set is cached for
5 minutes (via Frappe's cache), so adding or moving subgroups in ERP
becomes effective within 5 minutes without a process restart.

## Who puts an Item into the queue (producers)

An Item can land in the queue in three ways:

1. **The Item save handler** (`item_events.enqueue_item_for_fibery`).
   Whenever an Item is saved (created or modified), the `on_update`
   hook fires. In the **same database transaction** that saves the
   Item, it inserts a row into the queue. There is no Fibery call here;
   it is fast and local. If the Item transaction is rolled back, the
   queue row is rolled back with it. If a row for that `item_code`
   already exists, no duplicate is created (deduplication); if it was
   in `Error` state, its status is reset to `Not Sent` and the retry
   counter is zeroed.

2. **Nightly reconciliation** (`reconcile`).
   Once a day this queries Fibery for all entities in the `Test-Items`
   database and compares them with ERPNext Items by the `ERP Modified`
   field. For any Item that is missing in Fibery or whose stored
   modification time differs from the current one in ERPNext, it puts
   that `item_code` into the same queue. It never writes to Fibery
   directly — it only populates the queue.

3. **Manual operations** (whitelisted methods):
   - `sc_custom.fibery_sync.sync.enqueue_all` — enqueues every existing
     Item (used once during installation to backfill Fibery initially).
   - `sc_custom.fibery_sync.sync.requeue_failed` — moves all `Error`
     rows back to `Not Sent` (manual reset after a long outage).
   - `sc_custom.fibery_sync.sync.sync_items` — a test/diagnostic method
     that pushes a few most-recently-modified Items to Fibery
     **bypassing the queue** (useful to confirm the connection and the
     Fibery fields are configured correctly).

## Who delivers to Fibery (the queue processor)

The `flush_queue` function in `sync.py`. Its job is to walk the queue
and deliver each row to Fibery. The algorithm:

1. Read configuration from `site_config.json`.
2. Pick a batch of rows that make sense to send right now:
   - everything with `Not Sent` status;
   - rows in `Error` whose backoff window has elapsed (the backoff grows
     with the number of failures: `min(retry, 12) × 5` minutes);
   - rows stuck in `Sending` for more than 30 minutes (in case the
     process was interrupted mid-send).
3. For each row: switch it to `Sending`, read the **current** values of
   the Item from the database (`item_code`, `item_name`, `modified`,
   `description`), build the Fibery
   `fibery.entity.batch/create-or-update` command, and send it.
4. Based on the result:
   - Success (HTTP 200 + `success: true`) — the queue row is **deleted**.
   - If the Item was deleted from ERPNext before delivery — the queue
     row is also deleted (nothing to send).
   - Failure (any) — the row stays, `retry` is incremented,
     `error_code` and `last_error` are filled in, `last_attempt` is set
     to now. That automatically defines the next retry delay. An entry
     is also written to the Frappe Error Log.

Each row is processed in its own transaction — a failure on one Item
does not affect the others.

## What exactly is sent to Fibery

The Fibery request is an idempotent “create or update”:

- the conflict key is the `Item Code` field (`conflict-field`);
- if the record already exists — it is updated
  (`conflict-action: update-latest`); if not — it is created;
- the `fibery/id` of every entity is a deterministic UUID derived from
  `item_code`, so re-sending the same Item always hits the same Fibery
  entity (it never creates duplicates, even if Fibery somehow lost the
  Item Code field).

Target Fibery database (default): `ERP-ITM` in space `ERP Dev`. To point
at a different database, set `fibery_db` (and/or `fibery_space`) in
`site_config.json`.

Fields transmitted to Fibery (the **name** of each target field is a
module constant — see top of `sync.py`):

| Fibery field (const) | Fibery type | ERPNext source |
|---|---|---|
| `ITM n°` (`FIBERY_ITEM_CODE_FIELD`) | text | `Item.item_code` (also the conflict-field) |
| `Name` (built-in) | text | `Item.item_name` |
| `ERP Modified` (`FIBERY_MODIFIED_FIELD`) | text | `str(Item.modified)` (used by nightly reconcile as drift marker) |
| `ERP Description` (`FIBERY_DESCRIPTION_FIELD`) | text | `Item.description` with HTML stripped (NOT the built-in rich-text "Description") |
| `Valuation rate` (`FIBERY_VALUATION_FIELD`) | int | `int(round(Item.valuation_rate))` |
| `Main supplier` (`FIBERY_MAIN_SUPPLIER_FIELD`) | text | first row (by `idx`) of `Item Supplier` child table → `supplier` (Supplier ID); `""` if no rows |
| `Main supplier part n°` (`FIBERY_MAIN_SUPPLIER_PART_FIELD`) | text | first row of `Item Supplier` → `supplier_part_no`; `""` if no rows |
| `Has active BOM` (`FIBERY_HAS_BOM_FIELD`) | bool | True iff a BOM exists for this item with `is_active=1` and `docstatus=1` |
| `Has pdf attached` (`FIBERY_HAS_PDF_FIELD`) | bool | True iff at least one File is attached to the Item whose `file_name` ends with `.pdf` (case-insensitive — extension check, no MIME sniff) |
| `Has serial or batch n°` (`FIBERY_HAS_SERIAL_OR_BATCH_FIELD`) | bool | `Item.has_serial_no or Item.has_batch_no` |
| `Item group` (`FIBERY_ITEM_GROUP_FIELD`) | single-select | `Item.item_group` if it matches an existing Fibery option name (see below) |

All listed Fibery fields **must exist** in the target database with the
shown type. Rename a field in Fibery → edit the matching constant.

### Item group resolution

At send time the code fetches the live list of options for the
`Item group` Single Select from Fibery (one extra API call per
flush, cached for 5 minutes). The ERP `item_group` value is matched
**by exact name** against that list:

- match found → the `Item group` field is sent with that value;
- no match → the field is omitted from the payload; whatever value (if
  any) is currently in Fibery is left untouched.

Consequence: adding a new Item Group in ERP under
`SYNC_ITEM_GROUP_ROOTS` AND creating an option with the same name in
the Fibery `Item group` Single Select is enough to start syncing it —
no code change required. The 5-minute cache means the new option
becomes visible to the drainer within 5 minutes (or sooner if the
process restarts / the cache key is cleared).

### Known freshness limitations

These data sources do NOT bump `Item.modified`, so the `on_update` hook
and the nightly reconcile (which compares `Item.modified`) will not
auto-re-enqueue the Item after they change:

- `Valuation rate` — updated by stock movements, which don't save the Item;
- `Has active BOM` — creating/cancelling a BOM doesn't touch the Item;
- `Has pdf attached` — attaching/removing files doesn't touch the Item.

To always reflect these, the Item must be re-saved (or a dedicated hook
added on the relevant doctype to enqueue affected items) — out of scope
for the basic Item sync.

## Where to see status and history

- **Current queue and failures**: the DocType list **Fibery Sync Queue**
  in Desk. If the list is empty, everything has been delivered.
- **Scheduler run log**: the **Scheduled Job Log** list filtered by
  `scheduled_job_type` starting with `sync.` (one entry per run of
  `sync.flush_queue` and `sync.reconcile`).
- **The schedules themselves**: the **Scheduled Job Type** list — the
  records `sync.flush_queue` and `sync.reconcile` show the cron, the
  last run, and a “Run Now” button for a manual trigger.
- **Error logs**: the Error Log in Desk, title “Fibery Sync” (the
  processor also writes there on failed deliveries).

## Manual operations and one-time setup

### One-time install on a new site

After `bench install-app sc_custom` and `bench --site <site> migrate`
the Module Def, the `Fibery Sync Queue` DocType, its table and index,
the Workspace, and the two `Scheduled Job Type` records
(`sync.flush_queue`, `sync.reconcile`) are created automatically.
The following steps cannot live in code and must be done by hand:

1. **Add Fibery credentials** to `sites/<site>/site_config.json`:
   ```json
   {
     "fibery_host":  "youraccount.fibery.io",
     "fibery_token": "…"
   }
   ```
   Optional overrides: `fibery_space` (default `"ERP Dev"`),
   `fibery_db` (default `"Test-Items"`). Fibery **field names** are not
   in site_config — they are module constants at the top of `sync.py`
   (`FIBERY_ITEM_CODE_FIELD`, `FIBERY_MODIFIED_FIELD`,
   `FIBERY_DESCRIPTION_FIELD`).

2. **Create the target fields in Fibery** in the `Test-Items` database
   as plain Text fields:
   - `ERP Modified`
   - `Item Description`

   These are plain text — *not* the built-in rich-text `Description`.
   Without them the upsert fails with
   `entity.error/schema-field-not-found` and the row stays in `Error`.

3. **Enable the scheduler** (production usually has it on by default):
   ```bash
   bench --site <site> scheduler enable
   ```

4. **Backfill the queue** so existing Items are pushed to Fibery
   without waiting for the first nightly reconciliation:
   ```bash
   bench --site <site> execute sc_custom.fibery_sync.sync.enqueue_all
   ```

### Manually invoking the whitelisted methods

All three live in `sc_custom.fibery_sync.sync` and can be called via
HTTP, the bench CLI, or the in-app Console.

| Method | What it does | When to use |
|---|---|---|
| `enqueue_all` | Get-or-create a queue row for every `Item` (idempotent). Does not contact Fibery itself. | Initial backfill, or after restoring from backup to re-seed the queue. |
| `requeue_failed` | Reset every row in `Error` back to `Not Sent` with `retry = 0`. | Manual reset after a long Fibery outage / token rotation. |
| `sync_items` | Push the N most recently modified Items **directly** to Fibery, bypassing the queue. Returns the raw Fibery response. | Smoke test to confirm credentials and field names are correct, without involving the queue. |

Three equivalent ways to call them (replace `<site>` and the method
suffix as needed):

- **HTTP** (authenticated session or API key):
  ```
  POST /api/method/sc_custom.fibery_sync.sync.enqueue_all
  POST /api/method/sc_custom.fibery_sync.sync.requeue_failed
  GET  /api/method/sc_custom.fibery_sync.sync.sync_items?limit=5
  ```

- **bench execute**:
  ```bash
  bench --site <site> execute sc_custom.fibery_sync.sync.enqueue_all
  bench --site <site> execute sc_custom.fibery_sync.sync.requeue_failed
  bench --site <site> execute sc_custom.fibery_sync.sync.sync_items \
        --kwargs "{'limit': 5}"
  ```

- **bench console**:
  ```python
  from sc_custom.fibery_sync.sync import (
      enqueue_all, requeue_failed, sync_items, flush_queue,
  )
  enqueue_all()         # enqueue every Item
  flush_queue()         # ask the drainer to deliver one batch right now
  requeue_failed()      # re-arm everything stuck in Error
  sync_items(limit=5)   # raw smoke test
  ```

### Triggering scheduled jobs on demand

The drainer (`flush_queue`) runs every 5 minutes and the reconciliation
(`reconcile`) runs nightly at 02:00. To run either of them right now
without waiting:

- Open the **Scheduled Job Type** record (`sync.flush_queue` or
  `sync.reconcile`) in Desk and click **Run Now**.
- Or from the console:
  ```python
  frappe.get_doc("Scheduled Job Type", "sync.flush_queue").enqueue(force=True)
  ```

### Manual recovery of a single Item

If an Item is stuck or you want to force-resend it without touching
others, open its row in the **Fibery Sync Queue** list and either set
its status back to `Not Sent` and save, or delete the row and save the
Item — the `on_update` hook will re-enqueue it.
