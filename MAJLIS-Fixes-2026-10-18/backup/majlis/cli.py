import argparse
import signal
import sys

from .database import Database

# internal Docker network; no host API port published
API_BIND = "0.0.0.0"  # nosec B104


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate", "api", "bot", "notify"])
    args = parser.parse_args(argv)
    try:
        if args.command == "migrate":
            db = Database()
            db.open()
            try:
                db.migrate()
                print("Миграции PostgreSQL применены.")
            finally:
                db.close()
        elif args.command == "api":
            import uvicorn

            from .config import settings

            uvicorn.run(
                "majlis.api:app",
                host=API_BIND,
                port=settings().port,
                access_log=False,
                log_level="warning",
                proxy_headers=False,
            )
        else:
            import threading

            from .bot import notification_worker, run

            stop = threading.Event()
            for name in ("SIGTERM", "SIGINT"):
                if hasattr(signal, name):
                    signal.signal(getattr(signal, name), lambda *_: stop.set())
            (notification_worker if args.command == "notify" else run)(stop)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 -- component boundary must sanitize credential-bearing errors
        # Do not print validation reprs / exception URLs that can include credentials.
        print(
            "Ошибка компонента: "
            + type(exc).__name__
            + ". Проверьте локальные настройки и доступность сервисов.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
