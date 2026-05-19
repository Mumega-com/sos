
import os
import os.path
import json
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from sos.kernel import Config
from sos.observability.logging import get_logger

log = get_logger("google_auth")

# If modifying these scopes, delete the file token.json.
SCOPES = [
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/spreadsheets.readonly'
]

def get_google_credentials():
    """
    Handles the OAuth2 flow and returns valid credentials.
    """
    config = Config.load()
    data_dir = config.paths.home_dir / "google"
    data_dir.mkdir(parents=True, exist_ok=True)
    
    token_path = data_dir / 'token.json'
    creds_path = data_dir / 'credentials.json'

    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.info("Refreshing Google credentials...")
            creds.refresh(Request())
        else:
            if not os.path.exists(creds_path):
                log.error(f"Missing credentials.json at {creds_path}. Please download from Google Cloud Console.")
                raise FileNotFoundError(f"Missing credentials.json at {creds_path}")

            log.info("Initializing new Google OAuth flow...")
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            
            # Use local server if in a desktop env, otherwise use console flow if supported
            # For SOS, we'll try the local server but fallback to helpful error
            try:
                creds = flow.run_local_server(port=0)
            except Exception as e:
                log.error(f"Failed to start local server for OAuth: {e}")
                # Fallback: In a headless environment, user might need another way
                # For now, we expect local interaction for the first setup
                raise e

        # Save the credentials for the next run
        with open(token_path, 'w') as token:
            token.write(creds.to_json())

    return creds

def test_connection():
    """Simple test to verify we can list files."""
    try:
        creds = get_google_credentials()
        service = build('drive', 'v3', credentials=creds)

        # Call the Drive v3 API
        results = service.files().list(
            pageSize=10, fields="nextPageToken, files(id, name)").execute()
        items = results.get('files', [])

        if not items:
            print('No files found.')
        else:
            print('Files:')
            for item in items:
                print(f"{item['name']} ({item['id']})")
        return True
    except Exception as e:
        log.error(f"Google Drive connection test failed: {e}")
        return False

if __name__ == '__main__':
    print("Checking Google API Connection...")
    if test_connection():
        print("✅ Success!")
    else:
        print("❌ Failed.")
