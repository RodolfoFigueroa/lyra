import ee
from google.oauth2.service_account import Credentials

KEY_FILE = '/app/service-account.json'
SCOPES = ['https://www.googleapis.com/auth/earthengine']

def initialize_earth_engine():
    try:
        credentials = Credentials.from_service_account_file(KEY_FILE, scopes=SCOPES)
        ee.Initialize(credentials)
        print("Earth Engine initialized successfully!")
    except Exception as e:
        print(f"Error initializing Earth Engine: {e}")