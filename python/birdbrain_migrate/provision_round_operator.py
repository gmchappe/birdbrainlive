from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql

from analyze_staging import connect_db

ROLE_NAME = "birdbrain_round_operator"
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / ".env"

# Read access required by the guarded UDisc importer/finalizer and operator preview.
READ_TABLES = (
    "leagues",
    "seasons",
    "rounds",
    "layouts",
    "courses",
    "players",
    "season_memberships",
    "season_player_summaries",
    "round_participants",
    "hole_scores",
    "round_results",
    "playoff_resolutions",
    "handicap_adjustments",
    "handicap_calculations",
    "handicap_calculation_adjustments",
    "player_pool_assignments",
    "sham_pool_round_stats",
    "sham_layout_models",
    "financial_transactions",
    "ace_awards",
    "course_records",
    "round_warning_acknowledgements",
    "round_finalization_receipts",
    "round_udisc_import_receipts",
    "audit_events",
)

# Native workflow writes are intentionally append-only except for round lifecycle
# state and SHAM model upserts. DELETE is never granted.
INSERT_TABLES = (
    "round_participants",
    "hole_scores",
    "round_results",
    "playoff_resolutions",
    "handicap_adjustments",
    "handicap_calculations",
    "handicap_calculation_adjustments",
    "sham_pool_round_stats",
    "sham_layout_models",
    "financial_transactions",
    "ace_awards",
    "course_records",
    "round_warning_acknowledgements",
    "round_finalization_receipts",
    "round_udisc_import_receipts",
    "audit_events",
)

UPDATE_TABLES = (
    "rounds",
    "sham_layout_models",
)

# Explicitly verify that the operator cannot mutate these identity/config/history
# tables even though it needs SELECT on some of them.
DENIED_MUTATION_TABLES = (
    "players",
    "leagues",
    "seasons",
    "layouts",
    "courses",
    "season_memberships",
    "season_player_summaries",
    "player_pool_assignments",
)


def external_login_name(role_name: str) -> str:
    host = os.getenv("BB_DB_HOST", "")
    admin_user = os.getenv("BB_DB_USER", "")
    if ".pooler.supabase.com" in host and "." in admin_user:
        return f"{role_name}.{admin_user.split('.', 1)[1]}"
    return role_name


def update_env_file(user: str, password: str) -> None:
    values = {
        "BB_OPERATOR_DB_USER": user,
        "BB_OPERATOR_DB_PASSWORD": password,
    }
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    seen: set[str] = set()
    updated: list[str] = []

    for line in lines:
        stripped = line.strip()
        replaced = False
        for key, value in values.items():
            if stripped.startswith(f"{key}="):
                updated.append(f"{key}={value}")
                seen.add(key)
                replaced = True
                break
        if not replaced:
            updated.append(line)

    if updated and updated[-1] != "":
        updated.append("")
    for key, value in values.items():
        if key not in seen:
            updated.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(updated) + "\n", encoding="utf-8")


def current_or_new_password() -> str:
    existing = os.getenv("BB_OPERATOR_DB_PASSWORD", "").strip()
    return existing or secrets.token_urlsafe(32)


