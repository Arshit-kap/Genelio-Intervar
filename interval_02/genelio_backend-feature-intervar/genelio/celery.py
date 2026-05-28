"""Celery application factory.

Bootstrapped via ``genelio/__init__.py``; the worker is started with::

    celery -A genelio worker --loglevel=info

Broker URL is taken from ``CELERY_BROKER_URL`` (see settings.py) and
falls back to the local Redis container brought up by docker-compose.
"""
from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "genelio.settings")

app = Celery("genelio")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
