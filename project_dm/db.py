from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


load_dotenv()


class DatabaseRole(StrEnum):
    ADMIN = "ADMIN"
    WRITE = "WRITE"
    READ = "READ"


def database_url(role: DatabaseRole) -> str:
    variable = f"DATABASE_URL_{role.value}"
    value = os.getenv(variable)
    if not value:
        raise RuntimeError(f"{variable} is not configured")
    return value


@lru_cache(maxsize=3)
def engine(role: DatabaseRole) -> Engine:
    return create_engine(
        database_url(role),
        pool_pre_ping=True,
        pool_recycle=1_800,
    )


@lru_cache(maxsize=2)
def session_factory(role: DatabaseRole) -> sessionmaker[Session]:
    if role is DatabaseRole.ADMIN:
        raise ValueError("Application sessions must not use the admin role")
    return sessionmaker(
        bind=engine(role),
        autoflush=False,
        expire_on_commit=False,
    )


def write_session() -> Session:
    return session_factory(DatabaseRole.WRITE)()


def read_session() -> Session:
    return session_factory(DatabaseRole.READ)()
