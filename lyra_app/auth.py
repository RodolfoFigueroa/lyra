import logging

import ee
from google.oauth2.service_account import Credentials

from lyra_app.config import LyraConfig, get_config

logger = logging.getLogger(__name__)


def initialize_earth_engine(config: LyraConfig | None = None) -> None:
    config = get_config() if config is None else config
    service_account_file = config.earth_engine.service_account_file

    if not service_account_file.exists():
        err = f"Service account file not found at: {service_account_file}."
        raise FileNotFoundError(err)

    credentials = Credentials.from_service_account_file(
        service_account_file,
        scopes=["https://www.googleapis.com/auth/earthengine"],
    )
    ee.Initialize(credentials, project=config.earth_engine.project)
    logger.info("Earth Engine initialized successfully!")
