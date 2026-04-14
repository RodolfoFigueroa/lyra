import ee
from google.oauth2.service_account import Credentials


def initialize_earth_engine():
    try:
        credentials = Credentials.from_service_account_file(
            "/app/service-account.json",
            scopes=["https://www.googleapis.com/auth/earthengine"],
        )
        ee.Initialize(credentials)
        print("Earth Engine initialized successfully!")
    except Exception as e:
        print(f"Error initializing Earth Engine: {e}")
