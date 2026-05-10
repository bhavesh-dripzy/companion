import os
import json
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _aws_json_secret(secret_id: str) -> dict[str, str]:
    if not secret_id:
        return {}
    try:
        import boto3
    except ImportError:
        return {}
    client = boto3.client("secretsmanager")
    value = client.get_secret_value(SecretId=secret_id)
    raw = value.get("SecretString") or "{}"
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


_app_secret = _aws_json_secret(os.getenv("APP_SECRET_ID", "").strip())
_db_secret = _aws_json_secret(os.getenv("DB_SECRET_ID", "").strip())

SECRET_KEY = os.getenv(
    "SECRET_KEY", _app_secret.get("SECRET_KEY", "django-insecure-change-me")
)
DEBUG = os.getenv("DEBUG", "False").strip().lower() in ("1", "true", "yes")
_allowed = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").strip()
ALLOWED_HOSTS: list[str] = [
    h.strip() for h in _allowed.split(",") if h.strip()
]
CSRF_TRUSTED_ORIGINS = [
    h.strip()
    for h in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",")
    if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

if os.getenv("USE_SQLITE", "").strip().lower() in ("1", "true", "yes"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.getenv("SQLITE_NAME", BASE_DIR / "db.sqlite3"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.getenv("DB_NAME", _db_secret.get("dbname", "companion")),
            "USER": os.getenv("DB_USER", _db_secret.get("username", "root")),
            "PASSWORD": os.getenv("DB_PASSWORD", _db_secret.get("password", "")),
            "HOST": os.getenv("DB_HOST", _db_secret.get("host", "127.0.0.1")),
            "PORT": os.getenv("DB_PORT", str(_db_secret.get("port", "3306"))),
            "OPTIONS": {"charset": "utf8mb4"},
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = os.getenv("USE_X_FORWARDED_HOST", "True").strip().lower() in (
    "1",
    "true",
    "yes",
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
