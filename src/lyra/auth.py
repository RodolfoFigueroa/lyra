import ee
from google.oauth2.service_account import Credentials
import os
from pathlib import Path


def initialize_earth_engine():
    try:
        fpath = Path(
            os.environ.get("SERVICE_ACCOUNT_FILE_PATH", "/app/service-account.json")
        )
        credentials = Credentials.from_service_account_file(
            fpath,
            scopes=["https://www.googleapis.com/auth/earthengine"],
        )
        ee.Initialize(credentials, project=os.environ["EARTHENGINE_PROJECT"])
        print("Earth Engine initialized successfully!")
    except Exception as e:
        print(f"Error initializing Earth Engine: {e}")
