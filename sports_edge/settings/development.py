import os
from .base import *  # noqa: F401, F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

if not os.environ.get("DATABASE_URL"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
        }
    }

INSTALLED_APPS += ["django.contrib.admindocs"]  # noqa: F405

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
