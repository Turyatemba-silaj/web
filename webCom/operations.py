import csv
import io
import re
import zipfile
from datetime import datetime, time
from xml.etree import ElementTree as ET

from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from .access import require_model_access, require_view_access
from .forms_operations import DutyRosterExportForm, DutyRosterUploadForm, IncidentGuardsOnDutyMixin, IncidentManagementForm
from .models import (
    Asset,
    AssetAssignment,
    Contract,
    Deployment,
    DeploymentArea,
    Employee,
    Incident,
    IncidentNotification,
    Shift,
    Site,
)
from .views import AssetStockGroup, asset_stock_groups, render_page

def get_incident_authority_recipients():
    recipients = []

    supervisors = Employee.objects.filter(status="active", role="supervisor")
    for employee in supervisors:
        recipients.append((employee, "Supervisor"))

    managers = Employee.objects.filter(status="active", role="manager")
    for employee in managers:
        recipients.append((employee, "Manager"))

    hr_staff = Employee.objects.filter(status="active", department="hr")
    for employee in hr_staff:
        recipients.append((employee, "Human Resource"))

    seen = set()
    unique_recipients = []
    for employee, authority_group in recipients:
        key = (employee.pk, authority_group)
        if key not in seen:
            seen.add(key)
            unique_recipients.append((employee, authority_group))
    return unique_recipients


def build_incident_notification_message(incident):
    return (
        f"Incident alert: {incident.get_incident_type_display()} at {incident.site}. "
        f"Severity: {incident.get_severity_level_display()}. "
        f"Date: {incident.date_time}. Reported by: {incident.reported_by}."
    )


def deliver_incident_notification(notification):
    if not notification.recipient.email:
        notification.status = "pending"
        notification.delivery_note = "Recipient has no email address."
        notification.save(update_fields=["status", "delivery_note", "updated_at"])
        return

    if not getattr(settings, "EMAIL_HOST", "") or "console.EmailBackend" in getattr(settings, "EMAIL_BACKEND", ""):
        notification.status = "pending"
        notification.delivery_note = "SMTP email is not configured; notification was not sent to an inbox."
        notification.notified_at = timezone.now()
        notification.save(update_fields=["status", "delivery_note", "notified_at", "updated_at"])
        return

    try:
        send_mail(
            subject=f"Incident alert: {notification.incident.get_severity_level_display()}",
            message=notification.message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[notification.recipient.email],
            fail_silently=False,
        )
        notification.status = "sent"
        notification.delivery_note = "Email notification sent."
    except Exception as exc:
        notification.status = "failed"
        notification.delivery_note = str(exc)[:255]
    notification.notified_at = timezone.now()
    notification.save(update_fields=["status", "delivery_note", "notified_at", "updated_at"])


def send_incident_notifications(incident):
    recipients = get_incident_authority_recipients()
    if not recipients:
        return {
            "total_recipients": 0,
            "sent_count": 0,
            "failed_count": 0,
            "pending_count": 0,
            "results": [],
        }

    message = build_incident_notification_message(incident)
    if incident.status == "reported":
        incident.status = "notified"
    incident.notification_summary = f"Authorities notified on {timezone.now().strftime('%Y-%m-%d %H:%M')}."
    incident.save(update_fields=["status", "notification_summary", "updated_at"])

    results = []
    for employee, authority_group in recipients:
        notification, created = IncidentNotification.objects.get_or_create(
            incident=incident,
            recipient=employee,
            authority_group=authority_group,
            defaults={"message": message, "status": "pending"},
        )
        if not created:
            notification.message = message
            notification.status = "pending"
            notification.delivery_note = ""
            notification.save(update_fields=["message", "status", "delivery_note", "updated_at"])
        deliver_incident_notification(notification)
        results.append(
            {
                "recipient_id": employee.pk,
                "recipient": str(employee),
                "email": employee.email,
                "authority_group": authority_group,
                "status": notification.status,
                "delivery_note": notification.delivery_note,
                "notified_at": notification.notified_at.isoformat() if notification.notified_at else None,
            }
        )

    return {
        "total_recipients": len(results),
        "sent_count": sum(1 for result in results if result["status"] == "sent"),
        "failed_count": sum(1 for result in results if result["status"] == "failed"),
        "pending_count": sum(1 for result in results if result["status"] == "pending"),
        "results": results,
    }


def relief_guard_capacity(required_count):
    return required_count + 1 if required_count > 0 else 0


def rotating_scheduled_guards(guards, required_count, work_date, cycle_start):
    if required_count <= 0:
        return []
    guards = list(guards)
    if len(guards) <= required_count:
        return guards[:required_count]
    if not work_date or not cycle_start:
        return guards[:required_count]

    days_elapsed = max((work_date - cycle_start).days, 0)
    rotation_index = days_elapsed % len(guards)
    rotated_guards = guards[rotation_index:] + guards[:rotation_index]
    return rotated_guards[:required_count]


def rotating_available_guards(guards, required_count, work_date, cycle_start, excluded_guard_ids=None):
    excluded_guard_ids = set(excluded_guard_ids or [])
    available_guards = [guard for guard in guards if guard.pk not in excluded_guard_ids]
    return rotating_scheduled_guards(available_guards, required_count, work_date, cycle_start)

