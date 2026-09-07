from django.db import migrations


def mark_existing_paid(apps, schema_editor):
    """Payments recorded before approval existed are settled money.

    `status` defaults to 'pending' for everything recorded from here on, but
    rows that predate the rule were never going to be approved by anyone — and
    they have already been counted against their orders' balances. Leaving them
    pending would spring every balance back up. They are paid.
    """
    SupplierPayment = apps.get_model('finance', 'SupplierPayment')
    SupplierPayment.objects.update(status='paid')


def unmark(apps, schema_editor):
    # Reversing only puts the column back the way the schema migration left it.
    SupplierPayment = apps.get_model('finance', 'SupplierPayment')
    SupplierPayment.objects.update(status='pending')


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0011_alter_supplierpayment_options_and_more'),
    ]

    operations = [
        migrations.RunPython(mark_existing_paid, unmark),
    ]
