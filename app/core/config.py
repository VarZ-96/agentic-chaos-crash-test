import os
import yaml
from typing import Optional
from pydantic_settings import BaseSettings
from app.schemas.chaos_config import ChaosConfig

class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    CHAOS_CONFIG_PATH: str = "chaos.yaml"
    
    chaos_config: Optional[ChaosConfig] = None
    
    class Config:
        env_file = ".env"
        
    def load_chaos_config(self) -> None:
        if os.path.exists(self.CHAOS_CONFIG_PATH):
            with open(self.CHAOS_CONFIG_PATH, "r") as f:
                raw_config = yaml.safe_load(f)
                if raw_config:
                    self.chaos_config = ChaosConfig(**raw_config)

settings = Settings()
settings.load_chaos_config()
