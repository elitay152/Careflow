from sqlalchemy import (
    JSON, Column, ForeignKey, Integer, MetaData, String, Table, Text,
    create_engine, event,
)

metadata = MetaData()
patients = Table(
    "patients", metadata,
    Column("id", String, primary_key=True),
    Column("birth_date", String, nullable=False),
    Column("gender", String, nullable=False),
    Column("source_hash", String, nullable=False),
)
encounters = Table(
    "encounters", metadata,
    Column("id", String, primary_key=True),
    Column("patient_id", String, ForeignKey("patients.id"), nullable=False),
    Column("status", String, nullable=False),
    Column("started_at", String, nullable=False),
    Column("ended_at", String),
    Column("source_hash", String, nullable=False),
)
conditions = Table(
    "conditions", metadata,
    Column("id", String, primary_key=True),
    Column("patient_id", String, ForeignKey("patients.id"), nullable=False),
    Column("code", String, nullable=False),
    Column("code_system", String, nullable=False),
    Column("display", String, nullable=False),
    Column("clinical_status", String, nullable=False),
    Column("verification_status", String, nullable=False),
    Column("onset_date", String),
    Column("source_hash", String, nullable=False),
)
medications = Table(
    "medications", metadata,
    Column("id", String, primary_key=True),
    Column("patient_id", String, ForeignKey("patients.id"), nullable=False),
    Column("code", String, nullable=False),
    Column("code_system", String, nullable=False),
    Column("display", String, nullable=False),
    Column("status", String, nullable=False),
    Column("authored_date", String),
    Column("source_hash", String, nullable=False),
)
loads = Table(
    "loads", metadata,
    Column("source_hash", String, primary_key=True),
    Column("source_path", Text, nullable=False),
    Column("loaded_at", String, nullable=False),
    Column("accepted", Integer, nullable=False),
    Column("rejected", Integer, nullable=False),
    Column("ignored", Integer, nullable=False),
)
quarantine = Table(
    "quarantine", metadata,
    Column("id", String, primary_key=True),
    Column("source_hash", String, nullable=False),
    Column("entry_index", Integer, nullable=False),
    Column("reason", Text, nullable=False),
    Column("resource", JSON, nullable=False),
)


def connect(url):
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        raise ValueError("Supported databases: SQLite and PostgreSQL")
    metadata.create_all(engine)
    return engine


def upsert(connection, table, values):
    if connection.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    statement = insert(table).values(**values)
    connection.execute(statement.on_conflict_do_update(
        index_elements=[table.c.id],
        set_={key: statement.excluded[key] for key in values if key != "id"},
    ))
