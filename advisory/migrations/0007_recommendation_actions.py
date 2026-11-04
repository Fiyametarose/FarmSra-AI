import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("advisory", "0006_global_chat_sessions"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="RecommendationAction",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("section_key", models.CharField(max_length=40)),
                ("recommendation", models.TextField()),
                ("status", models.CharField(choices=[("not_started", "Not Started"), ("in_progress", "In Progress"), ("completed", "Completed"), ("skipped", "Skipped")], default="not_started", max_length=12)),
                ("farmer_notes", models.TextField(blank=True)),
                ("status_changed_at", models.DateTimeField(auto_now_add=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("advisory_version", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="tracked_actions", to="advisory.advisoryversion")),
                ("farmer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="recommendation_actions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["created_at"]},
        ),
        migrations.AddConstraint(
            model_name="recommendationaction",
            constraint=models.UniqueConstraint(fields=("advisory_version", "farmer", "section_key"), name="advisory_unique_tracked_section"),
        ),
    ]
