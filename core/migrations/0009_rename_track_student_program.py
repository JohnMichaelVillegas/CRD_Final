from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_remove_student_school'),
    ]

    operations = [
        migrations.AddField(
            model_name='student',
            name='program',
            field=models.CharField(
                blank=True,
                max_length=100
            ),
        ),
    ]