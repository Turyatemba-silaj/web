from django.db import migrations


def assign_jinja_sites_to_greater_jinja(apps, schema_editor):
    Region = apps.get_model("webCom", "Region")
    Site = apps.get_model("webCom", "Site")
    greater_jinja = Region.objects.filter(region_name="Greater Jinja").first()
    if not greater_jinja:
        return
    Site.objects.filter(site_name__icontains="jinja").update(region=greater_jinja)
    Site.objects.filter(site_address__icontains="jinja").update(region=greater_jinja)


def restore_jinja_sites_to_eastern_region(apps, schema_editor):
    Region = apps.get_model("webCom", "Region")
    Site = apps.get_model("webCom", "Site")
    eastern_region = Region.objects.filter(region_name="Eastern Region").first()
    if not eastern_region:
        return
    Site.objects.filter(site_name__icontains="jinja").update(region=eastern_region)
    Site.objects.filter(site_address__icontains="jinja").update(region=eastern_region)


class Migration(migrations.Migration):

    dependencies = [
        ("webCom", "0076_incident_affected_items_details_and_more"),
    ]

    operations = [
        migrations.RunPython(assign_jinja_sites_to_greater_jinja, restore_jinja_sites_to_eastern_region),
    ]
