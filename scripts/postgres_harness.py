"""Explicit, non-startup PostgreSQL migration harness for SMARTPARK.

This tool is deliberately conservative: it requires an explicit database URL,
never prints credentials, and refuses destructive initialization without an
explicit disposable-database marker.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
SCHEMA = ROOT / "database" / "schema.sql"


def _url() -> str:
    value = os.environ.get("SMARTPARK_TEST_DATABASE_URL")
    if not value or not value.startswith(("postgresql://", "postgresql+psycopg2://")):
        raise SystemExit("Set SMARTPARK_TEST_DATABASE_URL to an isolated PostgreSQL URL")
    return value


def _engine():
    return create_engine(_url(), connect_args={"connect_timeout": 10})


def _identity(connection):
    return connection.execute(text(
        "select current_database(), current_user, inet_server_addr()"
    )).one()


def _connect():
    try:
        return _engine().connect()
    except SQLAlchemyError as exc:
        raise SystemExit("PostgreSQL connection failed; credentials and connection details were withheld") from None


def verify() -> tuple[str, str, str]:
    try:
        with _connect() as connection:
            row = _identity(connection)
            print(f"database={row[0]} user={row[1]} server={row[2]}")
            return tuple(str(value) for value in row)
    except SQLAlchemyError:
        raise SystemExit("PostgreSQL verification failed; database details were withheld") from None


def _assert_disposable(identity) -> None:
    database_name, _user, server = identity
    expected_name = os.environ.get("SMARTPARK_DISPOSABLE_DB_NAME")
    if not expected_name or expected_name != database_name:
        raise SystemExit(
            "Refusing destructive setup: SMARTPARK_DISPOSABLE_DB_NAME must exactly match current_database()"
        )
    suspicious = ("prod", "production", "shared", "primary", "live", "supabase")
    target_text = f"{database_name} {server or ''}".lower()
    if any(marker in target_text for marker in suspicious):
        raise SystemExit("Refusing destructive setup: target identity resembles a shared or production database")
    if not any(marker in database_name.lower() for marker in ("test", "dev", "local", "scratch", "temp")):
        raise SystemExit("Refusing destructive setup: database name is not recognizably disposable")


def _assert_expected_schema(connection) -> None:
    required = {
        "users": set(),
        "parking_slots": {"id", "status", "slot_type", "distance_from_entry"},
        "bookings_and_tickets": {"id", "vehicle_number", "status", "entry_time"},
        "gate_logs": {"vehicle_number", "timestamp"},
    }
    rows = connection.execute(text(
        "select table_name, column_name from information_schema.columns "
        "where table_schema = 'public' and table_name in "
        "('users', 'parking_slots', 'bookings_and_tickets', 'gate_logs')"
    ))
    actual: dict[str, set[str]] = {}
    for table_name, column_name in rows:
        actual.setdefault(table_name, set()).add(column_name)
    missing_tables = set(required) - set(actual)
    missing_columns = {
        table: sorted(columns - actual.get(table, set()))
        for table, columns in required.items()
        if columns - actual.get(table, set())
    }
    if missing_tables or missing_columns:
        raise SystemExit(
            f"Refusing migration: expected schema is incomplete; missing_tables={sorted(missing_tables)} "
            f"missing_columns={missing_columns}"
        )


def _split_sql(source: str) -> list[str]:
    # Repository SQL files contain statements only; this intentionally avoids
    # pretending to be a general PostgreSQL parser.
    return [statement.strip() for statement in source.split(";") if statement.strip()]


def fresh() -> None:
    if os.environ.get("SMARTPARK_DISPOSABLE_DB") != "1":
        raise SystemExit("Refusing destructive fresh setup: set SMARTPARK_DISPOSABLE_DB=1")
    with _connect() as connection:
        identity = _identity(connection)
    _assert_disposable(identity)
    print("WARNING: running destructive database/schema.sql against the verified disposable database")
    try:
        with _engine().begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS smartpark_migrations"))
            for statement in _split_sql(SCHEMA.read_text(encoding="utf-8")):
                connection.execute(text(statement))
    except SQLAlchemyError:
        raise SystemExit("Fresh schema failed and was rolled back; database details were withheld") from None


def migrate() -> None:
    files = sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
    try:
        with _engine().begin() as connection:
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS smartpark_migrations "
                "(version varchar(128) PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            ))
            _assert_expected_schema(connection)
            for path in files:
                version = path.name
                applied = connection.execute(
                    text("SELECT 1 FROM smartpark_migrations WHERE version = :version"),
                    {"version": version},
                ).scalar()
                if applied:
                    print(f"skip {version}")
                    continue
                print(f"apply {version}")
                for statement in _split_sql(path.read_text(encoding="utf-8")):
                    connection.execute(text(statement))
                connection.execute(
                    text("INSERT INTO smartpark_migrations(version) VALUES (:version)"),
                    {"version": version},
                )
    except SQLAlchemyError:
        raise SystemExit(
            "Migration failed; the PostgreSQL transaction was rolled back. "
            "Inspect the database, correct the schema or SQL, then rerun."
        ) from None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("verify", "fresh", "migrate"))
    parser.add_argument("--allow-destructive", action="store_true")
    args = parser.parse_args()
    identity = verify()
    if args.command == "verify":
        return
    if args.command == "fresh":
        if not args.allow_destructive:
            raise SystemExit("Refusing fresh setup without --allow-destructive")
        fresh()
    else:
        migrate()


if __name__ == "__main__":
    main()
