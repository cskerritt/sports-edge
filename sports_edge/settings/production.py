from .base import *  # noqa: F401, F403
import environ

env = environ.Env()

DEBUG = False

# In production, DATABASE_URL must be set (no SQLite fallback)
DATABASES = {
    "default": env.db("DATABASE_URL")
}

CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=["https://*.up.railway.app"],
)

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_SSL_REDIRECT = False  # Railway handles SSL at the proxy level
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