def site_roster_staff(site, work_date):
    if not site or not work_date:
        return []

    assigned_staff = site.guards.filter(
        role__in=("guard", "supervisor"),
        status="active",
    )
    area_staff = Employee.objects.none()
    area_regions = site.deployment_area_regions()
    if area_regions.exists():
        area_staff = Employee.objects.filter(
            role__in=("guard", "supervisor"),
            status="active",
            deployment_areas__region__in=area_regions,
            deployment_areas__status="active",
            deployment_areas__start_date__lte=work_date,
        ).filter(
            Q(deployment_areas__end_date__isnull=True) | Q(deployment_areas__end_date__gte=work_date)
        )
    return list(
        (assigned_staff | area_staff).distinct().order_by("employee_number", "first_name", "last_name")
    )

def load_site_scheduled_guards(site, required_count, work_date):
    if required_count <= 0 or not site or not site.region_id or not work_date:
        return 0
    required_count = relief_guard_capacity(required_count)
    assigned_count = site.guards.filter(role__in=("guard", "supervisor"), status="active").count()
    if assigned_count >= required_count:
        return 0

    needed = required_count - assigned_count
    assigned_elsewhere = Employee.objects.filter(
        assigned_sites__isnull=False,
        is_reliever=False,
    ).exclude(assigned_sites=site)
    area_regions = site.deployment_area_regions()
    available_guards = Employee.objects.none()
    if area_regions.exists():
        available_guards = (
            Employee.objects.filter(
                role__in=("guard", "supervisor"),
                status="active",
                deployment_areas__region__in=area_regions,
                deployment_areas__status="active",
                deployment_areas__start_date__lte=work_date,
            )
            .filter(Q(deployment_areas__end_date__isnull=True) | Q(deployment_areas__end_date__gte=work_date))
            .exclude(pk__in=assigned_elsewhere.values("pk"))
            .exclude(assigned_sites=site)
            .distinct()
            .order_by("employee_number", "first_name", "last_name")[:needed]
        )
    guards_to_add = list(available_guards)
    if guards_to_add:
        site.guards.add(*guards_to_add)
    return len(guards_to_add)

def csv_value(row, *names):
    for name in names:
        value = row.get(name) or row.get(name.lower()) or row.get(name.upper())
        if value not in (None, ""):
            return str(value).strip()
    return ""


def normalize_header(value):
    return str(value or "").strip().lower().replace("_", " ")


