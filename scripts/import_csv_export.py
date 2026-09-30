"""Load a CSV data export (``*.csv.zip``) into the database.

For a new computer: ``alembic upgrade head``, then run this against the empty
database. ``--replace`` deletes every existing record first; it asks you to type
REPLACE unless ``--yes`` is given. Nothing is changed if any part fails.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.config.settings import get_settings
from app.database.session import SessionFactory
from app.services.data_import_service import DataImportService, summarise


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a Pesticide Shop CSV data export")
    parser.add_argument("archive", type=Path, help="the .csv.zip written by the data export")
    parser.add_argument("--replace", action="store_true", help="delete every existing record first")
    parser.add_argument("--yes", action="store_true", help="skip the REPLACE confirmation")
    arguments = parser.parse_args()
    if arguments.replace and not arguments.yes:
        answer = input("This deletes every record in the database. Type REPLACE to continue: ")
        if answer.strip() != "REPLACE":
            raise SystemExit("Cancelled; nothing was changed.")
    with SessionFactory.begin() as session:
        result = DataImportService(session, get_settings()).import_archive(
            arguments.archive, replace=arguments.replace
        )
    for line in summarise(result):
        print(line)
    for dataset in result.datasets:
        print(f"  {dataset.label}: {dataset.rows:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
