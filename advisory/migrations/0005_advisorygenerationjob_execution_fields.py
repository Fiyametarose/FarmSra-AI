from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("advisory", "0004_knowledgedocument_knowledgedocumentversion_and_more")]
    operations = [
        migrations.AddField(
            model_name="advisorygenerationjob", name="stage",
            field=models.CharField(default="preparing", max_length=32),
        ),
        migrations.AddField(
            model_name="advisorygenerationjob", name="attempt_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="advisorygenerationjob", name="claimed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
