"""Remove the ``user_input_request`` and ``user_input_response`` fields.

These fields were used by the old file-based blocked-flow (pre-Cycle 7).
The new session-based flow communicates via ``AgentClient.send_message()``
to the opencode server, so the DB-stored request/response is no longer
needed.  ``user_input_pending`` is retained.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("orchestrator", "0004_container_session_tracking"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="pipeline",
            name="user_input_request",
        ),
        migrations.RemoveField(
            model_name="pipeline",
            name="user_input_response",
        ),
    ]
