import ee
from google.oauth2.service_account import Credentials
import os
from pathlib import Path


def initialize_earth_engine():
    fpath = Path("/app/service-account.json")

    if not fpath.exists():
        err = f"Service account file not found at: {fpath}. Please set the SERVICE_ACCOUNT_FILE_PATH environment variable to the correct path."
        raise FileNotFoundError(err)

    credentials = Credentials.from_service_account_file(
        fpath,
        scopes=["https://www.googleapis.com/auth/earthengine"],
    )
    ee.Initialize(credentials, project=os.environ["EARTHENGINE_PROJECT"])
    print("Earth Engine initialized successfully!")
