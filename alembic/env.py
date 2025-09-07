# alembic/env.py
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, MetaData
from alembic import context

# --- Make 'app.' imports work when running Alembic from the project root ---
cwd = os.getcwd()
if cwd not in sys.path:
    sys.path.insert(0, cwd)

# Alembic Config object
config = context.config

# Configure Python logging from alembic.ini (if present)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# --- Use the same engine as the application ---
from app.db.session import engine  # must exist

# Import model modules so their Base.metadata is populated
from app.models import (
    user,
    company,
    package,
    company_package,
    invite,
    password_reset,
    ai_system,
    admin_assignment,
    system_assignment,
    ai_assessment,
    task_stats,
    compliance_task,
    notification,
    document,   # documents model
    # NOTE: audit_logs table is written via raw SQL service;
    # it may not have an ORM model on purpose → we protect it below.
)

def _combine_metadata() -> MetaData:
    """
    Merge all per-module Base.metadata into a single MetaData for autogenerate.
    """
    metas = [
        user.Base.metadata,
        company.Base.metadata,
        package.Base.metadata,
        company_package.Base.metadata,
        invite.Base.metadata,
        password_reset.Base.metadata,
        ai_system.Base.metadata,
        admin_assignment.Base.metadata,
        system_assignment.Base.metadata,
        ai_assessment.Base.metadata,
        task_stats.Base.metadata,
        compliance_task.Base.metadata,
        notification.Base.metadata,
        document.Base.metadata,
    ]
    combined = MetaData()
    for m in metas:
        for t in m.tables.values():
            # copy Table definition into combined metadata
            t.tometadata(combined)
    return combined

target_metadata = _combine_metadata()

# Ensure alembic has the same URL even in offline mode
config.set_main_option("sqlalchemy.url", str(engine.url))

def _is_sqlite() -> bool:
    try:
        return engine.url.get_backend_name() == "sqlite"
    except Exception:
        return False

def include_object(object, name, type_, reflected, compare_to):
    """
    Filter which objects Alembic should consider for autogenerate.

    - Skip Alembic's own version table.
    - IMPORTANT: Do NOT propose DROP for DB objects that are present in the
      database (reflected=True) but have no corresponding ORM object
      (compare_to is None). This protects legacy/reporting tables like
      'audit_logs' or anything intentionally managed outside SQLAlchemy.
    """
    if type_ == "table" and name == "alembic_version":
        return False

    # Prevent accidental DROP of tables/indexes/constraints not represented in ORM
    if reflected and compare_to is None and type_ in {
        "table", "index", "unique_constraint", "foreign_key"
    }:
        return False

    return True

def process_revision_directives(context, revision, directives):
    """
    Drop empty autogenerate revisions (keeps the history clean).
    """
    if getattr(context.config.cmd_opts, "autogenerate", False):
        script = directives[0]
        if script.upgrade_ops.is_empty():
            directives[:] = []

def run_migrations_offline():
    """
    Run migrations in 'offline' mode.
    Uses the same DB URL as the application.
    """
    url = str(engine.url)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
        render_as_batch=_is_sqlite(),  # SQLite-friendly ALTER TABLE
        process_revision_directives=process_revision_directives,
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    """
    Run migrations in 'online' mode using the app engine connection.
    """
    connectable = engine

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,
            render_as_batch=_is_sqlite(),  # SQLite-friendly ALTER TABLE
            process_revision_directives=process_revision_directives,
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()