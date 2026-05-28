"""URLs for the franklin app (mounted under /franklin/)."""
from django.urls import path

from .views import gene_lookup, search, variant_lookup

urlpatterns = [
    path("search/", search, name="franklin-search"),
    path("gene/", gene_lookup, name="franklin-gene"),
    path("variant/", variant_lookup, name="franklin-variant"),
]
