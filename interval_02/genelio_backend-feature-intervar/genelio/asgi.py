"""ASGI config for the genelio project."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "genelio.settings")
application = get_asgi_application()
