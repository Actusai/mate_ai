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

# Load .env so DATABASE_URL and other vars are available when running Alembic
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

# Alembic Config object
config = context.config

# Configure Python logging from alembic.ini (if present)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# --- Reuse the same engine URL as the application, with a safe fallback ---
engine = None
try:
    from app.db.session import engine as app_engine  # must exist in app runtime
    engine = app_engine
except Exception:
    # Fallback to avoid hard crash if session export changes
    from sqlalchemy import create_engine
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")
    connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
    engine = create_engine(DATABASE_URL, connect_args=connect_args)

# Ensure alembic has the same URL even in offline mode
try:
    config.set_main_option("sqlalchemy.url", str(engine.url))
except Exception:
    pass

# --- Import model modules so their Base.metadata is populated ---------------
# Core models (order matters due to FKs)
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
)

_core_modules = [
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
]

# Optional models (import safely; skip if missing)
_optional_names = (
    "task_stats",
    "compliance_task",
    "notification",
    "document",
    "incident",
    "assessment_approval",
    "regulatory_deadline",
    "calendar_pin",  # ← added
)

_optional_modules = []
for _name in _optional_names:
    try:
        _mod = __import__(f"app.models.{_name}", fromlist=["Base"])
        _optional_modules.append(_mod)
    except Exception:
        pass

def _combine_metadata() -> MetaData:
    """
    Merge all per-module Base.metadata into a single MetaData for autogenerate.
    """
    combined = MetaData()

    def _copy_tables(md):
        # SQLAlchemy 1.4 uses tometadata; 2.0 uses to_metadata
        for t in md.tables.values():
            try:
                t.to_metadata(combined)
            except AttributeError:
                t.tometadata(combined)

    for m in _core_modules + _optional_modules:
        try:
            _copy_tables(m.Base.metadata)
        except Exception:
            continue

    return combined

target_metadata = _combine_metadata()

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