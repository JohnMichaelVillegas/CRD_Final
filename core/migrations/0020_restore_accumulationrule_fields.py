
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0019_alter_accumulationrule_options_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="accumulationrule",
            name="description",
        ),
        migrations.RemoveField(
            model_name="accumulationrule",
            name="occurrence_count",
        ),
        migrations.RemoveField(
            model_name="accumulationrule",
            name="resulting_severity",
        ),
        migrations.AddField(
            model_name="accumulationrule",
            name="instance_range",
            field=models.CharField(max_length=50),
        ),
        migrations.AddField(
            model_name="accumulationrule",
            name="classification",
            field=models.CharField(
                choices=[
                    ("Infraction", "Infraction"),
                    ("Minor Offense", "Minor Offense"),
                    ("Major Offense", "Major Offense"),
                ],
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="accumulationrule",
            name="action_note",
            field=models.CharField(
                blank=True,
                default="",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="accumulationrule",
            name="order",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AlterModelOptions(
            name="accumulationrule",
            options={"ordering": ["order", "id"]},
        ),
    ]
