"""Products saved by the bulk import with a type that is neither `product` nor
`service` — the import used to store the Type column's text as-is. The edit
form had no option for those values, so such a product could not be saved
again. Anything that is not a service is a stocked product."""

from django.db import migrations


def repair(apps, schema_editor):
    Product = apps.get_model('inventory', 'Product')
    Product.objects.exclude(product_type__in=['product', 'service']).update(product_type='product')


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0017_alter_purchaseorder_status'),
    ]

    operations = [
        migrations.RunPython(repair, migrations.RunPython.noop),
    ]
