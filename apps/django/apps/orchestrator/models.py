"""Django models for the orchestrator pipeline management."""

import uuid

from django.db import models


class Pipeline(models.Model):
    """Tracks a single agentic pipeline execution."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    invocation_name = models.CharField(max_length=100)
    status = models.CharField(max_length=20, default="queued")
    current_stage = models.CharField(max_length=50, null=True, blank=True)
    iteration_count = models.IntegerField(default=0)
    user_input_pending = models.BooleanField(default=False)
    error_message = models.TextField(null=True, blank=True)
    user_input_request = models.JSONField(null=True, blank=True)
    user_input_response = models.TextField(null=True, blank=True)
    pr_url = models.URLField(null=True, blank=True)
    description = models.TextField(default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Pipeline"
        verbose_name_plural = "Pipelines"

    def __str__(self) -> str:
        return f"{self.invocation_name} ({self.status})"


class PipelineStage(models.Model):
    """Tracks a single stage within a pipeline."""

    pipeline = models.ForeignKey(
        Pipeline, on_delete=models.CASCADE, related_name="stages"
    )
    name = models.CharField(max_length=50)
    status = models.CharField(max_length=20, default="pending")
    output = models.JSONField(null=True, blank=True)
    retry_count = models.IntegerField(default=0)
    retry_after = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["pipeline", "id"]
        verbose_name = "Pipeline Stage"
        verbose_name_plural = "Pipeline Stages"
        constraints = [
            models.UniqueConstraint(
                fields=["pipeline", "name"], name="unique_pipeline_stage"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.pipeline.invocation_name}/{self.name} ({self.status})"
