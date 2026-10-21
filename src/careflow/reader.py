"""Provision a local aggregate-only reader, without altering ingestion credentials."""
import re
import secrets

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url


def configure_reader(admin_url, schema="analytics", role="careflow_reader", existing_url=None):
    for value in (schema, role):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
            raise ValueError("Schema and role must be simple lowercase identifiers")
    url = make_url(admin_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("The assistant reader requires PostgreSQL")
    password = secrets.token_urlsafe(32)
    reader_url = url.set(username=role, password=password)
    with psycopg.connect(url.set(drivername="postgresql").render_as_string(hide_password=False)) as connection:
        exists = connection.execute("SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolreplication, rolinherit FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
        if exists:
            # NOINHERIT alone does not prevent SET ROLE into a granted role.
            memberships = connection.execute("SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=%s) LIMIT 1", (role,)).fetchone()
            if not existing_url or make_url(existing_url).username != role or any(exists) or memberships:
                raise ValueError("Existing reader role cannot be adopted; preserve its credentials and configure it explicitly")
            reader_url = make_url(existing_url)
        else:
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)))
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role)))
        for table in ("mart_monthly_utilization", "mart_condition_prevalence"):
            connection.execute(sql.SQL("GRANT SELECT ON TABLE {}.{} TO {}").format(
                sql.Identifier(schema), sql.Identifier(table), sql.Identifier(role)))
        connection.execute(sql.SQL("GRANT SELECT (status) ON TABLE {}.fct_medication_requests TO {}").format(
            sql.Identifier(schema), sql.Identifier(role)))
    return reader_url.render_as_string(hide_password=False)
