# SMARTPARK database migrations

Production startup does not run `create_all()` or destructive DDL. Apply the
versioned SQL files below using a reviewed Supabase/PostgreSQL migration
workflow, then deploy the application with `AUTO_CREATE_SCHEMA=0`.

For a brand-new, dedicated PostgreSQL database that has been verified empty,
run `database/schema.sql` once to create the base schema, then apply migrations
`000` through `003` in order. `schema.sql` contains `DROP` statements and must
never be run against an existing installation. For an existing installation,
back up the database and apply only unapplied migrations after staging
verification. These numbered migration files are upgrades; they do not create
all base tables on their own.

Before applying a migration:

1. Take a Supabase backup and verify the target database and schema.
2. Run the SQL in an isolated staging database first.
3. Record the migration version in the deployment change log.
4. Run the application smoke tests against staging.

The migrations in this directory are additive/data-preserving. No migration
drops tables, types, users, bookings, payments, or audit records. Apply them
lexicographically (`000_...` through `003_...`) and record successful files in
the `smartpark_migrations` table. Migration `002_entry_modes.sql` adds the
`entry_mode` column required by the current application and defaults existing
tickets to `RESERVATION`. Migration `003_retire_first_deck.sql` marks available
non-ground-level bays as maintenance without deleting slots or ticket history;
currently active reservations and sessions are allowed to finish and their
slots remain retired when released. The application does not run migrations at
startup.

For a disposable PostgreSQL test database, use the repository harness from the
project root:

```bash
export SMARTPARK_TEST_DATABASE_URL='postgresql+psycopg2://...'
python3 scripts/postgres_harness.py verify
python3 scripts/postgres_harness.py fresh --allow-destructive
python3 scripts/postgres_harness.py migrate
python3 scripts/postgres_harness.py verify
```

`fresh` refuses to run unless `--allow-destructive` is supplied,
`SMARTPARK_DISPOSABLE_DB=1` is set, and
`SMARTPARK_DISPOSABLE_DB_NAME` exactly matches `current_database()`. It also
rejects names/hosts that resemble production or shared infrastructure and
requires a recognizably disposable database name such as `smartpark_test`.
The harness never prints the connection URL. Do not set the disposable marker
for a shared, staging, or production database.

Each migration runs in one PostgreSQL transaction with its ledger insert. A
failure rolls back the migration and its ledger entry; the harness exits
non-zero and does not report success. After inspecting the PostgreSQL error and
correcting the schema or migration, rerun `migrate`. Verify the ledger and
schema before retrying; never manually mark a failed migration as applied.
The harness preflights the required legacy tables and columns and will refuse
to modify an incomplete schema.

`bookings_and_tickets.user_id` is nullable by design: existing guest records
remain unowned, while newly authenticated customer bookings are assigned to
the authenticated account. Historical guest records must not be linked by
email or client-supplied identifiers; link them only through a separately
verified support workflow.
