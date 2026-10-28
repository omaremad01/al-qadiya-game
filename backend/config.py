import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
MODEL_PRIMARY = os.getenv("MODEL_PRIMARY", "llama-3.3-70b-versatile").strip()
MODEL_FALLBACK = os.getenv("MODEL_FALLBACK", "llama-3.1-8b-instant").strip()
MODELS = [m for m in (MODEL_PRIMARY, MODEL_FALLBACK) if m]

CASE_SECRET = os.getenv("CASE_SECRET", "dev-secret-change-me")
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

RATE_CHAT_PER_MIN = int(os.getenv("RATE_LIMIT_CHAT_PER_MIN", "25"))
RATE_NEW_PER_HOUR = int(os.getenv("RATE_LIMIT_NEW_CASES_PER_HOUR", "6"))
