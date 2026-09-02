import csv
import io
from datetime import datetime

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.forms import modelformset_factory
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from .access import require_model_access
from .finance import (
    create_split_site_invoices,
    invoice_item_formset_has_rows,
    invoice_item_post_data,
    invoice_item_price_payload,
    invoice_update_fields,
    payroll_dashboard,
    procurement_dashboard,
    proforma_item_formset_has_rows,
    proforma_item_price_payload,
    refresh_budget_audit_notifications,
    refresh_expense_accountability_notifications,
    sync_receivables_from_payments,
    sync_salary_table,
    validate_proforma_total_against_requisition,
)
from .forms_base import get_default_shift
from .forms_finance import InvoiceBillableItemFormSet, SupplierProformaInvoiceItemFormSet
from .forms_hr import DocumentForm
from .forms_operations import ContractDeliverableFormSet
from .hr import document_register_context, notify_disciplinary_employee
from .models import Asset, Attendance, Deployment, Document, Employee, Invoice, ProcurementRequisition, Site, SupplierProformaInvoice
from .operations import load_site_scheduled_guards, rotating_available_guards, site_roster_staff
from .views import (
    asset_stock_groups,
    build_create_formset,
    build_rows,
    csv_import_fields,
    get_config,
    get_field_label,
    get_field_value,
    managed_table_message,
    prepare_csv_form_data,
    render_page,
)


def asset_assignment_type_map():
    return {
        str(asset.pk): asset.asset_type
        for asset in Asset.objects.only("pk", "asset_type")
    }