def parse_roster_date(value):
    clean_value = str(value or "").strip()
    clean_value = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", clean_value, flags=re.IGNORECASE)
    clean_value = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", clean_value)
    clean_value = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", clean_value)
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%d %m %Y", "%d %m %y", "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(clean_value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD.")


def parse_roster_time(value, default):
    if not value:
        return default
    clean_value = value.lower().replace("hrs", "").replace(" ", "").strip()
    for fmt in ("%H:%M", "%H%M"):
        try:
            return datetime.strptime(clean_value, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Invalid time '{value}'. Use HH:MM or 0700.")


def parse_scheduled_period(rows):
    date_pattern = r"\d{4}[-.]\d{2}[-.]\d{2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"
    for row in rows:
        line = " ".join(cell.strip() for cell in row if cell.strip())
        if "scheduled period" in line.lower():
            matches = re.findall(date_pattern, line)
            if len(matches) >= 2:
                return parse_roster_date(matches[0]), parse_roster_date(matches[1])
            _, _, period_text = line.partition(":")
            start_text, _, end_text = period_text.strip().partition(" to ")
            return parse_roster_date(start_text.strip()), parse_roster_date(end_text.strip())
    return None, None


def infer_site_name_from_matrix(rows):
    for row in rows[:8]:
        line = " ".join(cell.strip() for cell in row if cell.strip())
        if "site roster for" in line.lower():
            _, _, site_text = line.partition("Site Roster for")
            site_text = site_text.strip()
            if ":" in site_text:
                site_text = site_text.split(":", 1)[1].strip()
            return site_text
    return ""


def find_site_by_name(site_name):
    if not site_name:
        raise ValueError("Select a site or include a recognizable site name in the roster heading.")
    exact = Site.objects.filter(site_name__iexact=site_name).first()
    if exact:
        return exact
    contains = Site.objects.filter(site_name__icontains=site_name).first()
    if contains:
        return contains
    raise ValueError(f"Site '{site_name}' was not found.")


def get_roster_site(row, selected_site):
    if selected_site:
        return selected_site
    site_id = csv_value(row, "site_id", "site")
    site_name = csv_value(row, "site_name")
    if site_id.isdigit():
        return Site.objects.get(pk=site_id)
    if site_name:
        return find_site_by_name(site_name)
    if site_id:
        return find_site_by_name(site_id)
    raise ValueError("Provide a site on the form or include site_id/site_name in the CSV.")


def get_roster_guard(row):
    guard_id = csv_value(row, "guard_id", "guard", "pers no", "pers_no")
    employee_id = csv_value(row, "employee_id")
    employee_name = csv_value(row, "employee_name", "guard_name", "scheduled_guard", "name")
    if guard_id.isdigit():
        employee = Employee.objects.filter(pk=guard_id, role="guard").first()
        if employee:
            return employee
    if employee_id.isdigit():
        return Employee.objects.get(pk=employee_id, role="guard")
    if employee_name:
        if employee_name.lower() == "shortage guard":
            return None
        parts = employee_name.split()
        if len(parts) >= 2:
            return Employee.objects.get(
                role="guard",
                first_name__iexact=parts[0],
                last_name__iexact=" ".join(parts[1:]),
            )
    raise ValueError("Include guard_id, employee_id, or employee_name in the CSV.")


def is_xlsx_upload(uploaded_file):
    name = (getattr(uploaded_file, "name", "") or "").lower()
    content_type = (getattr(uploaded_file, "content_type", "") or "").lower()
    return name.endswith(".xlsx") or "spreadsheetml" in content_type


def xlsx_column_index(cell_ref):
    column_letters = re.sub(r"\d", "", cell_ref or "")
    index = 0
    for letter in column_letters:
        index = index * 26 + (ord(letter.upper()) - ord("A") + 1)
    return max(index - 1, 0)


def read_xlsx_shared_strings(archive):
    try:
        xml_data = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(xml_data)
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    strings = []
    for item in root.findall("x:si", namespace):
        parts = [node.text or "" for node in item.findall(".//x:t", namespace)]
        strings.append("".join(parts))
    return strings


def read_xlsx_rows(raw_data):
    with zipfile.ZipFile(io.BytesIO(raw_data)) as archive:
        shared_strings = read_xlsx_shared_strings(archive)
        worksheet_name = "xl/worksheets/sheet1.xml"
        try:
            xml_data = archive.read(worksheet_name)
        except KeyError as exc:
            raise ValueError("The uploaded workbook does not contain a first worksheet.") from exc

    root = ET.fromstring(xml_data)
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows = []
    for row_node in root.findall(".//x:sheetData/x:row", namespace):
        row_values = []
        for cell in row_node.findall("x:c", namespace):
            column_index = xlsx_column_index(cell.attrib.get("r", ""))
            while len(row_values) < column_index:
                row_values.append("")

            cell_type = cell.attrib.get("t")
            value_node = cell.find("x:v", namespace)
            inline_node = cell.find("x:is/x:t", namespace)
            value = ""
            if cell_type == "s" and value_node is not None:
                shared_index = int(value_node.text or 0)
                value = shared_strings[shared_index] if shared_index < len(shared_strings) else ""
            elif inline_node is not None:
                value = inline_node.text or ""
            elif value_node is not None:
                value = value_node.text or ""
            row_values.append(str(value).strip())
        rows.append(row_values)
    return rows

def decode_roster_file(raw_data):
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw_data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_data.decode("latin-1", errors="replace")

def detect_roster_dialect(decoded_file):
    sample = decoded_file[:2048]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        if "\t" in sample:
            return csv.excel_tab
        return csv.excel


def matrix_column_date(header, period_start, period_end):
    if not period_start or not period_end:
        return None
    clean_header = str(header or "").strip()
    if not clean_header:
        return None

    try:
        parsed_date = parse_roster_date(clean_header)
        if period_start <= parsed_date <= period_end:
            return parsed_date
    except ValueError:
        pass

    if re.fullmatch(r"\d+(\.0+)?", clean_header):
        number = int(float(clean_header))
        if number > 31:
            excel_date = datetime(1899, 12, 30).date() + timezone.timedelta(days=number)
            if period_start <= excel_date <= period_end:
                return excel_date

    candidates = []
    if "/" in clean_header:
        candidates.extend(re.findall(r"\d{1,2}", clean_header.split("/", 1)[1]))
    candidates.extend(re.findall(r"\d{1,2}", clean_header))
    seen = set()
    for candidate in candidates:
        day = int(candidate)
        if day in seen:
            continue
        seen.add(day)
        cursor = period_start
        while cursor <= period_end:
            if cursor.day == day:
                return cursor
            cursor += timezone.timedelta(days=1)
    return None


def attach_guard_to_site(site, guard):
    if not guard or site.guards.filter(pk=guard.pk).exists():
        return
    relief_guard_capacity = site.number_of_guards + 1 if site.number_of_guards > 0 else 0
    if site.guards.count() >= relief_guard_capacity:
        raise ValueError(f"{site} already has the required guards plus one relief guard.")
    site.guards.add(guard)

def create_roster_deployment(site, guard, shift_date, shift_type):
    if guard:
        site.guards.add(guard)
        if site.region_id:
            DeploymentArea.objects.get_or_create(
                employee=guard,
                region=site.region,
                start_date=shift_date,
                defaults={"status": "active"},
            )
    default_start = time(7, 0) if shift_type == "day" else time(18, 0)
    default_end = time(18, 0) if shift_type == "day" else time(7, 0)
    shift, _ = Shift.objects.get_or_create(
        start_time=default_start,
        end_time=default_end,
        defaults={"hours_per_shift": 0},
    )
    Deployment.objects.get_or_create(
        client=site.client,
        site=site,
        shift=shift,
        start_date=shift_date,
        end_date=shift_date,
        status="active",
    )


def import_matrix_roster(rows, selected_site):
    period_start, period_end = parse_scheduled_period(rows)
    if not period_start or not period_end:
        raise ValueError("Scheduled Period row was not found.")

    site = selected_site or find_site_by_name(infer_site_name_from_matrix(rows))
    header_index = None
    header = []
    for index, row in enumerate(rows):
        normalized = [normalize_header(cell) for cell in row]
        if "pers no" in normalized and "name" in normalized:
            header_index = index
            header = row
            break
    if header_index is None:
        raise ValueError("Pers No / Name header row was not found.")

    normalized_header = [normalize_header(cell) for cell in header]
    pers_index = normalized_header.index("pers no")
    name_index = normalized_header.index("name")
    date_columns = []
    for index, label in enumerate(header):
        shift_date = matrix_column_date(str(label).strip(), period_start, period_end)
        if shift_date:
            date_columns.append((index, shift_date))

    saved_count = 0
    for row_number, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        if not any(cell.strip() for cell in row):
            continue
        row += [""] * (len(header) - len(row))
        guard_name = row[name_index].strip()
        pers_no = row[pers_index].strip()
        if not guard_name:
            continue
        guard = get_roster_guard({"pers no": pers_no, "name": guard_name})
        for column_index, shift_date in date_columns:
            marker = row[column_index].strip().upper()
            if marker in ("", "O", "OFF", "R"):
                continue
            if marker == "D":
                shift_type = "day"
            elif marker == "N":
                shift_type = "night"
            else:
                raise ValueError(f"CSV row {row_number}: unknown duty marker '{marker}'. Use D, N, or O.")
            create_roster_deployment(site, guard, shift_date, shift_type)
            saved_count += 1
    return saved_count


def import_flat_roster(decoded_file, selected_site):
    reader = csv.DictReader(io.StringIO(decoded_file, newline=""), dialect=detect_roster_dialect(decoded_file))
    saved_count = 0
    line_number = 1
    for line_number, row in enumerate(reader, start=2):
        off_value = csv_value(row, "off", "day_off")
        if off_value.lower() in ("1", "yes", "true", "off", "o"):
            continue

        site = get_roster_site(row, selected_site)
        guard = get_roster_guard(row)
        shift_date = parse_roster_date(csv_value(row, "shift_date", "date", "duty_date", "start_date"))
        end_date_value = csv_value(row, "end_date", "deployment_end_date")
        end_date = parse_roster_date(end_date_value) if end_date_value else shift_date
        shift_type = csv_value(row, "shift_type").lower()
        if shift_type in ("d", "day"):
            shift_type = "day"
        elif shift_type in ("n", "night"):
            shift_type = "night"
        else:
            shift_type = "day"

        default_start = time(7, 0) if shift_type == "day" else time(18, 0)
        default_end = time(18, 0) if shift_type == "day" else time(7, 0)
        start_time = parse_roster_time(csv_value(row, "start_time"), default_start)
        end_time = parse_roster_time(csv_value(row, "end_time"), default_end)
        shift, _ = Shift.objects.get_or_create(
            start_time=start_time,
            end_time=end_time,
            defaults={"hours_per_shift": 0},
        )
        if guard:
            site.guards.add(guard)
        Deployment.objects.get_or_create(
            client=site.client,
            site=site,
            shift=shift,
            start_date=shift_date,
            end_date=end_date,
            status="active",
        )
        saved_count += 1
    return saved_count, line_number


def roster_period_dates(period_start, period_end):
    dates = []
    cursor = period_start
    while cursor <= period_end:
        dates.append(cursor)
        cursor += timezone.timedelta(days=1)
    return dates


def roster_day_label(work_date):
    day_labels = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    return f"{day_labels[work_date.weekday()]}/{work_date.day:02d}"


def export_roster_staff(site, period_start, period_end):
    return list(
        site.guards.filter(
            role="guard",
            status="active",
        ).order_by("employee_number", "first_name", "last_name")
    )

def employee_roster_name(employee):
    return f"{employee.first_name} {employee.last_name}".strip()


def build_monthly_roster_matrix(site, period_start, period_end):
    dates = roster_period_dates(period_start, period_end)
    guards = export_roster_staff(site, period_start, period_end)
    total_required = site.day_shift_guards + site.night_shift_guards
    rows = [
        {
            "employee": guard,
            "pers_no": guard.employee_number or guard.pk,
            "grade": "",
            "name": employee_roster_name(guard),
            "contact": guard.phone_number or "",
            "markers": {work_date: "O" for work_date in dates},
        }
        for guard in guards
    ]
    row_by_guard_id = {row["employee"].pk: row for row in rows}
    shortage_rows = []

    for work_date in dates:
        scheduled = rotating_scheduled_guards(guards, total_required, work_date, period_start)
        day_guards = scheduled[: site.day_shift_guards]
        night_guards = scheduled[site.day_shift_guards : site.day_shift_guards + site.night_shift_guards]

        for guard in day_guards:
            row_by_guard_id[guard.pk]["markers"][work_date] = "D"
        for guard in night_guards:
            row_by_guard_id[guard.pk]["markers"][work_date] = "N"

        shortage_markers = []
        shortage_markers.extend(["D"] * max(site.day_shift_guards - len(day_guards), 0))
        shortage_markers.extend(["N"] * max(site.night_shift_guards - len(night_guards), 0))
        for index, marker in enumerate(shortage_markers):
            if index >= len(shortage_rows):
                shortage_rows.append(
                    {
                        "employee": None,
                        "pers_no": "zz",
                        "grade": "",
                        "name": "Shortage Guard",
                        "contact": "",
                        "markers": {date_value: "O" for date_value in dates},
                    }
                )
            shortage_rows[index]["markers"][work_date] = marker

    return dates, rows + shortage_rows


def next_roster_period(period_start, period_end):
    next_start = period_end + timezone.timedelta(days=1)
    if period_start.day == 26 and period_end.day == 25:
        if next_start.month == 12:
            next_end = next_start.replace(year=next_start.year + 1, month=1, day=25)
        else:
            next_end = next_start.replace(month=next_start.month + 1, day=25)
        return next_start, next_end
    return next_start, next_start + (period_end - period_start)


def roster_site_reference(site):
    if site.contract_id and site.contract.contract_number:
        return f"{site.contract.contract_number}:{site.site_name}"
    return site.site_name


def write_monthly_roster_csv(response, site, period_start, period_end):
    company_name = getattr(settings, "COMPANY_NAME", "TURYANS SECURITY COMPANY (U) LIMITED")
    dates, rows = build_monthly_roster_matrix(site, period_start, period_end)
    writer = csv.writer(response)
    trailing_blanks = [""] * len(dates)
    generated_at = timezone.localtime().strftime("%Y-%m-%d %H:%M")
    deployment_area = site.region.region_name if site.region_id else "-"
    next_start, next_end = next_roster_period(period_start, period_end)

    writer.writerow([f"{generated_at} {company_name} Site Roster for {roster_site_reference(site)}", "", "", "", "", *trailing_blanks])
    writer.writerow([])
    writer.writerow([f"Deployment Area: {deployment_area}", "", "", "", "", *trailing_blanks])
    writer.writerow([])
    writer.writerow([
        f"Scheduled Period: {period_start:%Y-%m-%d} to {period_end:%Y-%m-%d}",
        "",
        f"{next_start:%Y.%m.%d} to",
        f"{next_end:%Y.%m.%d}",
        "",
        *trailing_blanks,
    ])
    writer.writerow([])
    writer.writerow(["Pers No", "Grade", "Name", "Contact", "Worked days", *[roster_day_label(work_date) for work_date in dates]])
    for row in rows:
        writer.writerow([
            row["pers_no"],
            row["grade"],
            row["name"],
            row["contact"],
            "",
            *[row["markers"][work_date] for work_date in dates],
        ])


def is_matrix_roster(rows):
    for row in rows:
        normalized = [normalize_header(cell) for cell in row]
        if "pers no" in normalized and "name" in normalized:
            return True
    return False


def default_monthly_roster_period(reference_date=None):
    reference_date = reference_date or timezone.localdate()
    if reference_date.day >= 26:
        period_start = reference_date.replace(day=26)
    elif reference_date.month == 1:
        period_start = reference_date.replace(year=reference_date.year - 1, month=12, day=26)
    else:
        period_start = reference_date.replace(month=reference_date.month - 1, day=26)

    if period_start.month == 12:
        period_end = period_start.replace(year=period_start.year + 1, month=1, day=25)
    else:
        period_end = period_start.replace(month=period_start.month + 1, day=25)
    return period_start, period_end


def duty_roster_export(request):
    require_view_access(request, "duty_roster_export")
    period_start, period_end = default_monthly_roster_period()
    form = DutyRosterExportForm(
        request.POST or None,
        initial={"period_start": period_start, "period_end": period_end},
    )

    if request.method == "POST" and form.is_valid():
        site = form.cleaned_data["site"]
        period_start = form.cleaned_data["period_start"]
        period_end = form.cleaned_data["period_end"]
        filename = f"duty-roster-{site.site_name}-{period_start:%Y%m%d}-{period_end:%Y%m%d}.csv"
        filename = re.sub(r"[^A-Za-z0-9_.-]+", "-", filename).strip("-")
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        write_monthly_roster_csv(response, site, period_start, period_end)
        return response

    context = {
        "title": "Export Duty Roster",
        "form": form,
    }
    return render_page(request, "webCom/duty_roster_export.html", context, "deployments")


def duty_roster_upload(request):
    require_view_access(request, "duty_roster_upload")
    form = DutyRosterUploadForm(request.POST or None, request.FILES or None)
    saved_count = 0
    errors = []

    if request.method == "POST" and form.is_valid():
        selected_site = form.cleaned_data.get("site")
        uploaded_file = form.cleaned_data["roster_file"]
        raw_data = uploaded_file.read()
        decoded_file = ""
        if is_xlsx_upload(uploaded_file):
            rows = read_xlsx_rows(raw_data)
        else:
            decoded_file = decode_roster_file(raw_data)
            dialect = detect_roster_dialect(decoded_file)
            rows = list(csv.reader(io.StringIO(decoded_file, newline=""), dialect=dialect))

        try:
            with transaction.atomic():
                if is_matrix_roster(rows):
                    saved_count = import_matrix_roster(rows, selected_site)
                else:
                    if is_xlsx_upload(uploaded_file):
                        raise ValueError("Excel uploads must use the monthly roster format with Pers No, Name, and date columns.")
                    saved_count, line_number = import_flat_roster(decoded_file, selected_site)
        except Exception as exc:
            errors.append(str(exc))
            saved_count = 0

        if saved_count:
            messages.success(request, f"{saved_count} duty roster deployment(s) uploaded successfully.")
            return redirect("webcom:list", model_name="deployments")

    context = {
        "title": "Upload Duty Roster",
        "form": form,
        "errors": errors,
    }
    return render_page(request, "webCom/duty_roster_upload.html", context, "deployments")

def asset_store_report(request):
    require_view_access(request, "asset_store_report")
    assets = Asset.objects.prefetch_related("assignments").order_by("asset_type", "asset_number")
    report_rows = []
    for asset in asset_stock_groups(assets):
        report_rows.append(
            {
                "asset": asset,
                "store_quantity": asset.quantity,
                "assets_issued_out": asset.assets_issued_out,
                "stock_balance": asset.stock_balance,
                "low_stock_message": asset.low_stock_message,
                "notes": "-",
            }
        )

    context = {
        "title": "Asset Store Report",
        "report_rows": report_rows,
    }
    return render_page(request, "webCom/asset_store_report.html", context, "assets")


def asset_group_detail(request):
    require_model_access(request, "assets")
    asset_type = request.GET.get("asset_type", "")
    asset_name_key = request.GET.get("asset_name", "")
    assets = [
        asset
        for asset in Asset.objects.filter(asset_type=asset_type).prefetch_related("assignments").order_by("asset_number", "asset_id")
        if AssetStockGroup.normalized_asset_name(asset.asset_name) == asset_name_key
    ]
    if not assets:
        messages.warning(request, "Asset group was not found.")
        return redirect("webcom:list", model_name="assets")

    group = AssetStockGroup(assets)
    individual_rows = []
    for asset in assets:
        individual_rows.append(
            {
                "asset": asset,
                "store_quantity": asset.quantity,
                "assets_issued_out": asset.assets_issued_out,
                "stock_balance": asset.stock_balance,
                "low_stock_message": asset.low_stock_message,
            }
        )

    context = {
        "title": f"{group.get_asset_type_display()} - {group.asset_name or 'Unnamed Asset'}",
        "group": group,
        "individual_rows": individual_rows,
    }
    return render_page(request, "webCom/asset_group_detail.html", context, "assets")


def matching_asset_group_from_request(request):
    asset_id = request.GET.get("asset_id", "")
    if asset_id:
        asset = get_object_or_404(Asset.objects.prefetch_related("assignments"), pk=asset_id)
        assets = [asset]
    else:
        asset_type = request.GET.get("asset_type", "")
        asset_name_key = request.GET.get("asset_name", "")
        assets = [
            asset
            for asset in Asset.objects.filter(asset_type=asset_type).prefetch_related("assignments").order_by("asset_number", "asset_id")
            if AssetStockGroup.normalized_asset_name(asset.asset_name) == asset_name_key
        ]
    if not assets:
        return None, []
    return AssetStockGroup(assets), assets


def asset_issued_out_detail(request):
    require_model_access(request, "assets")
    group, assets = matching_asset_group_from_request(request)
    if group is None:
        messages.warning(request, "Issued-out asset group was not found.")
        return redirect("webcom:list", model_name="assets")

    assignments = (
        AssetAssignment.objects.filter(asset__in=assets, status="assigned")
        .select_related("asset", "guard", "driver", "site", "deployment", "issued_by", "received_by")
        .order_by("asset__asset_number", "-assigned_date", "-assignment_id")
    )
    issue_rows = []
    for assignment in assignments:
        issue_rows.append(
            {
                "assignment": assignment,
                "asset": assignment.asset,
                "quantity": assignment.quantity,
                "received_by": assignment.received_by or assignment.assigned_to,
                "issued_by": assignment.issued_by or "-",
                "assigned_to": assignment.assigned_to,
                "stock_balance": assignment.asset.stock_balance,
            }
        )

    context = {
        "title": f"Issued Out - {group.get_asset_type_display()} {group.asset_name or ''}".strip(),
        "group": group,
        "issue_rows": issue_rows,
    }
    return render_page(request, "webCom/asset_issued_out_detail.html", context, "assets")

def asset_assignment_report(request):
    require_view_access(request, "asset_assignment_report")
    assets = Asset.objects.prefetch_related(
        "assignments__guard",
        "assignments__driver",
        "assignments__site",
        "assignments__deployment",
    ).order_by("asset_type", "asset_number")
    report_rows = []

    for asset in assets:
        assigned_total = sum(assignment.quantity for assignment in asset.assignments.filter(status="assigned"))
        assignment_rows = asset.assignments.all().order_by("assigned_date", "pk")
        for assignment in assignment_rows:
            report_rows.append(
                {
                    "asset": asset,
                    "quantity": assignment.quantity,
                    "status": assignment.get_status_display(),
                    "accountability_status": assignment.get_accountability_status_display(),
                    "assigned_to": assignment.assigned_to,
                    "issued_by": assignment.issued_by or "-",
                    "received_by": assignment.received_by or "-",
                    "site": assignment.site or "-",
                    "deployment": assignment.deployment or "-",
                    "assigned_date": assignment.assigned_date,
                    "return_date": assignment.return_date or "-",
                    "condition_issued": assignment.get_condition_issued_display(),
                    "condition_returned": assignment.get_condition_returned_display() if assignment.condition_returned else "-",
                }
            )

        unassigned_quantity = asset.quantity - assigned_total
        if unassigned_quantity > 0:
            report_rows.append(
                {
                    "asset": asset,
                    "quantity": unassigned_quantity,
                    "status": "Unassigned",
                    "accountability_status": "-",
                    "assigned_to": "-",
                    "issued_by": "-",
                    "received_by": "-",
                    "site": "-",
                    "deployment": "-",
                    "assigned_date": "-",
                    "return_date": "-",
                    "condition_issued": "-",
                    "condition_returned": "-",
                }
            )

    context = {
        "title": "Asset Accountability Report",
        "report_rows": report_rows,
    }
    return render_page(request, "webCom/asset_assignment_report.html", context, "asset-assignments")

def contract_report(request, pk):
    require_view_access(request, "contract_report")
    contract = get_object_or_404(
        Contract.objects.select_related("client").prefetch_related("sites", "deliverables"),
        pk=pk,
    )
    sites = list(contract.sites.prefetch_related("guards").all().order_by("site_name"))
    deliverables = contract.deliverables.all().order_by("item_name")

    coverage_by_site = {site.pk: {"day": False, "night": False} for site in sites}
    deployments = (
        Deployment.objects.select_related("shift", "site")
        .filter(site__in=sites, status="active", start_date__lte=contract.contract_end_date)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=contract.contract_start_date))
        .order_by("site__site_name", "shift__start_time")
    )
    for deployment in deployments:
        site_coverage = coverage_by_site.get(deployment.site_id)
        if site_coverage is not None:
            for shift_type in deployment.covered_shift_types:
                site_coverage[shift_type] = True

    site_rows = []
    total_scheduled_day_guards = 0
    total_scheduled_night_guards = 0
    for site in sites:
        assigned_guards = list(site.guards.all())
        assigned_guard_count = len(assigned_guards)
        coverage = coverage_by_site[site.pk]
        scheduled_day_guards = min(assigned_guard_count, site.day_shift_guards) if coverage["day"] else 0
        scheduled_night_guards = min(assigned_guard_count, site.night_shift_guards) if coverage["night"] else 0
        total_scheduled_day_guards += scheduled_day_guards
        total_scheduled_night_guards += scheduled_night_guards
        site_rows.append(
            {
                "site": site,
                "scheduled_day_guards": scheduled_day_guards,
                "scheduled_night_guards": scheduled_night_guards,
                "scheduled_guard_names": ", ".join(str(guard) for guard in assigned_guards) or "-",
                "scheduled_total_price": (scheduled_day_guards + scheduled_night_guards) * contract.rate_per_guard,
            }
        )

    context = {
        "contract": contract,
        "client": contract.client,
        "sites": sites,
        "site_rows": site_rows,
        "total_scheduled_day_guards": total_scheduled_day_guards,
        "total_scheduled_night_guards": total_scheduled_night_guards,
        "total_scheduled_guards": total_scheduled_day_guards + total_scheduled_night_guards,
        "scheduled_guard_contract_value": (total_scheduled_day_guards + total_scheduled_night_guards) * contract.rate_per_guard,
        "deliverables": deliverables,
        "title": f"Contract Report - {contract.client.client_name}",
    }
    return render_page(request, "webCom/contract_report.html", context, "contracts")

