import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("advisory", "0005_advisorygenerationjob_execution_fields"),
    ]
    operations = [
        migrations.AlterField(
            model_name="aichatsession", name="case",
            field=models.OneToOneField(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name="ai_chat_session", to="diagnoses.diagnosiscase",
            ),
        ),
        migrations.AddConstraint(
            model_name="aichatsession",
            constraint=models.UniqueConstraint(
                condition=models.Q(("case__isnull", True)), fields=("farmer",),
                name="advisory_one_general_chat_per_farmer",
            ),
        ),
    ]