def model_list(request, model_name):
    if model_name == "payroll":
        require_model_access(request, model_name)
        return payroll_dashboard(request)
    if model_name == "procurement":
        require_model_access(request, model_name)
        return procurement_dashboard(request)

    config = get_config(model_name)
    require_model_access(request, model_name)


    if model_name == "documents":
        return render_page(request, "webCom/document_list.html", document_register_context(), model_name)
    if model_name == "attendance":
        selected_site_id = request.POST.get("site") or request.GET.get("site")
        mark_date = request.POST.get("mark_date") or request.GET.get("mark_date", "")
        selected_site = None
        roster_rows = []
        attendance_hint = ""
        mark_day = None

        sites = Site.objects.select_related("contract", "client").order_by("site_name")
        guard_choices = Employee.objects.none()
        absence_reasons = [
            ("", "-"),
            ("Sick", "Sick"),
            ("Leave", "Leave"),
            ("Off duty", "Off duty"),
            ("No show", "No show"),
            ("Suspended", "Suspended"),
            ("Replaced", "Replaced"),
            ("Other", "Other"),
        ]
        if selected_site_id:
            selected_site = get_object_or_404(sites, pk=selected_site_id)

        if mark_date:
            try:
                mark_day = datetime.strptime(mark_date, "%Y-%m-%d").date()
            except ValueError:
                messages.error(request, "Choose a valid attendance date.")

        if request.method == "POST" and selected_site and mark_day:
            row_keys = request.POST.getlist("row_key")
            saved_count = 0
            site_guard_ids = {employee.pk for employee in site_roster_staff(selected_site, mark_day)}
            for row_key in row_keys:
                deployment_id = request.POST.get(f"deployment_id_{row_key}")
                scheduled_guard_id = request.POST.get(f"scheduled_guard_id_{row_key}")
                shift_type = request.POST.get(f"shift_type_{row_key}")
                if not scheduled_guard_id or not scheduled_guard_id.isdigit() or int(scheduled_guard_id) not in site_guard_ids:
                    continue
                deployment = Deployment.objects.select_related("shift", "site").filter(
                    pk=deployment_id,
                    site=selected_site,
                    status="active",
                    start_date__lte=mark_day,
                ).filter(Q(end_date__isnull=True) | Q(end_date__gte=mark_day)).first()
                if not deployment or shift_type not in deployment.covered_shift_types:
                    continue
                shift = get_default_shift(shift_type)

                scheduled_guard = guard_choices.filter(pk=scheduled_guard_id).first()
                if scheduled_guard is None:
                    continue
                present = request.POST.get(f"present_{row_key}") == "on"
                attended_guard_id = request.POST.get(f"attended_guard_{row_key}") or None
                attended_guard = guard_choices.filter(pk=attended_guard_id).first() if attended_guard_id and attended_guard_id.isdigit() and int(attended_guard_id) in site_guard_ids else None
                if present and attended_guard is None:
                    attended_guard = scheduled_guard
                employee = attended_guard or scheduled_guard

                try:
                    Attendance.objects.update_or_create(
                        deployment=deployment,
                        date=mark_day,
                        shift=shift,
                        scheduled_guard=scheduled_guard,
                        defaults={
                            "site": selected_site,
                            "attended_guard": attended_guard,
                            "present": present,
                            "reason": request.POST.get(f"reason_{row_key}", ""),
                            "employee": employee,
                            "time_in": shift.start_time,
                            "time_out": shift.end_time,
                        },
                    )
                except ValidationError as exc:
                    messages.error(request, "; ".join(exc.messages))
                    continue
                saved_count += 1
            sync_salary_table()
            if saved_count:
                messages.success(request, f"{saved_count} attendance record(s) saved.")
            return redirect(f"{request.path}?site={selected_site.pk}&mark_date={mark_date}")

        if selected_site and mark_day:
            deployments = (
                Deployment.objects.select_related("shift", "site")
                .filter(site=selected_site, status="active", start_date__lte=mark_day)
                .filter(Q(end_date__isnull=True) | Q(end_date__gte=mark_day))
                .order_by("shift__start_time", "shift__shift_type")
            )
            if deployments.exists():
                required_slots = 0
                for deployment in deployments:
                    for shift_type in deployment.covered_shift_types:
                        required_slots += selected_site.day_shift_guards if shift_type == "day" else selected_site.night_shift_guards
                added_count = load_site_scheduled_guards(selected_site, required_slots, mark_day)
                if added_count:
                    selected_site.refresh_from_db()
                    messages.info(request, f"{added_count} eligible guard(s) were assigned to this site roster automatically.")

            site_guards = site_roster_staff(selected_site, mark_day)
            guard_choices = Employee.objects.filter(pk__in=[employee.pk for employee in site_guards]).order_by("first_name", "last_name")
            if not deployments.exists():
                attendance_hint = "No active deployment covers this site on the selected date. Create a deployment for this site, shift, and date range."
            elif not site_guards:
                attendance_hint = "No eligible guards are available for this site/date. Add active guard deployment areas for this site's region or assign guards directly to the site."
            attendance_records = {
                (attendance.deployment_id, attendance.scheduled_guard_id, attendance.shift.shift_type if attendance.shift_id else None): attendance
                for attendance in Attendance.objects.select_related("attended_guard", "scheduled_guard", "shift")
                .filter(site=selected_site, date=mark_day, deployment__in=deployments)
            }

            shortage_messages = []
            scheduled_guard_ids = set()
            for deployment in deployments:
                shift_types = tuple(deployment.covered_shift_types)
                required_by_shift = {
                    shift_type: selected_site.day_shift_guards if shift_type == "day" else selected_site.night_shift_guards
                    for shift_type in shift_types
                }
                total_required = sum(required_by_shift.values())
                deployment_guards = rotating_available_guards(
                    site_guards,
                    total_required,
                    mark_day,
                    deployment.start_date,
                    scheduled_guard_ids,
                )
                if len(deployment_guards) < total_required:
                    shortage_messages.append(
                        f"{deployment.site} has {len(deployment_guards)} available unique guard(s) for {total_required} required shift slot(s) on {mark_day}. Assign more guards directly to this site."
                    )
                guard_offset = 0

                for shift_type in shift_types:
                    shift = get_default_shift(shift_type)
                    required_guards = required_by_shift[shift_type]
                    scheduled_guards = deployment_guards[guard_offset : guard_offset + required_guards]
                    guard_offset += required_guards
                    for scheduled_guard in scheduled_guards:
                        scheduled_guard_ids.add(scheduled_guard.pk)
                        attendance = attendance_records.get((deployment.pk, scheduled_guard.pk, shift_type))
                        if attendance and attendance.attended_guard_id:
                            attended_guard = attendance.attended_guard
                        elif attendance and not attendance.present:
                            attended_guard = None
                        else:
                            attended_guard = scheduled_guard
                        row_key = f"{deployment.pk}_{shift_type}_{scheduled_guard.pk}"
                        roster_rows.append(
                            {
                                "row_key": row_key,
                                "deployment": deployment,
                                "scheduled_guard": scheduled_guard,
                                "scheduled_guard_id": scheduled_guard.pk,
                                "shift_date": mark_day,
                                "shift_type": shift.get_shift_type_display(),
                                "shift_type_value": shift_type,
                                "present": bool(attendance.present) if attendance else False,
                                "attended_guard_id": attended_guard.pk if attended_guard else "",
                                "reason": attendance.reason if attendance and attendance.reason else "",
                            }
                        )
            if shortage_messages:
                attendance_hint = " ".join([message for message in [attendance_hint, *shortage_messages] if message])
        context = {
            "model_name": model_name,
            "title": "Roster Attendances",
            "sites": sites,
            "guard_choices": guard_choices,
            "absence_reasons": absence_reasons,
            "selected_site": selected_site,
            "selected_site_id": selected_site_id,
            "mark_date": mark_date,
            "roster_rows": roster_rows,
            "attendance_hint": attendance_hint,
        }
        return render_page(request, "webCom/attendance_roster.html", context, model_name)

    if model_name == "salaries":
        sync_salary_table()

    if model_name == "budgets":
        refresh_budget_audit_notifications()

    if model_name == "expenses":
        refresh_expense_accountability_notifications()

    if model_name == "paymees":
        sync_receivables_from_payments()

    objects = config["model"].objects.all().order_by(config["model"]._meta.pk.name)
    if model_name == "assets":
        objects = asset_stock_groups(objects.prefetch_related("assignments"))
        for asset_group in objects:
            if asset_group.is_low_stock:
                messages.warning(request, asset_group.low_stock_message)
    context = {
        "model_name": model_name,
        "title": config["title"],
        "fields": config["fields"],
        "field_labels": [get_field_label(config["model"], field) for field in config["fields"]],
        "rows": build_rows(objects, config["fields"]),
        "managed_table": config.get("managed_table", False),
    }
    return render_page(request, "webCom/model_list.html", context, model_name)


