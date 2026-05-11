import os


class Settings:
    postgres_host: str = os.environ.get("POSTGRES_HOST", "postgres")
    postgres_port: int = int(os.environ.get("POSTGRES_PORT", "5432"))
    postgres_db: str = os.environ.get("POSTGRES_DB", "analytics")
    postgres_user: str = os.environ.get("POSTGRES_USER", "analytics")
    postgres_password: str = os.environ.get("POSTGRES_PASSWORD", "")

    jwt_secret: str = os.environ.get("TELEMETRY_JWT_SECRET", "")
    jwt_algorithm: str = os.environ.get("TELEMETRY_JWT_ALGORITHM", "HS256")

    ingest_queue_size: int = int(os.environ.get("INGEST_QUEUE_SIZE", "10000"))

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
