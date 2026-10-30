import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

DEBUG = env_bool("DJANGO_DEBUG", default=False)
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-dev-key"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is disabled.")
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")

LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]
ACCOUNT_EMAIL_VERIFICATION = "none"

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'allauth',
    'allauth.account',
    'customuser.apps.CustomuserConfig',
    'jiramodule.apps.JiramoduleConfig',
    'sprint.apps.SprintConfig',
    'issue.apps.IssueConfig',
    'dev.apps.DevConfig',
    'team.apps.TeamConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'JiraFox.middleware.UserLanguageMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Add the account middleware:
    "allauth.account.middleware.AccountMiddleware",
    "jiramodule.middleware.JiraErrorMiddleware",
]

ROOT_URLCONF = 'JiraFox.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / "templates"],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                "django.template.context_processors.debug",
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                "django.template.context_processors.i18n",
                "team.context_processors.projects",
            ],
            'libraries': {
                'datetime_filters': 'templatetags.datetime_filters',
                'common_tags': 'templatetags.common_tags',
            },
        },
    },
]

WSGI_APPLICATION = 'JiraFox.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_USER_MODEL = "customuser.CustomUser"

AUTH_PASSWORD_VALIDATORS = [
    {   'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',},
    {   'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',},
    {   'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',},
    {   'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',},
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = "fr"
LANGUAGES = [
    ("fr", "Français"),
    ("nl", "Nederlands"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Europe/Brussels"
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []
STATIC_ROOT = BASE_DIR / "staticfiles"

# Email: console in development, SMTP otherwise. EMAIL_BACKEND takes a shortcut below or a
# dotted backend path (e.g. a third-party provider's backend).
EMAIL_BACKENDS = {
    "smtp": "django.core.mail.backends.smtp.EmailBackend",
    "console": "django.core.mail.backends.console.EmailBackend",
    "file": "django.core.mail.backends.filebased.EmailBackend",
    "locmem": "django.core.mail.backends.locmem.EmailBackend",
    "dummy": "django.core.mail.backends.dummy.EmailBackend",
}
email_backend = (os.getenv("EMAIL_BACKEND") or ("console" if DEBUG else "smtp")).strip()
EMAIL_BACKEND = EMAIL_BACKENDS.get(email_backend.lower(), email_backend)

EMAIL_HOST = os.getenv("EMAIL_HOST") or "localhost"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", default=False)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", default=False)
if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured("EMAIL_USE_TLS and EMAIL_USE_SSL are mutually exclusive.")
# 465 for implicit SSL, 587 for STARTTLS, plain SMTP on 25.
EMAIL_PORT = int(os.getenv("EMAIL_PORT") or (465 if EMAIL_USE_SSL else 587 if EMAIL_USE_TLS else 25))
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT") or 10)
EMAIL_SSL_CERTFILE = os.getenv("EMAIL_SSL_CERTFILE") or None
EMAIL_SSL_KEYFILE = os.getenv("EMAIL_SSL_KEYFILE") or None
EMAIL_FILE_PATH = os.getenv("EMAIL_FILE_PATH") or BASE_DIR / "sent_emails"

DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL") or "JiraFox <noreply@localhost>"
SERVER_EMAIL = os.getenv("SERVER_EMAIL") or DEFAULT_FROM_EMAIL
EMAIL_SUBJECT_PREFIX = os.getenv("EMAIL_SUBJECT_PREFIX", "[JiraFox] ")
# Comma-separated addresses that receive the server errors (500) when DEBUG is off.
ADMINS = [address.strip() for address in os.getenv("DJANGO_ADMINS", "").split(",") if address.strip()]
MANAGERS = ADMINS

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.getenv("DJANGO_LOG_LEVEL", "INFO"),
    },
}
