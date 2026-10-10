import os


def _b(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


class Settings:
    # Any SQLAlchemy URL. Docker: postgres. Cloud: Cloud SQL (Auth Proxy / unix socket).
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./retail.db")
    MODEL_DIR = os.getenv("MODEL_DIR", "./models")
    # Dataset (hackathon CSVs). Not redistributed with this project: mount it or let the app download it.
    DATA_DIR = os.getenv("DATA_DIR", "./data")
    AUTO_LOAD = _b("AUTO_LOAD", "true")            # load CSVs into an empty DB at start-up
    AUTO_DOWNLOAD = _b("AUTO_DOWNLOAD", "true")    # fetch missing CSVs from the source repository
    DATASET_RAW_URL = os.getenv(
        "DATASET_RAW_URL",
        "https://raw.githubusercontent.com/ams-innovation/dataset-hacktahton-road2tech/master")
    FRONTEND_DIR = os.getenv("FRONTEND_DIR", "")   # serve the SPA from FastAPI (single-container mode)
    CURRENCY = os.getenv("CURRENCY", "USD")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    EXPORT_API_KEY = os.getenv("EXPORT_API_KEY", "")
    POWERBI_EMBED_URL = os.getenv("POWERBI_EMBED_URL", "")
    # Business assumptions (the dataset has no wage data -> configurable)
    WAGE_PER_HOUR = float(os.getenv("WAGE_PER_HOUR", "17.5"))
    SERVICE_Z = float(os.getenv("SERVICE_Z", "1.28"))            # ~90 % cycle service level
    MIN_STAFF_RATIO = float(os.getenv("MIN_STAFF_RATIO", "1.5"))  # staff-hours per open hour (observed min ~1.7)
    SPLH_PERCENTILE = float(os.getenv("SPLH_PERCENTILE", "0.5"))  # target sales per labour hour = store median
    STAFF_TOLERANCE = float(os.getenv("STAFF_TOLERANCE", "0.10"))
    UNDERSTAFF_LEAKAGE = float(os.getenv("UNDERSTAFF_LEAKAGE", "0.15"))  # share of demand lost per short staff-hour (assumption)
    CARRY_COST_PER_DAY = float(os.getenv("CARRY_COST_PER_DAY", "0.003"))
    DEMO_STORES = int(os.getenv("DEMO_STORES", "0"))              # >0: load only first N stores (fast tests)


settings = Settings()
