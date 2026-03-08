from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

#SECRET_KEY = 'django-insecure-w*fbnkw+lhu^ixu@h=p_3n0*pqu8s426gfrqfc$ut^hrz5=d0x'
#DEBUG = True

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-dev-key")
DEBUG = os.getenv("DJANGO_DEBUG", "True") == "True"
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
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Add the account middleware:
    "allauth.account.middleware.AccountMiddleware",
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
                'django.template.context_processors.request',
                "django.template.context_processors.i18n",
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
TIME_ZONE = "Europe/Brussels"
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []