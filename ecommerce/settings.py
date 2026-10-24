import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

_env_file = os.environ.get('ENV_FILE', str(BASE_DIR / '.env'))
load_dotenv(_env_file, override=True)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def env_list(name, default=None):
    value = os.environ.get(name)
    if not value:
        return default or []
    return [item.strip() for item in value.split(',') if item.strip()]


SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-ibvd9zhrkk$+2sqvv%4#l*&i4rjo)z8tt=f!&y7fasg-*#ps3b',
)
DEBUG = env_bool('DJANGO_DEBUG', True)
ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', ['localhost', '127.0.0.1'])
CSRF_TRUSTED_ORIGINS = env_list('DJANGO_CSRF_TRUSTED_ORIGINS', [])

if env_bool('ENABLE_NGROK', DEBUG):
    for host in ['.ngrok-free.app', '.ngrok.app']:
        if host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(host)
    for origin in ['https://*.ngrok-free.app', 'https://*.ngrok.app']:
        if origin not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(origin)

# Vercel domains
if os.environ.get('VERCEL'):
    ALLOWED_HOSTS.append('.vercel.app')
    CSRF_TRUSTED_ORIGINS.append('https://*.vercel.app')


INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    'shop',
    'rest_framework',
    'drf_yasg',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'cloudinary',
    'cloudinary_storage',
]

MIDDLEWARE = [
    'shop.middleware.AdminAPITokenMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
]

ADMIN_API_SECRET = os.environ.get('ADMIN_API_SECRET', '')
ADMIN_API_HEADER = 'HTTP_X_ADMIN_API_TOKEN'

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = env_bool('SESSION_COOKIE_SECURE', not DEBUG)
SESSION_COOKIE_AGE = int(os.environ.get('SESSION_COOKIE_AGE', '3600'))
CSRF_COOKIE_SECURE = env_bool('CSRF_COOKIE_SECURE', not DEBUG)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

ROOT_URLCONF = 'ecommerce.urls'

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
                'shop.context_processors.footer_context',
                'shop.context_processors.admin_order_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'ecommerce.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': os.environ.get('DB_ENGINE', 'django.db.backends.mysql'),
        'NAME': os.environ.get('DB_NAME', 'ecommerce_db'),
        'USER': os.environ.get('DB_USER', 'root'),
        'PASSWORD': os.environ.get('DB_PASSWORD', '1110joel'),
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '3306'),
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Cloudinary — used on Vercel (no persistent filesystem)
_cloudinary_url = os.environ.get('CLOUDINARY_URL')
if _cloudinary_url:
    DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'
    CLOUDINARY_STORAGE = {'CLOUDINARY_URL': _cloudinary_url}

REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.MultiPartParser',
        'rest_framework.parsers.FormParser',
        'rest_framework.parsers.JSONParser',
    ],
}
# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'Asia/Kolkata'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'

STATICFILES_DIRS = [
    BASE_DIR / 'static',
]

STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# --- Django-Allauth Configuration ---
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

SITE_ID = 1

SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'APP': {
            'client_id': os.environ.get('GOOGLE_CLIENT_ID', ''),
            'secret': os.environ.get('GOOGLE_CLIENT_SECRET', ''),
            'key': ''
        },
        'SCOPE': [
            'profile',
            'email',
        ],
        'AUTH_PARAMS': {
            'access_type': 'online',
        },
        'OAUTH_PKCE_ENABLED': True,
    }
}
SOCIALACCOUNT_LOGIN_ON_GET = True

ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*']
ACCOUNT_SESSION_REMEMBER = True
ACCOUNT_EMAIL_VERIFICATION = 'none'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

# --- Email Configuration ---
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
# Fallback email backend to console for development if settings aren't in env
if not EMAIL_HOST_USER or not EMAIL_HOST_PASSWORD:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# --- PhonePe Payment Gateway ---
PHONEPE_ENV = os.environ.get('PHONEPE_ENV', 'sandbox').strip().lower()
PHONEPE_CLIENT_ID = os.environ.get('PHONEPE_CLIENT_ID', '')
PHONEPE_CLIENT_SECRET = os.environ.get('PHONEPE_CLIENT_SECRET', '')
PHONEPE_CLIENT_VERSION = os.environ.get('PHONEPE_CLIENT_VERSION', '1')
PHONEPE_WEBHOOK_USERNAME = os.environ.get('PHONEPE_WEBHOOK_USERNAME', '')
PHONEPE_WEBHOOK_PASSWORD = os.environ.get('PHONEPE_WEBHOOK_PASSWORD', '')
PHONEPE_REQUEST_TIMEOUT = int(os.environ.get('PHONEPE_REQUEST_TIMEOUT', '20'))
WHATSAPP_INVOICE_LINK_MAX_AGE_DAYS = int(os.environ.get('WHATSAPP_INVOICE_LINK_MAX_AGE_DAYS', '30'))

if PHONEPE_ENV == 'production':
    PHONEPE_AUTH_URL = os.environ.get(
        'PHONEPE_AUTH_URL',
        'https://api.phonepe.com/apis/identity-manager/v1/oauth/token',
    )
    PHONEPE_BASE_URL = os.environ.get(
        'PHONEPE_BASE_URL',
        'https://api.phonepe.com/apis/pg',
    )
else:
    PHONEPE_AUTH_URL = os.environ.get(
        'PHONEPE_AUTH_URL',
        'https://api-preprod.phonepe.com/apis/pg-sandbox/v1/oauth/token',
    )
    PHONEPE_BASE_URL = os.environ.get(
        'PHONEPE_BASE_URL',
        'https://api-preprod.phonepe.com/apis/pg-sandbox',
    )

