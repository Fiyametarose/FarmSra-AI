import django.db.models.deletion
import diagnoses.models
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("diagnoses", "0004_diagnosiscase_completed_at_and_more")]
    operations = [
        migrations.AddField(
            model_name="diagnosiscase", name="parent_case",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="reassessments", to="diagnoses.diagnosiscase",
            ),
        ),
        migrations.CreateModel(
            name="CaseFollowUp",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("symptoms", models.TextField(blank=True)),
                ("image", models.ImageField(blank=True, null=True, upload_to=diagnoses.models.follow_up_image_path)),
                ("observed_at", models.DateTimeField()),
                ("notes", models.TextField(blank=True)),
                ("actions_taken", models.TextField(blank=True)),
                ("request_token", models.UUIDField(unique=True)),
                ("reassessment_requested_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("diagnosis", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="follow_ups", to="diagnoses.diagnosiscase")),
                ("reassessment", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="source_follow_up", to="diagnoses.diagnosiscase")),
            ],
            options={"ordering": ["observed_at", "created_at"]},
        ),
    ]
