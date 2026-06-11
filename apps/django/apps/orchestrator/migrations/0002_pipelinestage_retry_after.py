from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orchestrator", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="pipelinestage",
            name="retry_after",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
