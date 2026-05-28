"""Report model — a single uploaded file tied to one report type.

The six types were observed in the upload picker at /report on genelio.com.
Only `gut` currently has real parsing logic; the rest are structurally
first-class but queue to a stub analyzer that just marks them `processed`.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class ReportType(models.TextChoices):
    WGS = "wgs", "WGS"
    WES = "wes", "WES"
    ORAL = "oral", "Oral Microbiome"
    GUT = "gut", "Gut Microbiome"
    SKIN = "skin", "Skin Microbiome"
    VAGINAL = "vaginal", "Vaginal Microbiome"
    # Annotated clinical-variant CSV (BioAro / ANNOVAR-style export — 72
    # columns covering variant calls + ClinVar + ACMG evidence + ML
    # predictors + patient genotype + clinical flags).
    CLINICAL_CSV = "clinical_csv", "Clinical Variant CSV"
    # Raw InterVar annotation TXT (tab-separated, 34 columns; ACMG
    # evidence packed into one InterVar string, includes OMIM / Orpha
    # cross-references). Whole-genome scale (10k-100k+ rows per patient).
    INTERVAR = "intervar", "InterVar TXT"


class ReportStatus(models.TextChoices):
    UPLOADED = "uploaded", "Uploaded"
    PROCESSING = "processing", "Processing"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class EnrichmentStatus(models.TextChoices):
    NONE = "none", "None"                 # Not applicable (e.g. microbiome)
    PENDING = "pending", "Pending"        # Task enqueued, not started
    RUNNING = "running", "Running"        # Task currently fanning out
    READY = "ready", "Ready"              # Enrichment data is current
    FAILED = "failed", "Failed"           # Task raised; partial data may exist


def report_upload_path(instance: "Report", filename: str) -> str:
    return f"reports/{instance.owner_id}/{instance.id}/{filename}"


class Report(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="reports", on_delete=models.CASCADE
    )
    report_type = models.CharField(max_length=16, choices=ReportType.choices)
    file = models.FileField(upload_to=report_upload_path)
    original_filename = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=16, choices=ReportStatus.choices, default=ReportStatus.UPLOADED,
    )
    error_message = models.TextField(blank=True, default="")

    # Structured JSON output of the analyzer (for gut, this is the parsed
    # report; for others it's a stub until an analyzer is wired up).
    parsed_data = models.JSONField(blank=True, null=True)

    # Per-variant Franklin (Genoox) enrichment. Populated asynchronously
    # for wes/wgs reports by `franklin.tasks.enrich_report`. Microbiome
    # reports stay at status="none" — Franklin doesn't apply to them.
    enrichment_status = models.CharField(
        max_length=16,
        choices=EnrichmentStatus.choices,
        default=EnrichmentStatus.NONE,
    )
    enrichment_data = models.JSONField(blank=True, null=True)
    enrichment_error = models.TextField(blank=True, default="")
    enrichment_updated_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["owner", "-created_at"]),
            models.Index(fields=["report_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.report_type}:{self.id}"
