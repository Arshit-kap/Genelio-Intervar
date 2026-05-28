"""WSGI config for the genelio project."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "genelio.settings")
application = get_wsgi_application()