def model_export(request, model_name):
    config = get_config(model_name)
    require_model_access(request, model_name)
    if model_name == "paymees":
        sync_receivables_from_payments()

    model = config["model"]
    fields = config.get("fields", [])
    timestamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{model_name}-{timestamp}.csv"'

    writer = csv.writer(response)
    writer.writerow(fields)
    ordering = config.get("ordering") or [model._meta.pk.name]
    objects = model.objects.all().order_by(*ordering)
    if model_name == "assets":
        objects = asset_stock_groups(objects.prefetch_related("assignments"))
    for obj in objects:
        writer.writerow([get_field_value(obj, field) for field in fields])
    return response


def model_import(request, model_name):
    config = get_config(model_name)
    require_model_access(request, model_name)
    if config.get("managed_table"):
        messages.info(request, f"{managed_table_message(config)} It cannot be imported.")
        return redirect("webcom:list", model_name=model_name)

    import_fields = csv_import_fields(config)
    errors = []
    saved_count = 0

    if request.method == "POST":
        uploaded_file = request.FILES.get("import_file")
        if not uploaded_file:
            errors.append("Choose a CSV file to import.")
        elif not uploaded_file.name.lower().endswith(".csv"):
            errors.append("Import file must be a .csv file.")
        else:
            decoded_file = uploaded_file.read().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(decoded_file, newline=""))
            missing_fields = [field for field in import_fields if field not in (reader.fieldnames or [])]
            if missing_fields:
                errors.append("Missing CSV column(s): " + ", ".join(missing_fields))
            else:
                with transaction.atomic():
                    for line_number, raw_row in enumerate(reader, start=2):
                        row = normalize_csv_row(raw_row)
                        if not any(row.get(field) for field in import_fields):
                            continue
                        form_data = prepare_csv_form_data(config["form"], config, row)
                        form = config["form"](form_data, required_only=config.get("required_only", True))
                        if form.is_valid():
                            form.save()
                            saved_count += 1
                        else:
                            for field_name, field_errors in form.errors.items():
                                label = field_name if field_name != "__all__" else "row"
                                errors.append(f"Line {line_number} {label}: {'; '.join(field_errors)}")
                    if errors:
                        transaction.set_rollback(True)
                if saved_count and not errors:
                    messages.success(request, f"{saved_count} {config['title']} record(s) imported successfully.")
                    return redirect("webcom:list", model_name=model_name)

    context = {
        "model_name": model_name,
        "title": f"Import {config['title']}",
        "import_fields": import_fields,
        "errors": errors,
    }
    return render_page(request, "webCom/model_import.html", context, model_name)


