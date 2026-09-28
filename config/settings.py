"""
Django settings for the PUNKT project.

See ТЗ PUNKT (Техническое задание), sections 2 and 5.4, for the
requirements this file implements: single-domain HTTPS, DEBUG=False in
production, cookie flags, and configuration exclusively via environment
variables (.env), never committed secrets.
"""

from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / '.env')


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def env_list(name, default=''):
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(',') if item.strip()]


SECRET_KEY = env('SECRET_KEY', 'django-insecure-dev-only-change-me')

DEBUG = env_bool('DEBUG', False)

ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', 'localhost,127.0.0.1')

CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS', '')

BASE_URL = env('BASE_URL', 'http://localhost:8000')


INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'accounts',
    'issues',
    'bot',
    'reports',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


def _database_from_url(url):
    parsed = urlparse(url)
    return {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': parsed.path.lstrip('/'),
        'USER': parsed.username or '',
        'PASSWORD': parsed.password or '',
        'HOST': parsed.hostname or '',
        'PORT': parsed.port or '',
    }


DATABASE_URL = env('DATABASE_URL')
if DATABASE_URL:
    DATABASES = {'default': _database_from_url(DATABASE_URL)}
else:
    # Local development fallback only; production always sets DATABASE_URL.
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_USER_MODEL = 'accounts.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Domain users authenticate only through the Telegram magic link (see
# accounts.views); Django's password login stays available solely for the
# technical Administrator role in /admin (ТЗ §1.1).
LOGIN_URL = '/login'

LANGUAGE_CODE = 'ru'

# Event timestamps are stored in UTC (USE_TZ=True); TIME_ZONE controls the
# local time used for display and due-date comparisons: Asia/Novosibirsk
# per ТЗ §3.
TIME_ZONE = env('TIME_ZONE', 'Asia/Novosibirsk')

USE_I18N = True

USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Хешированные имена статики после деплоя (collectstatic) исключают
# показ старого JS/CSS из кэша браузера. В DEBUG используется обычное
# хранилище, чтобы не требовать collectstatic при локальной разработке.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {
        'BACKEND': (
            'django.contrib.staticfiles.storage.StaticFilesStorage' if DEBUG
            else 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage'
        ),
    },
}

# Media is never served directly by the web server: original photos and
# previews are only reachable through issues.views.photo, which enforces
# the same authorization rules as the issue itself (ТЗ §4, GET /photos/{uuid}).
MEDIA_ROOT = Path(env('MEDIA_ROOT', BASE_DIR / 'media'))

BACKUP_PATH = Path(env('BACKUP_PATH', BASE_DIR / 'backups'))

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- Session & CSRF cookies (ТЗ §5.4) ---------------------------------
SESSION_COOKIE_AGE = 7 * 24 * 60 * 60  # 7 days
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_ENGINE = 'django.contrib.sessions.backends.db'

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# --- Uploads (ТЗ §5.3: photos up to 10 MiB) ---------------------------
PHOTO_MAX_BYTES = 10 * 1024 * 1024
PHOTO_MAX_PIXELS = 25_000_000
DATA_UPLOAD_MAX_MEMORY_SIZE = 15 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 15 * 1024 * 1024

# --- Telegram bot (ТЗ §2, §5.2) ----------------------------------------
BOT_TOKEN = env('BOT_TOKEN', '')
TELEGRAM_API_BASE = env('TELEGRAM_API_BASE', 'https://api.telegram.org')
TELEGRAM_CONNECT_TIMEOUT = 5
TELEGRAM_READ_TIMEOUT = 30
TELEGRAM_POLL_TIMEOUT = 35

# next_path whitelist for the magic-link login (ТЗ §5.4): only relative
# in-app paths, never an open redirect target.
LOGIN_ALLOWED_NEXT_PREFIXES = ('/issues',)

# Domain-level limits (ТЗ §5.3)
MAX_ISSUES = 1000
MAX_USERS_PER_SITE = 20
MAX_AFTER_PHOTOS_PER_ISSUE = 10
MAX_REPORT_ROWS = 100
ISSUES_PAGE_SIZE = 25

# Rate limits (ТЗ §5.4)
MAX_LOGIN_TOKENS_PER_MINUTE = 5
MAX_CHANGES_PER_MINUTE = 30

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {'class': 'logging.StreamHandler'},
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
}
