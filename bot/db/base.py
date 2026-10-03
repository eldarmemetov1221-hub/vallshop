"""Инфраструктура БД: async-движок, фабрика сессий, Base."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Базовый класс для всех ORM-моделей."""


class Database:
    """Обёртка над async-движком и фабрикой сессий."""

    def __init__(self, url: str, echo: bool = False) -> None:
        self.engine = create_async_engine(url, echo=echo, future=True)
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )

    async def create_all(self) -> None:
        """Создать таблицы и до-мигрировать недостающие колонки (без Alembic).

        create_all создаёт новые таблицы, но НЕ добавляет колонки в уже
        существующие. Для простых миграций (ADD COLUMN) делаем это вручную —
        безопасно для SQLite и Postgres.
        """
        from . import models  # noqa: F401  (регистрация моделей в метаданных)

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(self._add_missing_columns)

    @staticmethod
    def _add_missing_columns(conn) -> None:
        from sqlalchemy import inspect, text

        inspector = inspect(conn)
        existing_tables = set(inspector.get_table_names())

        # (таблица, колонка, DDL-тип с дефолтом)
        wanted = [
            ("users", "balance", "NUMERIC(12,2) NOT NULL DEFAULT 0"),
            ("orders", "quantity", "INTEGER NOT NULL DEFAULT 1"),
        ]
        for table, column, ddl in wanted:
            if table not in existing_tables:
                continue
            cols = {c["name"] for c in inspector.get_columns(table)}
            if column not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))

    async def dispose(self) -> None:
        await self.engine.dispose()

    def session(self) -> AsyncSession:
        return self.session_factory()
