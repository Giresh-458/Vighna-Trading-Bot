import os
import time
import pyotp
import logging
import threading
from typing import Optional, Dict
from SmartApi import SmartConnect
from app.config import ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP_SECRET

logger = logging.getLogger(__name__)

class AngelAuth:
    TOKEN_MAX_AGE = 72000  # 20 hours (tokens expire at ~24h, refresh early)

    def __init__(self):
        self.api_key = ANGEL_API_KEY
        self.client_id = ANGEL_CLIENT_ID
        self.password = ANGEL_PASSWORD
        self.totp_secret = ANGEL_TOTP_SECRET
        self.api: Optional[SmartConnect] = None
        self.feed_token: Optional[str] = None
        self.jwt_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self._login_time: float = 0.0
        self._auth_lock = threading.Lock()
        self.session_file = "angel_session.json"

    def login(self, force_refresh: bool = False) -> bool:
        """
        Authenticate with Angel One SmartAPI or reuse cached session.
        """
        if not all([self.api_key, self.client_id, self.password, self.totp_secret]):
            logger.error("Missing Angel One credentials in .env")
            return False

        # 1. Try loading cached session if not forced and still fresh (<16h)
        if not force_refresh and os.path.exists(self.session_file):
            try:
                import json
                with open(self.session_file, "r") as f:
                    sdata = json.load(f)
                age = time.time() - sdata.get("timestamp", 0)
                if age < 57600 and sdata.get("jwtToken"):  # 16 hours
                    self.jwt_token = sdata["jwtToken"]
                    self.refresh_token = sdata.get("refreshToken")
                    self.feed_token = sdata.get("feedToken")
                    self._login_time = sdata.get("timestamp", time.time())

                    self.api = SmartConnect(api_key=self.api_key)
                    raw_jwt = self.jwt_token.replace("Bearer ", "").strip()
                    self.api.setAccessToken(raw_jwt)
                    self.api.setUserId(self.client_id)
                    logger.info("Reusing active Angel One session from cache.")
                    return True
            except Exception as e:
                logger.debug(f"Failed to load cached session: {e}")

        # 2. Perform fresh login via TOTP
        try:
            self.api = SmartConnect(api_key=self.api_key)
            totp = pyotp.TOTP(self.totp_secret).now()
            
            data = self.api.generateSession(self.client_id, self.password, totp)
            
            if data and data.get('status'):
                self.jwt_token = data['data']['jwtToken']
                self.refresh_token = data['data']['refreshToken']
                self.feed_token = self.api.getfeedToken()
                self._login_time = time.time()

                # Persist to session file for sharing across bot and dashboard
                try:
                    import json
                    with open(self.session_file, "w") as f:
                        json.dump({
                            "jwtToken": self.jwt_token,
                            "refreshToken": self.refresh_token,
                            "feedToken": self.feed_token,
                            "timestamp": self._login_time
                        }, f)
                except Exception as e:
                    logger.warning(f"Could not save session cache: {e}")

                logger.info("Angel One authentication successful.")
                return True
            else:
                logger.error(f"Angel One auth failed: {data.get('message')}")
                return False
                
        except Exception as e:
            logger.exception("Exception during Angel One login")
            return False

    def refresh_if_needed(self) -> bool:
        """Re-login if the JWT token is older than TOKEN_MAX_AGE seconds."""
        with self._auth_lock:
            if self._login_time and (time.time() - self._login_time) > self.TOKEN_MAX_AGE:
                logger.info("Angel One JWT token is stale. Re-authenticating...")
                return self.login()
            return True

    def get_tokens(self) -> Dict[str, str]:
        return {
            "jwtToken": self.jwt_token,
            "refreshToken": self.refresh_token,
            "feedToken": self.feed_token,
            "clientId": self.client_id,
            "apiKey": self.api_key
        }
