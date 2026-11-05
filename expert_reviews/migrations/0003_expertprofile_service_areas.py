from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [("expert_reviews", "0002_expertreferral_recipient_choice_and_more")]

    operations = [
        migrations.AddField(
            model_name="expertprofile",
            name="service_areas",
            field=models.TextField(
                blank=True,
                validators=[django.core.validators.MaxLengthValidator(500)],
            ),
        ),
    ]
