from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql

from analyze_staging import connect_db

ROLE_NAME = "birdbrain_shiny_reader"
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / ".env"

READ_OBJECTS = (
    "v_schedule",
    "v_leaderboard",
    "v_current_all_time",
    "v_course_records",
    "v_aces",
    "v_hall_of_champions",
    # bb_get_schedule() reads the league timezone from this tiny config table.
    "leagues",
)


def external_login_name(role_name: str) -> str:
    """Derive the Supavisor username form from the existing admin username.

    Direct Supabase connections use plain role names. Shared pooler connections
    use role.project_ref. Reuse the suffix already present in BB_DB_USER rather
    than asking the operator to copy project identifiers around.
    """
    host = os.getenv("BB_DB_HOST", "")
    admin_user = os.getenv("BB_DB_USER", "")
    if ".pooler.supabase.com" in host and "." in admin_user:
        return f"{role_name}.{admin_user.split('.', 1)[1]}"
    return role_name


def update_env_file(user: str, password: str) -> None:
    values = {
        "BB_SHINY_DB_USER": user,
        "BB_SHINY_DB_PASSWORD": password,
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
    existing = os.getenv("BB_SHINY_DB_PASSWORD", "").strip()
    return existing or secrets.token_urlsafe(32)


def role_exists(cur) -> bool:
    cur.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)", (ROLE_NAME,))
    return bool(cur.fetchone()[0])


def provision(password: str) -> None:
    conn = connect_db()
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                if not role_exists(cur):
                    cur.execute(
                        sql.SQL(
                            "CREATE ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB "
                            "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                        ).format(sql.Identifier(ROLE_NAME))
                    )

                # ALTER ROLE ... PASSWORD is utility/DDL syntax and PostgreSQL does
                # not accept a bind parameter in this grammar position. Compose the
                # generated secret as a Psycopg SQL literal so quoting/escaping is
                # handled safely without exposing the password in output.
                cur.execute(
                    sql.SQL(
                        "ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                    ).format(
                        sql.Identifier(ROLE_NAME),
                        sql.Literal(password),
                    )
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

                # Start from a deliberately narrow table/view permission set.
                cur.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {}").format(
                        sql.Identifier(ROLE_NAME)
                    )
                )
                for object_name in READ_OBJECTS:
                    cur.execute(
                        sql.SQL("GRANT SELECT ON {} TO {}").format(
                            sql.Identifier(object_name),
                            sql.Identifier(ROLE_NAME),
                        )
                    )
    finally:
        conn.close()


def reader_connection(user: str, password: str) -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ["BB_DB_HOST"],
        port=int(os.getenv("BB_DB_PORT", "5432")),
        dbname=os.environ["BB_DB_NAME"],
        user=user,
        password=password,
        sslmode=os.getenv("BB_DB_SSLMODE", "require"),
    )


def verify(user: str, password: str) -> dict[str, int]:
    conn = reader_connection(user, password)
    try:
        counts: dict[str, int] = {}
        with conn.cursor() as cur:
            for view in READ_OBJECTS[:-1]:
                cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(view)))
                counts[view] = int(cur.fetchone()[0])

            # Verify a normalized base table is not directly readable.
            try:
                cur.execute("SELECT COUNT(*) FROM players")
            except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
            else:
                raise RuntimeError(
                    "Least-privilege verification failed: reader can SELECT from players."
                )
        return counts
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Provision the least-privilege PostgreSQL login used by hosted BirdBrain "
            "Shiny. Dry-run is the default."
        )
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--no-write-env",
        action="store_true",
        help="Do not save BB_SHINY_DB_USER/BB_SHINY_DB_PASSWORD to the gitignored .env.",
    )
    args = parser.parse_args()

    load_dotenv(ENV_PATH)
    user = external_login_name(ROLE_NAME)

    print("BirdBrain Shiny reader provisioning")
    print("===================================")
    print(f"Role:           {ROLE_NAME}")
    print(f"External login: {user}")
    print("Privileges:     CONNECT + public schema USAGE + SELECT on six compatibility views")
    print("                + SELECT on leagues for timezone configuration")
    print("No INSERT/UPDATE/DELETE privileges will be granted.")

    if not args.apply:
        print("\nDRY RUN ONLY: no role or password changes were made.")
        return

    password = current_or_new_password()
    provision(password)
    counts = verify(user, password)

    if not args.no_write_env:
        update_env_file(user, password)
        print(f"\nSaved Shiny-only credentials to gitignored {ENV_PATH.name}.")
    else:
        print("\nCredentials were not written to .env (--no-write-env).")

    print("Read-only verification passed:")
    for view, count in counts.items():
        print(f"  {view:<24} rows={count}")
    print("  players                  SELECT denied (expected)")
    print("\nThe password was not printed. Do not replace the migration/admin BB_DB_* credentials.")


if __name__ == "__main__":
    main()
