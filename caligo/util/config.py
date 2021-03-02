import json
import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class BotConfig:
    """
    Bot configuration
    """

    def __init__(self, log: logging.Logger) -> "BotConfig":
        if os.path.isfile("config.env"):
            load_dotenv("config.env")

        # Core config
        self.api_id = int(os.environ.get("API_ID", 0))
        self.api_hash = os.environ.get("API_HASH")
        self.db_uri = os.environ.get("DB_URI")
        self.string_session = os.environ.get("STRING_SESSION")

        # Core needed
        self.log = int(os.environ.get("LOG_GROUP", 0))

        # GoogleDrive
        try:
            self.gdrive_data = json.loads(os.environ.get("G_DRIVE_DATA"))
        except TypeError:
            log.warning("Google Drive client secret is empty.")
        except json.decoder.JSONDecodeError:
            self.gdrive_data = None
            log.warning("Google Drive client secret is invalid.")
        self.gdrive_folder_id = os.environ.get("G_DRIVE_FOLDER_ID")