def incident_notify(request, pk):
    require_view_access(request, "incident_notify")
    incident = get_object_or_404(Incident, pk=pk)
    delivery = send_incident_notifications(incident)
    if delivery["total_recipients"] == 0:
        messages.warning(request, "No active supervisors, managers, or human resource staff were found.")
        return redirect("webcom:list", model_name="incidents")

    messages.success(
        request,
        f"Incident notification prepared for {delivery['total_recipients']} authority contact(s); {delivery['sent_count']} email(s) sent.",
    )
    return redirect("webcom:incident_notifications")


def incident_notify_api(request, pk):
    require_view_access(request, "incident_notify")
    if request.method != "POST":
        return JsonResponse(
            {"error": {"code": "method_not_allowed", "message": "Only POST is supported by this API endpoint."}},
            status=405,
        )

    incident = get_object_or_404(Incident.objects.select_related("site"), pk=pk)
    delivery = send_incident_notifications(incident)
    status_code = 200 if delivery["total_recipients"] else 404
    return JsonResponse(
        {
            "incident": {
                "id": incident.pk,
                "type": incident.get_incident_type_display(),
                "site": str(incident.site),
                "severity": incident.get_severity_level_display(),
                "status": incident.get_status_display(),
            },
            "email": {
                "live_smtp_configured": bool(getattr(settings, "EMAIL_HOST", "")),
                "backend": getattr(settings, "EMAIL_BACKEND", ""),
                "from_email": getattr(settings, "DEFAULT_FROM_EMAIL", ""),
            },
            "delivery": delivery,
        },
        status=status_code,
    )


