"""Run `python -m rfp.storage.check` to verify configured R2 bucket access."""

from rfp.core.config import get_settings
from rfp.storage.r2 import CloudflareR2Storage, StorageError


def main() -> None:
    try:
        CloudflareR2Storage(get_settings()).check_bucket()
    except (StorageError, ValueError):
        raise SystemExit(
            "R2 check failed; verify private configuration and bucket permissions"
        ) from None
    print("R2 bucket is accessible")


if __name__ == "__main__":
    main()