def model_detail(request, model_name, pk):
    config = get_config(model_name)
    require_model_access(request, model_name)
    if model_name == "paymees":
        sync_receivables_from_payments()

    obj = get_object_or_404(config["model"], pk=pk)
    fields = config.get("detail_fields", [field.name for field in config["model"]._meta.fields])
    context = {
        "model_name": model_name,
        "title": config["title"],
        "object": obj,
        "pk": obj.pk,
        "details": [(get_field_label(config["model"], field), get_field_value(obj, field)) for field in fields],
        "managed_table": config.get("managed_table", False),
    }
    if model_name == "incidents":
        context["notifications"] = obj.notifications.select_related("recipient").order_by("-notified_at")
        return render_page(request, "webCom/incident_detail.html", context, model_name)
    return render_page(request, "webCom/model_detail.html", context, model_name)


def model_create(request, model_name):
    config = get_config(model_name)
    require_model_access(request, model_name)
    if config.get("managed_table"):
        messages.info(request, managed_table_message(config))
        return redirect("webcom:list", model_name=model_name)
    if model_name == "leaves":
        form = config["form"](request.POST or None, request.FILES or None, required_only=False)
        if request.method == "POST" and form.is_valid():
            leave = form.save(commit=False)
            leave.application_status = "draft" if request.POST.get("action") == "draft" else "submitted"
            if leave.application_status == "draft":
                leave.operations_verification_status = "pending"
                leave.approval_status = "pending"
            leave.save()
            if leave.application_status == "submitted":
                leave.notify_submission()
            messages.success(request, "Leave request saved as draft." if leave.application_status == "draft" else "Leave request submitted successfully.")
            return redirect("webcom:detail", model_name=model_name, pk=leave.pk)
        context = {
            "model_name": model_name,
            "title": "Apply for Leave",
            "form": form,
            "submit_label": "Submit",
        }
        return render_page(request, "webCom/leave_form.html", context, model_name)
    if model_name == "assets":
        form = config["form"](request.POST or None, request.FILES or None, required_only=False)
        if request.method == "POST" and form.is_valid():
            asset = form.save()
            messages.success(request, "Asset record created successfully.")
            return redirect("webcom:detail", model_name=model_name, pk=asset.pk)
        context = {
            "model_name": model_name,
            "title": "Add Asset",
            "form": form,
            "submit_label": "Save Asset",
        }
        return render_page(request, "webCom/asset_form.html", context, model_name)
    if model_name == "invoices":
        invoice_instance = Invoice()
        form = config["form"](request.POST or None, request.FILES or None, instance=invoice_instance, required_only=config.get("required_only", True))
        item_post_data = invoice_item_post_data(request)
        has_invoice_items = request.method != "POST" or item_post_data is not None
        invoice_item_formset = InvoiceBillableItemFormSet(item_post_data if has_invoice_items else None, instance=invoice_instance, prefix="items")
        invoice_items_are_valid = not has_invoice_items or invoice_item_formset.is_valid()
        if request.method == "POST" and form.is_valid() and invoice_items_are_valid:
            invoice_mode = form.cleaned_data.get("invoice_mode", "consolidated")
            if invoice_mode == "split_sites":
                contract = form.cleaned_data["contract"]
                if not contract.sites.exists():
                    form.add_error("sites", "The selected contract has no sites to invoice separately.")
                else:
                    with transaction.atomic():
                        invoices = create_split_site_invoices(form)
                    if has_invoice_items and invoice_item_formset_has_rows(invoice_item_formset):
                        messages.info(request, "Provisional items are only added in the consolidated invoice option, so they were not duplicated across site invoices.")
                    messages.success(request, f"{len(invoices)} site invoice(s) created with separate invoice numbers.")
                    return redirect("webcom:list", model_name=model_name)
            else:
                invoice = form.save()
                if has_invoice_items:
                    invoice_item_formset.instance = invoice
                    invoice_item_formset.save()
                invoice.save(update_fields=invoice_update_fields())
                messages.success(request, "Invoice created successfully. Guarding services and provisional items are included on one invoice.")
                return redirect("webcom:detail", model_name=model_name, pk=invoice.pk)
        context = {
            "model_name": model_name,
            "title": "Add Invoice",
            "form": form,
            "invoice_form_layout": True,
            "invoice_item_formset": invoice_item_formset,
            "invoice_item_prices_json": invoice_item_price_payload(),
            "submit_label": "Save Invoice",
        }
        return render_page(request, "webCom/model_form.html", context, model_name)

    if model_name == "supplier-proformas":
        proforma_instance = SupplierProformaInvoice()
        form = config["form"](request.POST or None, request.FILES or None, instance=proforma_instance, required_only=config.get("required_only", True))
        item_formset = SupplierProformaInvoiceItemFormSet(request.POST or None, instance=proforma_instance, prefix="proforma_items")
        if request.method == "POST":
            form_is_valid = form.is_valid()
            item_formset_is_valid = item_formset.is_valid()
            if form_is_valid and item_formset_is_valid and not proforma_item_formset_has_rows(item_formset):
                item_formset._non_form_errors = item_formset.error_class(["Add at least one proforma item line."])
                item_formset_is_valid = False
            if form_is_valid and item_formset_is_valid:
                form_is_valid = validate_proforma_total_against_requisition(form, item_formset)
            if form_is_valid and item_formset_is_valid:
                try:
                    with transaction.atomic():
                        proforma = form.save()
                        item_formset.instance = proforma
                        item_formset.save()
                        proforma.save(update_fields=["subtotal_amount", "tax_amount", "total_amount", "status", "purchase_order", "updated_at"])
                except ValidationError as exc:
                    form.add_error(None, exc)
                else:
                    messages.success(request, "Supplier proforma saved. Subtotal, tax, and total were calculated from item lines.")
                    return redirect("webcom:detail", model_name=model_name, pk=proforma.pk)
        context = {
            "model_name": model_name,
            "title": "Add Supplier Proforma",
            "form": form,
            "proforma_item_formset": item_formset,
            "proforma_item_prices_json": proforma_item_price_payload(),
            "submit_label": "Save Proforma",
        }
        return render_page(request, "webCom/model_form.html", context, model_name)
    if model_name == "deployments":
        form = config["form"](request.POST or None, request.FILES or None, required_only=False)
        if request.method == "POST" and form.is_valid():
            deployment = form.save()
            messages.success(request, "Deployment saved successfully.")
            return redirect("webcom:detail", model_name=model_name, pk=deployment.pk)
        context = {
            "model_name": model_name,
            "title": "Add Deployment",
            "form": form,
            "submit_label": "Save Deployment",
        }
        return render_page(request, "webCom/model_form.html", context, model_name)
    if model_name == "performance-evaluations":
        form = config["form"](request.POST or None, request.FILES or None, required_only=False)
        if request.method == "POST" and form.is_valid():
            evaluation = form.save()
            messages.success(request, "Performance evaluation saved successfully.")
            return redirect("webcom:detail", model_name=model_name, pk=evaluation.pk)
        context = {
            "model_name": model_name,
            "title": "Add Performance Evaluation",
            "form": form,
            "performance_evaluation_layout": True,
            "submit_label": "Save Evaluation",
        }
        return render_page(request, "webCom/model_form.html", context, model_name)
    if model_name == "documents":
        DocumentFormSet = modelformset_factory(
            Document,
            form=DocumentForm,
            fields=("doc_type", "file_path", "expiry_date"),
            extra=3,
            can_delete=False,
        )
        employee_id = request.POST.get("employee") or request.GET.get("employee", "")
        selected_employee = Employee.objects.filter(pk=employee_id).first() if employee_id else None
        entry_formset = DocumentFormSet(
            request.POST or None,
            request.FILES or None,
            queryset=Document.objects.none(),
            prefix="records",
        )
        if request.method == "POST":
            if not selected_employee:
                messages.error(request, "Choose the employee these documents belong to.")
            elif entry_formset.is_valid():
                documents = entry_formset.save(commit=False)
                documents = [document for document in documents if document.doc_type or document.file_path or document.expiry_date]
                if documents:
                    for document in documents:
                        document.employee = selected_employee
                        document.save()
                    messages.success(request, f"{len(documents)} document(s) saved for {selected_employee}.")
                    return redirect("webcom:list", model_name=model_name)
                messages.warning(request, "Add at least one document before saving.")
        context = {
            "model_name": model_name,
            "title": "Add Employee Documents",
            "entry_formset": entry_formset,
            "employees": Employee.objects.order_by("first_name", "last_name", "employee_number"),
            "selected_employee": selected_employee,
            "submit_label": "Save Documents",
        }
        return render_page(request, "webCom/document_upload.html", context, model_name)
    CreateFormSet = build_create_formset(config)
    initial_rows = []
    selected_approval_requisition = None
    if model_name == "procurement-approvals" and request.method == "GET":
        requisition_id = request.GET.get("requisition", "")
        if requisition_id:
            selected_approval_requisition = ProcurementRequisition.objects.filter(pk=requisition_id, status="submitted").select_related("requested_by", "preferred_supplier").first()
            if selected_approval_requisition:
                initial_rows = [{
                    "requisition": selected_approval_requisition,
                    "decision": "approved",
                    "approved_amount": selected_approval_requisition.estimated_amount,
                }]
    entry_formset = CreateFormSet(
        request.POST or None,
        request.FILES or None,
        queryset=config["model"].objects.none(),
        prefix="records",
        initial=initial_rows,
    )
    if request.method == "POST" and entry_formset.is_valid():
        objects = entry_formset.save()
        if model_name == "disciplinary-actions":
            for obj in objects:
                notify_disciplinary_employee(obj)
        if objects:
            messages.success(request, f"{len(objects)} {config['title']} record(s) created successfully.")
            if len(objects) == 1:
                return redirect("webcom:detail", model_name=model_name, pk=objects[0].pk)
            return redirect("webcom:list", model_name=model_name)
        messages.warning(request, "Enter at least one row before saving.")

    context = {
        "model_name": model_name,
        "title": f"Add {config['title']}",
        "entry_formset": entry_formset,
        "entry_field_labels": [field.label for field in entry_formset.empty_form.visible_fields()],
        "submit_label": "Save Records",
        "selected_approval_requisition": selected_approval_requisition,
    }
    if model_name == "asset-assignments":
        context["asset_assignment_asset_types"] = asset_assignment_type_map()
    return render_page(request, "webCom/model_form.html", context, model_name)
