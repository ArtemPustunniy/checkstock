from alembic import context
from sqlalchemy import create_engine

from app.config import settings
from app.models import Base

target_metadata = Base.metadata


def run_migrations() -> None:
    url = context.config.attributes.get("database_url") or settings.database_url
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations()
