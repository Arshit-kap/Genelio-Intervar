"""
Django settings for the Genelio backend.

Mirrors the public API surface observed at https://ai-api.genelio.com:
  /auth/...          -> accounts app (djoser-compatible URL shape, JWT auth)
  /chatbot/chat/...  -> chatbot app (chat sessions + messages)
  /chatbot/report/...-> reports app (uploaded reports per type)
"""
from datetime import timedelta
from pathlib import Path

import dj_database_url
from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config("DJANGO_SECRET_KEY", default="dev-secret-change-me")
DEBUG = config("DJANGO_DEBUG", default=True, cast=bool)
ALLOWED_HOSTS = config("DJANGO_ALLOWED_HOSTS", default="*", cast=Csv())

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # 3rd party
    "rest_framework",
    "rest_framework_simplejwt",
    "corsheaders",
    "django_filters",
    "drf_spectacular",

    # local
    "accounts",
    "reports",
    "chatbot",
    "franklin",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "genelio.urls"

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

WSGI_APPLICATION = "genelio.wsgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# Postgres by default. The Postgres container is brought up by
# `docker compose up -d db`; Django itself runs on the host, so the default
# connection string targets localhost:5432.
DATABASES = {
    "default": dj_database_url.config(
        default=config(
            "DATABASE_URL",
            default="postgres://genelio:genelio@localhost:5432/genelio",
        ),
        conn_max_age=600,
    )
}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# DRF + JWT
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
        "rest_framework.filters.SearchFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 6,  # matches what genelio.com currently renders (6 cards / page)
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    "AUTH_HEADER_TYPES": ("Bearer", "JWT"),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Genelio API",
    "DESCRIPTION": "Backend for the Genelio AI health-report assistant.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# ---------------------------------------------------------------------------
# CORS / CSRF
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = config(
    "CORS_ALLOWED_ORIGINS",
    default="http://localhost:3000,http://localhost:5173",
    cast=Csv(),
)
CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS",
    default="http://localhost:3000,http://localhost:5173",
    cast=Csv(),
)
CORS_ALLOW_CREDENTIALS = True

# ---------------------------------------------------------------------------
# Internationalization & static
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Upload limits — InterVar TXTs can be 30-40 MB at whole-genome scale.
# ---------------------------------------------------------------------------
DATA_UPLOAD_MAX_MEMORY_SIZE = 60 * 1024 * 1024   # 60 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 60 * 1024 * 1024   # 60 MB

# ---------------------------------------------------------------------------
# Celery (Franklin report enrichment, future async jobs)
# ---------------------------------------------------------------------------
# Tasks live in `<app>/tasks.py` modules and are picked up by
# ``celery.app.autodiscover_tasks()`` from genelio/celery.py.
CELERY_BROKER_URL = config(
    "CELERY_BROKER_URL", default="redis://localhost:6379/1"
)
CELERY_RESULT_BACKEND = config("CELERY_RESULT_BACKEND", default="")
# In dev without a worker running, flip eager mode on so tasks execute
# inline (synchronous). Defaults to eager only when explicitly opted-in.
CELERY_TASK_ALWAYS_EAGER = config(
    "CELERY_TASK_ALWAYS_EAGER", default=False, cast=bool
)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_RESULT_SERIALIZER = "json"

# ---------------------------------------------------------------------------
# Cache (Franklin lookups, future RAG memoization, etc.)
# ---------------------------------------------------------------------------
# If REDIS_URL is set, use it; otherwise fall back to local memory so dev
# without a Redis container still works.
REDIS_URL = config("REDIS_URL", default="")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "genelio-locmem",
        }
    }

# ---------------------------------------------------------------------------
# Gut microbiome pipeline (used by chatbot when the attached report is gut)
# ---------------------------------------------------------------------------
VLLM_BASE_URL = config("VLLM_BASE_URL", default="http://localhost:8011/v1")
MODEL_NAME = config("MODEL_NAME", default="qwen3-30b")
# Nomic v1.5 — same embedder used by the original Gradio prototype.
# Requires `trust_remote_code=True` when loaded via sentence-transformers,
# and uses `search_document:` / `search_query:` task prefixes on inputs.
EMBED_MODEL = config("EMBED_MODEL", default="nomic-ai/nomic-embed-text-v1.5")
# Persistent store for per-report Chroma collections. Lives under MEDIA so
# it's co-located with uploaded files and wiped together in dev resets.
CHROMA_PERSIST_DIR = config(
    "CHROMA_PERSIST_DIR", default=str(BASE_DIR / "media" / "chroma"),
)