def model_update(request, model_name, pk):
    config = get_config(model_name)
    require_model_access(request, model_name)
    if config.get("managed_table"):
        messages.info(request, managed_table_message(config))
        return redirect("webcom:list", model_name=model_name)
    obj = get_object_or_404(config["model"], pk=pk)
    if model_name == "assets":
        form = config["form"](request.POST or None, request.FILES or None, instance=obj, required_only=False)
        if request.method == "POST" and form.is_valid():
            asset = form.save()
            messages.success(request, "Asset record updated successfully.")
            return redirect("webcom:detail", model_name=model_name, pk=asset.pk)
        context = {
            "model_name": model_name,
            "title": "Edit Asset",
            "form": form,
            "object": obj,
            "pk": obj.pk,
            "submit_label": "Save Asset",
        }
        return render_page(request, "webCom/asset_form.html", context, model_name)
    if model_name == "leaves":
        form = config["form"](request.POST or None, request.FILES or None, instance=obj, required_only=False)
        if request.method == "POST" and form.is_valid():
            leave = form.save(commit=False)
            leave.application_status = "draft" if request.POST.get("action") == "draft" else "submitted"
            leave.save()
            if leave.application_status == "submitted":
                leave.notify_submission()
            messages.success(request, "Leave draft updated." if leave.application_status == "draft" else "Leave request submitted successfully.")
            return redirect("webcom:detail", model_name=model_name, pk=leave.pk)
        context = {
            "model_name": model_name,
            "title": "Edit Leave Application",
            "form": form,
            "object": obj,
            "pk": obj.pk,
            "submit_label": "Submit",
        }
        return render_page(request, "webCom/leave_form.html", context, model_name)
    form = config["form"](request.POST or None, request.FILES or None, instance=obj, required_only=config.get("required_only", True))
    deliverable_formset = None
    invoice_item_formset = None
    proforma_item_formset = None
    has_invoice_items = False

    if model_name == "contracts":
        deliverable_formset = ContractDeliverableFormSet(
            request.POST or None,
            instance=obj,
            prefix="deliverables",
        )

    if model_name == "invoices":
        item_post_data = invoice_item_post_data(request)
        has_invoice_items = request.method != "POST" or item_post_data is not None
        invoice_item_formset = InvoiceBillableItemFormSet(
            item_post_data if has_invoice_items else None,
            instance=obj,
            prefix="items",
        )

    if model_name == "supplier-proformas":
        proforma_item_formset = SupplierProformaInvoiceItemFormSet(
            request.POST or None,
            instance=obj,
            prefix="proforma_items",
        )

    if request.method == "POST":
        form_is_valid = form.is_valid()
        formset_is_valid = deliverable_formset is None or deliverable_formset.is_valid()
        invoice_items_are_valid = invoice_item_formset is None or not has_invoice_items or invoice_item_formset.is_valid()
        proforma_items_are_valid = proforma_item_formset is None or proforma_item_formset.is_valid()
        if proforma_item_formset is not None and proforma_items_are_valid and not proforma_item_formset_has_rows(proforma_item_formset):
            proforma_item_formset._non_form_errors = proforma_item_formset.error_class(["Add at least one proforma item line."])
            proforma_items_are_valid = False
        if form_is_valid and formset_is_valid and invoice_items_are_valid and proforma_items_are_valid:
            obj = form.save()
            if model_name == "disciplinary-actions":
                notify_disciplinary_employee(obj)
            if deliverable_formset is not None:
                deliverable_formset.instance = obj
                deliverable_formset.save()
                obj.update_contract_value()
            if invoice_item_formset is not None and has_invoice_items:
                invoice_item_formset.instance = obj
                invoice_item_formset.save()
                obj.save(update_fields=invoice_update_fields())
            if proforma_item_formset is not None:
                proforma_item_formset.instance = obj
                proforma_item_formset.save()
                obj.save(update_fields=["subtotal_amount", "tax_amount", "total_amount", "status", "purchase_order", "updated_at"])
            messages.success(request, f"{config['title']} record updated successfully.")
            return redirect("webcom:detail", model_name=model_name, pk=obj.pk)

    context = {
        "model_name": model_name,
        "title": f"Edit {config['title']}",
        "form": form,
        "deliverable_formset": deliverable_formset,
        "invoice_item_formset": invoice_item_formset,
        "proforma_item_formset": proforma_item_formset,
        "proforma_item_prices_json": proforma_item_price_payload() if model_name == "supplier-proformas" else "{}",
        "invoice_item_prices_json": invoice_item_price_payload() if model_name == "invoices" else "{}",
        "object": obj,
        "pk": obj.pk,
        "submit_label": "Save",
        "performance_evaluation_layout": model_name == "performance-evaluations",
        "invoice_form_layout": model_name == "invoices",
    }
    if model_name == "asset-assignments":
        context["asset_assignment_asset_types"] = asset_assignment_type_map()
    return render_page(request, "webCom/model_form.html", context, model_name)

def model_delete(request, model_name, pk):
    config = get_config(model_name)
    require_model_access(request, model_name)
    if config.get("managed_table"):
        messages.info(request, managed_table_message(config))
        return redirect("webcom:list", model_name=model_name)
    obj = get_object_or_404(config["model"], pk=pk)

    if request.method == "POST":
        obj.delete()
        messages.success(request, f"{config['title']} record deleted successfully.")
        return redirect("webcom:list", model_name=model_name)

    context = {"model_name": model_name, "title": f"Delete {config['title']}", "object": obj}
    return render_page(request, "webCom/model_confirm_delete.html", context, model_name)
