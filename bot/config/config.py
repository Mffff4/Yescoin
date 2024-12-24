from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True)

    API_ID: int = None
    API_HASH: str = None
    GLOBAL_CONFIG_PATH: str = "TG_FARM"

    FIX_CERT: bool = False
    SESSION_START_DELAY: int = 360
    REF_ID: str = 'dIk9eL'
    
    SESSIONS_PER_PROXY: int = 1
    USE_PROXY: bool = True
    DISABLE_PROXY_REPLACE: bool = False
    DEVICE_PARAMS: bool = False
    DEBUG_LOGGING: bool = False

    AUTO_UPDATE: bool = True
    CHECK_UPDATE_INTERVAL: int = 300
    BLACKLISTED_SESSIONS: str = ""

    SLEEP_BETWEEN_TAP: List[int] = [3, 8]
    SLEEP_BY_MIN_ENERGY: List[int] = [1800, 10800]
    
    RANDOM_TAPS_COUNT: List[int] = [35, 100]
    MIN_AVAILABLE_ENERGY: int = 10

    MAX_TAP_LEVEL: int = 10
    MAX_ENERGY_LEVEL: int = 10
    MAX_CHARGE_LEVEL: int = 10
    MAX_YESPAC_LEVEL: int = 2

    AUTO_BIND_WALLET: bool = False

    @property
    def blacklisted_sessions(self) -> List[str]:
        return [s.strip() for s in self.BLACKLISTED_SESSIONS.split(',') if s.strip()]

settings = Settings()
