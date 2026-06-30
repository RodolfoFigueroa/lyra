from sqlalchemy.engine import URL, create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from lyra_app.config import LyraConfig, get_config


def database_url(drivername: str, config: LyraConfig | None = None) -> URL:
    config = get_config() if config is None else config
    return URL.create(
        drivername,
        username=config.database.user,
        password=config.database.read_password(),
        host=config.database.host,
        port=config.database.port,
        database=config.database.name,
    )


engine = create_engine(database_url("postgresql+psycopg2"))

async_engine = create_async_engine(database_url("postgresql+asyncpg"))
