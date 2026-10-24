from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0016_sale_godown'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='lp_number',
            field=models.CharField(blank=True, max_length=50, verbose_name='LP No.'),
        ),
        migrations.AddField(
            model_name='sale',
            name='pi_number',
            field=models.CharField(blank=True, max_length=50, verbose_name='PI No.'),
        ),
        migrations.AddField(
            model_name='sale',
            name='order_number',
            field=models.CharField(blank=True, max_length=50, verbose_name='Order No.'),
        ),
        migrations.AddField(
            model_name='historicalsale',
            name='lp_number',
            field=models.CharField(blank=True, max_length=50, verbose_name='LP No.'),
        ),
        migrations.AddField(
            model_name='historicalsale',
            name='pi_number',
            field=models.CharField(blank=True, max_length=50, verbose_name='PI No.'),
        ),
        migrations.AddField(
            model_name='historicalsale',
            name='order_number',
            field=models.CharField(blank=True, max_length=50, verbose_name='Order No.'),
        ),
    ]
