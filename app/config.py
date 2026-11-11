import os
from pathlib import Path
from dataclasses import dataclass
import certifi
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')

# The streaming SDK uses Python's TLS defaults. Some macOS Python installs
# lack a default trust bundle, so use certifi unless one is configured.
os.environ.setdefault('SSL_CERT_FILE', certifi.where())

@dataclass
class Settings:
    assembly_key: str = os.getenv('ASSEMBLYAI_API_KEY', '')
    groq_key: str = os.getenv('GROQ_API_KEY', '')
    model: str = os.getenv('GROQ_MODEL', 'openai/gpt-oss-20b')
    max_minutes: float = float(os.getenv('MAX_SESSION_MINUTES', '120'))
    hourly_rate: float = float(os.getenv('ASSEMBLYAI_HOURLY_RATE', '0.15'))
    grace_seconds: float = float(os.getenv('BROWSER_GRACE_SECONDS', '15'))