def site_deployment_area_guards_api(request, pk):
    require_model_access(request, "incidents")
    if request.method != "GET":
        return JsonResponse(
            {"error": {"code": "method_not_allowed", "message": "Only GET is supported by this API endpoint."}},
            status=405,
        )
    site = get_object_or_404(Site.objects.select_related("region"), pk=pk)
    regions = site.deployment_area_regions()
    choices = IncidentGuardsOnDutyMixin.deployment_area_guard_choices(site)
    return JsonResponse(
        {
            "site": {"id": site.pk, "name": site.site_name, "deployment_area": str(site.region) if site.region_id else ""},
            "deployment_areas": list(regions.values_list("region_name", flat=True)),
            "guards": [{"value": value, "label": label} for value, label in choices],
        }
    )


def incident_manage(request, pk):
    require_view_access(request, "incident_manage")
    incident = get_object_or_404(
        Incident.objects.select_related("site", "investigation_assigned_to").prefetch_related("notifications__recipient"),
        pk=pk,
    )
    action = request.POST.get("incident_action", "save_updates") if request.method == "POST" else ""
    form = IncidentManagementForm(request.POST or None, instance=incident, action=action, user=request.user)
    if request.method == "POST" and form.is_valid():
        incident = form.save()
        messages.success(request, f"Incident updated. Current status: {incident.get_status_display()}.")
        return redirect("webcom:incident_manage", pk=incident.pk)

    status_steps = [
        ("reported", "Reported"),
        ("notified", "Authorities Notified"),
        ("investigating", "Under Investigation"),
        ("action_taken", "Action Taken"),
        ("resolved", "Resolved"),
        ("closed", "Closed"),
    ]
    status_order = [key for key, _label in status_steps]
    current_index = status_order.index(incident.status) if incident.status in status_order else 0
    context = {
        "title": f"Manage Incident - {incident}",
        "incident": incident,
        "form": form,
        "notifications": incident.notifications.select_related("recipient").order_by("-notified_at", "-notification_id"),
        "status_steps": [
            {"key": key, "label": label, "complete": index <= current_index}
            for index, (key, label) in enumerate(status_steps)
        ],
    }
    return render_page(request, "webCom/incident_manage.html", context, "incidents")


