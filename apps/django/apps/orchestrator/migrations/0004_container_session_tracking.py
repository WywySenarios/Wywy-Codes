from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orchestrator", "0003_pipeline_error_message"),
    ]

    operations = [
        migrations.AddField(
            model_name="pipeline",
            name="container_id",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="pipelinestage",
            name="session_id",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="pipelinestage",
            name="forked_from_session_id",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]
