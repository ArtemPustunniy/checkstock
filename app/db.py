from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Сессия на запрос. Транзакцию открывает обработчик (`with session.begin()`),
    чтобы границы атомарной операции были видны прямо в коде эндпоинта."""
    with SessionLocal() as session:
        yield session
