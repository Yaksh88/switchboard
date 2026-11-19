import uvicorn

from switchboard.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        "switchboard.api.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
