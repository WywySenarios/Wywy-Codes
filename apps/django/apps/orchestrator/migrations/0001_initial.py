import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Pipeline",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("invocation_name", models.CharField(max_length=100)),
                ("status", models.CharField(default="queued", max_length=20)),
                (
                    "current_stage",
                    models.CharField(blank=True, max_length=50, null=True),
                ),
                ("iteration_count", models.IntegerField(default=0)),
                ("user_input_pending", models.BooleanField(default=False)),
                (
                    "user_input_request",
                    models.JSONField(blank=True, null=True),
                ),
                (
                    "user_input_response",
                    models.TextField(blank=True, null=True),
                ),
                ("pr_url", models.URLField(blank=True, null=True)),
                ("description", models.TextField(default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Pipeline",
                "verbose_name_plural": "Pipelines",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="PipelineStage",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=50)),
                ("status", models.CharField(default="pending", max_length=20)),
                ("output", models.JSONField(blank=True, null=True)),
                ("retry_count", models.IntegerField(default=0)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "pipeline",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stages",
                        to="orchestrator.pipeline",
                    ),
                ),
            ],
            options={
                "verbose_name": "Pipeline Stage",
                "verbose_name_plural": "Pipeline Stages",
                "ordering": ["pipeline", "id"],
                "unique_together": {("pipeline", "name")},
            },
        ),
    ]
