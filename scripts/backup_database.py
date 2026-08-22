"""Run one configured PostgreSQL backup from the command line."""

from app.config.settings import get_settings
from app.services.backup_service import BackupService


def main() -> int:
    result = BackupService(get_settings()).create_backup()
    print(f"Created {result.path} ({result.size_bytes:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