def role_attributes(cur) -> dict[str, bool] | None:
    cur.execute(
        """
        SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
               rolreplication, rolbypassrls
        FROM pg_roles
        WHERE rolname = %s
        """,
        (ROLE_NAME,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "login": bool(row[0]),
        "superuser": bool(row[1]),
        "createdb": bool(row[2]),
        "createrole": bool(row[3]),
        "replication": bool(row[4]),
        "bypassrls": bool(row[5]),
    }


def assert_not_elevated(attrs: dict[str, bool]) -> None:
    elevated = [
        key
        for key in ("superuser", "createdb", "createrole", "replication", "bypassrls")
        if attrs.get(key)
    ]
    if elevated:
        raise RuntimeError(
            f"Refusing to provision {ROLE_NAME!r}: existing role has elevated "
            f"attributes {elevated}. Resolve it manually first."
        )


def owned_sequences(cur, table_names: tuple[str, ...]) -> list[str]:
    cur.execute(
        """
        SELECT DISTINCT seq.relname
        FROM pg_class tbl
        JOIN pg_namespace ns ON ns.oid = tbl.relnamespace
        JOIN pg_attribute att
          ON att.attrelid = tbl.oid
         AND att.attnum > 0
         AND NOT att.attisdropped
        JOIN pg_depend dep
          ON dep.refobjid = tbl.oid
         AND dep.refobjsubid = att.attnum
         AND dep.deptype IN ('a','i')
        JOIN pg_class seq
          ON seq.oid = dep.objid
         AND seq.relkind = 'S'
        WHERE ns.nspname = 'public'
          AND tbl.relname = ANY(%s)
        ORDER BY seq.relname
        """,
        (list(table_names),),
    )
    return [row[0] for row in cur.fetchall()]


def provision(password: str) -> list[str]:
    conn = connect_db()
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                attrs = role_attributes(cur)
                if attrs is None:
                    cur.execute(
                        sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
                            sql.Identifier(ROLE_NAME),
                            sql.Literal(password),
                        )
                    )
                    attrs = role_attributes(cur)
                    if attrs is None:
                        raise RuntimeError(f"Failed to create role {ROLE_NAME!r}.")
                else:
                    assert_not_elevated(attrs)
                    cur.execute(
                        sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                            sql.Identifier(ROLE_NAME),
                            sql.Literal(password),
                        )
                    )
                    attrs = role_attributes(cur)
                    if attrs is None:
                        raise RuntimeError(f"Role {ROLE_NAME!r} disappeared during provisioning.")

                assert_not_elevated(attrs)

                # This role must not inherit capabilities from another database role.
                cur.execute(
                    """
                    SELECT parent.rolname
                    FROM pg_auth_members m
                    JOIN pg_roles child ON child.oid = m.member
                    JOIN pg_roles parent ON parent.oid = m.roleid
                    WHERE child.rolname = %s
                    """,
                    (ROLE_NAME,),
                )
                memberships = [row[0] for row in cur.fetchall()]
                if memberships:
                    raise RuntimeError(
                        f"Refusing to provision {ROLE_NAME!r}: role inherits memberships "
                        f"{memberships}. Remove them before continuing."
                    )

                cur.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(os.environ["BB_DB_NAME"]),
                        sql.Identifier(ROLE_NAME),
                    )
                )
                cur.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                        sql.Identifier(ROLE_NAME)
                    )
                )

                # Clear any previous direct object grants before rebuilding the allowlist.
                cur.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {}").format(
                        sql.Identifier(ROLE_NAME)
                    )
                )
                cur.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {}").format(
                        sql.Identifier(ROLE_NAME)
                    )
                )

                for table_name in READ_TABLES:
                    cur.execute(
                        sql.SQL("GRANT SELECT ON {} TO {}").format(
                            sql.Identifier(table_name), sql.Identifier(ROLE_NAME)
                        )
                    )
                for table_name in INSERT_TABLES:
                    cur.execute(
                        sql.SQL("GRANT INSERT ON {} TO {}").format(
                            sql.Identifier(table_name), sql.Identifier(ROLE_NAME)
                        )
                    )
                for table_name in UPDATE_TABLES:
                    cur.execute(
                        sql.SQL("GRANT UPDATE ON {} TO {}").format(
                            sql.Identifier(table_name), sql.Identifier(ROLE_NAME)
                        )
                    )

                sequences = owned_sequences(cur, INSERT_TABLES)
                for sequence_name in sequences:
                    cur.execute(
                        sql.SQL("GRANT USAGE ON SEQUENCE {} TO {}").format(
                            sql.Identifier(sequence_name), sql.Identifier(ROLE_NAME)
                        )
                    )

                # Private migration staging is explicitly outside the operator boundary.
                cur.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA migration_staging FROM {}").format(
                        sql.Identifier(ROLE_NAME)
                    )
                )
        return sequences
    finally:
        conn.close()


def operator_connection(user: str, password: str) -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ["BB_DB_HOST"],
        port=int(os.getenv("BB_DB_PORT", "5432")),
        dbname=os.environ["BB_DB_NAME"],
        user=user,
        password=password,
        sslmode=os.getenv("BB_DB_SSLMODE", "require"),
    )


