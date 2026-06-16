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
`fibery_token`, plus the optional `fibery_space`, `fibery_db`,
`fibery_modified_field`, `fibery_description_field`.

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

Fields transmitted to Fibery:

- `Item Code` ← `item_code`
- `Name` ← `item_name`
- `ERP Modified` ← string from the ERPNext `modified` field (used by
  the nightly reconciliation as a state marker)
- `Item Description` ← ERPNext `description` with the HTML markup
  stripped to plain text (`<div><p>…</p></div>` → plain text)

The names of these last three plain-text fields in Fibery
(`ERP Modified` and `Item Description`) are plain Text fields that
**must exist** in the `Test-Items` database. The names can be
overridden via the `fibery_modified_field` / `fibery_description_field`
keys in `site_config.json`.

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
