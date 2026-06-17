from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orchestrator", "0002_pipelinestage_retry_after"),
    ]

    operations = [
        migrations.AddField(
            model_name="pipeline",
            name="error_message",
            field=models.TextField(blank=True, null=True),
        ),
    ]