def verify(user: str, password: str, sequences: list[str]) -> None:
    conn = operator_connection(user, password)
    try:
        with conn.cursor() as cur:
            for table_name in READ_TABLES:
                cur.execute("SELECT has_table_privilege(current_user, %s, 'SELECT')", (f"public.{table_name}",))
                if not cur.fetchone()[0]:
                    raise RuntimeError(f"Missing SELECT on {table_name}.")

            for table_name in INSERT_TABLES:
                cur.execute("SELECT has_table_privilege(current_user, %s, 'INSERT')", (f"public.{table_name}",))
                if not cur.fetchone()[0]:
                    raise RuntimeError(f"Missing INSERT on {table_name}.")

            for table_name in UPDATE_TABLES:
                cur.execute("SELECT has_table_privilege(current_user, %s, 'UPDATE')", (f"public.{table_name}",))
                if not cur.fetchone()[0]:
                    raise RuntimeError(f"Missing UPDATE on {table_name}.")

            for table_name in READ_TABLES:
                cur.execute("SELECT has_table_privilege(current_user, %s, 'DELETE')", (f"public.{table_name}",))
                if cur.fetchone()[0]:
                    raise RuntimeError(f"Least-privilege failure: DELETE granted on {table_name}.")

            for table_name in DENIED_MUTATION_TABLES:
                for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                    cur.execute(
                        "SELECT has_table_privilege(current_user, %s, %s)",
                        (f"public.{table_name}", privilege),
                    )
                    if cur.fetchone()[0]:
                        raise RuntimeError(
                            f"Least-privilege failure: {privilege} granted on {table_name}."
                        )

            for sequence_name in sequences:
                cur.execute(
                    "SELECT has_sequence_privilege(current_user, %s, 'USAGE')",
                    (f"public.{sequence_name}",),
                )
                if not cur.fetchone()[0]:
                    raise RuntimeError(f"Missing USAGE on sequence {sequence_name}.")

            cur.execute("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")
            if cur.fetchone()[0]:
                raise RuntimeError("Least-privilege failure: operator can CREATE in public schema.")

            cur.execute("SELECT has_schema_privilege(current_user, 'migration_staging', 'USAGE')")
            if cur.fetchone()[0]:
                raise RuntimeError("Least-privilege failure: operator has migration_staging USAGE.")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Provision the least-privilege PostgreSQL login used by BirdBrain native "
            "round import/finalization tools. Dry-run is the default."
        )
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--no-write-env",
        action="store_true",
        help="Do not save BB_OPERATOR_DB_USER/BB_OPERATOR_DB_PASSWORD to gitignored .env.",
    )
    args = parser.parse_args()

    load_dotenv(ENV_PATH)
    user = external_login_name(ROLE_NAME)

    print("BirdBrain round operator provisioning")
    print("=====================================")
    print(f"Role:           {ROLE_NAME}")
    print(f"External login: {user}")
    print("Boundary:       native UDisc import + results review + transactional finalization")
    print("Denied:         DELETE, DDL/schema CREATE, migration_staging, identity/config mutation")

    if not args.apply:
        print("\nDRY RUN ONLY: no role or password changes were made.")
        return

    password = current_or_new_password()
    sequences = provision(password)
    verify(user, password, sequences)

    if not args.no_write_env:
        update_env_file(user, password)
        print(f"\nSaved operator credentials to gitignored {ENV_PATH.name}.")
    else:
        print("\nCredentials were not written to .env (--no-write-env).")

    print("Least-privilege verification passed:")
    print(f"  readable tables:       {len(READ_TABLES)}")
    print(f"  insertable tables:     {len(INSERT_TABLES)}")
    print(f"  updatable tables:      {len(UPDATE_TABLES)}")
    print(f"  owned sequences:       {len(sequences)}")
    print("  DELETE:                denied on all workflow tables")
    print("  public schema CREATE:  denied")
    print("  migration_staging:     denied")
    print("\nThe password was not printed. Migration/admin BB_DB_* credentials were not changed.")


if __name__ == "__main__":
    main()
