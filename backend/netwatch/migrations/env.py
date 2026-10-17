from alembic import context

from netwatch.db import metadata

connection = context.config.attributes.get("connection")
if connection is None:  # pragma: no cover - always invoked programmatically
    raise RuntimeError("NetWatch migrations must be run through netwatch.db.run_migrations")

context.configure(connection=connection, target_metadata=metadata, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
