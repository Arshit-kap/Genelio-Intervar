"""Franklin lookup endpoints.

  GET /franklin/search/?q=<query>&ref=<hg19|hg38>&mode=<auto|gene|variant>
      Single entrypoint. `mode=auto` (default) detects gene vs variant
      from the query shape.
  GET /franklin/gene/?symbol=<sym>&ref=<hg19|hg38>
  GET /franklin/variant/?q=<hgvs_or_coords>&ref=<hg19|hg38>
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import services
from .client import looks_like_variant


def _validate_reference(raw: str | None) -> tuple[str | None, Response | None]:
    ref = (raw or "hg19").upper()
    if ref not in ("HG19", "HG38"):
        return None, Response(
            {"detail": "reference must be hg19 or hg38"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return ref, None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def search(request):
    query = (request.query_params.get("q") or "").strip()
    if not query:
        return Response(
            {"detail": "q query parameter is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    reference, err = _validate_reference(request.query_params.get("ref"))
    if err:
        return err

    mode = (request.query_params.get("mode") or "auto").lower()
    if mode not in ("auto", "gene", "variant"):
        return Response(
            {"detail": "mode must be auto, gene, or variant"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if mode == "auto":
        mode = "variant" if looks_like_variant(query) else "gene"

    if mode == "variant":
        result = services.lookup_variant(query, reference)
    else:
        result = services.lookup_gene(query, reference)

    return Response(result)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def gene_lookup(request):
    symbol = (request.query_params.get("symbol") or "").strip()
    if not symbol:
        return Response(
            {"detail": "symbol query parameter is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    reference, err = _validate_reference(request.query_params.get("ref"))
    if err:
        return err
    return Response(services.lookup_gene(symbol, reference))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def variant_lookup(request):
    query = (request.query_params.get("q") or "").strip()
    if not query:
        return Response(
            {"detail": "q query parameter is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    reference, err = _validate_reference(request.query_params.get("ref"))
    if err:
        return err
    return Response(services.lookup_variant(query, reference))