def incident_affected_item_rows(incident):
    rows = []
    for line in (incident.affected_items_details or "").splitlines():
        parts = [part.strip() for part in line.split("|")]
        if not any(parts):
            continue
        rows.append(
            {
                "item": parts[0] if len(parts) > 0 else "",
                "serial_number": parts[1] if len(parts) > 1 else "",
                "engraved_number": parts[2] if len(parts) > 2 else "",
            }
        )
    if not rows and (incident.alleged_stolen_items or incident.description):
        rows.append(
            {
                "item": incident.alleged_stolen_items or incident.description,
                "serial_number": "",
                "engraved_number": "",
            }
        )
    return rows


def incident_investigation_report(request, pk):
    require_view_access(request, "incident_report")
    incident = get_object_or_404(
        Incident.objects.select_related(
            "site",
            "site__client",
            "site__contract",
            "site__region",
            "investigation_assigned_to",
        ).prefetch_related("notifications__recipient"),
        pk=pk,
    )
    notifications = incident.notifications.select_related("recipient").order_by("notified_at", "notification_id")
    site_guards = incident.site.guards.order_by("first_name", "last_name", "employee_number")
    context = {
        "title": f"Investigation Report - Incident {incident.pk}",
        "company_name": getattr(settings, "COMPANY_NAME", "TURYANS SECURITY COMPANY (U) LIMITED"),
        "incident": incident,
        "site": incident.site,
        "client": incident.site.client,
        "contract": incident.site.contract,
        "notifications": notifications,
        "site_guards": site_guards,
        "affected_item_rows": incident_affected_item_rows(incident),
        "prepared_at": timezone.localtime(),
    }
    return render_page(request, "webCom/incident_investigation_report.html", context, "incidents")


