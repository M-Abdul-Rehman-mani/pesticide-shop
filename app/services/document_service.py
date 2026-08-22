"""Concurrency-safe human-readable document numbering."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.settings import DocumentSequence


class DocumentNumberService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def next_number(self, name: str, prefix: str, at: datetime) -> str:
        year = at.year
        self._session.execute(
            insert(DocumentSequence)
            .values(name=name, prefix=prefix, year=year, current_value=0)
            .on_conflict_do_nothing(index_elements=[DocumentSequence.name])
        )
        sequence = self._session.execute(
            select(DocumentSequence)
            .where(DocumentSequence.name == name)
            .with_for_update(of=DocumentSequence)
        ).scalar_one()
        if sequence.year != year:
            sequence.year = year
            sequence.current_value = 0
        sequence.prefix = prefix
        sequence.current_value += 1
        self._session.flush()
        return f"{prefix}-{year}-{sequence.current_value:06d}"
