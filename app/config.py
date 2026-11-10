import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')

@dataclass
class Settings:
    assembly_key: str = os.getenv('ASSEMBLYAI_API_KEY', '')
    groq_key: str = os.getenv('GROQ_API_KEY', '')
    model: str = os.getenv('GROQ_MODEL', 'openai/gpt-oss-20b')
    max_minutes: float = float(os.getenv('MAX_SESSION_MINUTES', '120'))
    hourly_rate: float = float(os.getenv('ASSEMBLYAI_HOURLY_RATE', '0.15'))
    grace_seconds: float = float(os.getenv('BROWSER_GRACE_SECONDS', '15'))