def incident_notifications(request):
    require_view_access(request, "incident_notifications")
    notifications = IncidentNotification.objects.select_related(
        "incident",
        "incident__site",
        "recipient",
        
    ).order_by("-notified_at", "-notification_id")
    context = {
        "title": "Incident Notifications",
        "notifications": notifications,
    }
    return render_page(request, "webCom/incident_notifications.html", context, "incidents")

def incident_report(request):
    require_view_access(request, "incident_report")
    incidents = Incident.objects.select_related(
        "site",
        "investigation_assigned_to",
    ).prefetch_related(
        "notifications__recipient",
    ).order_by("-date_time", "-incident_id")

    report_rows = []
    status_counts = {key: 0 for key, _label in Incident.STATUS_CHOICES}
    severity_counts = {key: 0 for key, _label in Incident.SEVERITY_LEVEL_CHOICES}

    for incident in incidents:
        status_counts[incident.status] = status_counts.get(incident.status, 0) + 1
        severity_counts[incident.severity_level] = severity_counts.get(incident.severity_level, 0) + 1
        notifications = list(incident.notifications.all())
        report_rows.append(
            {
                "incident": incident,
                "notifications_count": len(notifications),
                "notified_to": ", ".join(str(notification.recipient) for notification in notifications) or "-",
            }
        )

    context = {
        "title": "Incident Report",
        "report_rows": report_rows,
        "total_incidents": len(report_rows),
        "status_counts": [(dict(Incident.STATUS_CHOICES).get(key, key), count) for key, count in status_counts.items()],
        "severity_counts": [(dict(Incident.SEVERITY_LEVEL_CHOICES).get(key, key), count) for key, count in severity_counts.items()],
    }
    return render_page(request, "webCom/incident_report.html", context, "incidents")
