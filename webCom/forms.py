from datetime import time

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.auth.password_validation import validate_password
from django.db.models import Q
from django.forms import inlineformset_factory, modelformset_factory
from django.utils import timezone

from .access import role_group_permission_queryset, sync_role_group_permissions

from .models import (
    Advance,
    Asset,
    AssetAssignment,
    Attendance,
    Budget,
    Client,
    Contract,
    ContractDeliverable,
    Deployment,
    DeploymentArea,
    Region,
    Disciplinary_Action,
    Document,
    Employee,
    Expense,
    Guard,
    Incident,
    Invoice,
    InvoiceBillableItem,
    InvoiceBillableItemPrice,
    Leave,
    Patrol_Log,
    Paymee,
    PayrollDeduction,
    Payment,
    Supplier,
    ProcurementRequisition,
    ProcurementApproval,
    SupplierProformaInvoice,
    SupplierProformaItemPrice,
    SupplierProformaInvoiceItem,
    PurchaseOrder,
    GoodsReceivedNote,
    SupplierInvoice,
    SupplierPayment,
    Performance_Evaluation,
    Position,
    Role,
    Salary,
    Shift,
    Site,
    Supervisor,
    site_assignment_conflicts,
    Training,
    WebsiteAdvertisement,
    CompanyEvent,
    WebsiteResource,
    AssociatedLink,
    JobPosting,
    JobApplication,
)


DATE_WIDGET = forms.DateInput(attrs={"type": "date", "class": "form-control"})
TIME_WIDGET = forms.TimeInput(attrs={"type": "time", "class": "form-control"})
DATETIME_WIDGET = forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"})

DEFAULT_SHIFT_TIMES = {
    "day": (time(7, 0), time(18, 0)),
    "night": (time(18, 0), time(7, 0)),
}


def get_default_shift(shift_type):
    start_time, end_time = DEFAULT_SHIFT_TIMES[shift_type]
    shift, _ = Shift.objects.get_or_create(
        start_time=start_time,
        end_time=end_time,
        defaults={"hours_per_shift": 0, "shift_type": shift_type},
    )
    return shift


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        required_only = kwargs.pop("required_only", False)
        super().__init__(*args, **kwargs)
        if required_only:
            for field_name in list(self.fields):
                if not self.fields[field_name].required:
                    self.fields.pop(field_name)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(widget, forms.CheckboxSelectMultiple):
                continue
            else:
                widget.attrs.setdefault("class", "form-control")


class DateRangeValidationMixin:
    start_date_field = "start_date"
    end_date_field = "end_date"

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get(self.start_date_field)
        end_date = cleaned_data.get(self.end_date_field)
        if start_date and end_date and end_date < start_date:
            self.add_error(self.end_date_field, "End date cannot be earlier than start date.")
        return cleaned_data


class ClientForm(StyledModelForm):
    class Meta:
        model = Client
        fields = "__all__"


class ContractForm(DateRangeValidationMixin, StyledModelForm):
    start_date_field = "contract_start_date"
    end_date_field = "contract_end_date"

    class Meta:
        model = Contract
        fields = "__all__"
        widgets = {"contract_start_date": DATE_WIDGET, "contract_end_date": DATE_WIDGET}


class ContractDeliverableForm(StyledModelForm):
    class Meta:
        model = ContractDeliverable
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("item_name", "quantity", "unit_price"):
            if field_name in self.fields:
                self.fields[field_name].required = False

    def has_changed(self):
        if self.instance.pk:
            return super().has_changed()
        if not self.data.get(self.add_prefix("item_name")) and not self.data.get(self.add_prefix("quantity")) and not self.data.get(self.add_prefix("unit_price")):
            return False
        return super().has_changed()

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("DELETE"):
            return cleaned_data
        has_any_value = cleaned_data.get("item_name") or cleaned_data.get("quantity") is not None or cleaned_data.get("unit_price") is not None
        if has_any_value:
            if not cleaned_data.get("item_name"):
                self.add_error("item_name", "Select an item.")
            if cleaned_data.get("quantity") is None:
                self.add_error("quantity", "Enter quantity.")
            if cleaned_data.get("unit_price") is None:
                self.add_error("unit_price", "Enter unit price.")
        return cleaned_data


ContractDeliverableFormSet = inlineformset_factory(
    Contract,
    ContractDeliverable,
    form=ContractDeliverableForm,
    fields=("item_name", "quantity", "unit_price"),
    extra=3,
    can_delete=True,
)


class RegionForm(StyledModelForm):
    class Meta:
        model = Region
        fields = "__all__"


class SiteForm(StyledModelForm):
    class Meta:
        model = Site
        fields = ["region", "client", "contract", "site_name", "site_address", "day_shift_guards", "night_shift_guards", "guards"]
        widgets = {"guards": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client"].required = True
        self.fields["region"].label = "Deployment Area"
        self.fields["region"].required = False
        self.fields["guards"].queryset = self.guard_queryset_for_selected_region()
        self.fields["guards"].required = False

    def selected_region_id(self):
        if self.is_bound:
            return self.data.get(self.add_prefix("region")) or None
        if self.instance and self.instance.pk:
            return self.instance.region_id
        value = self.initial.get("region")
        return getattr(value, "pk", value) or None

    def guard_queryset_for_selected_region(self):
        region_id = self.selected_region_id()
        base_queryset = Employee.objects.filter(role__in=("guard", "supervisor"), status="active")
        if not region_id:
            return base_queryset.none()
        today = timezone.localdate()
        assigned_ids = list(self.instance.guards.values_list("pk", flat=True)) if self.instance and self.instance.pk else []
        return (
            base_queryset.filter(
                Q(deployment_areas__region_id=region_id, deployment_areas__status="active", deployment_areas__start_date__lte=today)
                & (Q(deployment_areas__end_date__isnull=True) | Q(deployment_areas__end_date__gte=today))
                | Q(pk__in=assigned_ids)
            )
            .distinct()
            .order_by("employee_number", "first_name", "last_name")
        )

    @staticmethod
    def guards_outside_deployment_area(guards, region):
        if not region or guards is None:
            return []
        today = timezone.localdate()
        guard_ids = [guard.pk for guard in guards]
        area_guard_ids = set(
            Employee.objects.filter(
                pk__in=guard_ids,
                deployment_areas__region=region,
                deployment_areas__status="active",
                deployment_areas__start_date__lte=today,
            )
            .filter(Q(deployment_areas__end_date__isnull=True) | Q(deployment_areas__end_date__gte=today))
            .values_list("pk", flat=True)
        )
        return [guard for guard in guards if guard.pk not in area_guard_ids]

    def clean(self):
        cleaned_data = super().clean()
        contract = cleaned_data.get("contract")
        client = cleaned_data.get("client")
        day_shift_guards = cleaned_data.get("day_shift_guards") or 0
        night_shift_guards = cleaned_data.get("night_shift_guards") or 0
        guards = cleaned_data.get("guards")

        if contract and client and contract.client_id != client.pk:
            self.add_error("contract", "Selected contract does not belong to the selected client.")

        if not cleaned_data.get("region"):
            site_for_inference = Site(site_name=cleaned_data.get("site_name") or "", site_address=cleaned_data.get("site_address") or "")
            inferred_region = site_for_inference.infer_region_from_location()
            if inferred_region:
                cleaned_data["region"] = inferred_region
            else:
                self.add_error("region", "Choose a deployment area or enter a recognizable site location, for example Kampala Road or Mbarara.")

        if contract:
            other_sites = Site.objects.filter(contract=contract)
            if self.instance.pk:
                other_sites = other_sites.exclude(pk=self.instance.pk)
            if sum(site.day_shift_guards for site in other_sites) + day_shift_guards > contract.day_shift_guards:
                self.add_error("day_shift_guards", "Total site day shift guards cannot exceed the contract day shift guards.")
            if sum(site.night_shift_guards for site in other_sites) + night_shift_guards > contract.night_shift_guards:
                self.add_error("night_shift_guards", "Total site night shift guards cannot exceed the contract night shift guards.")

        if guards is not None:
            required_guards = day_shift_guards + night_shift_guards
            if required_guards and guards.count() < required_guards:
                self.add_error("guards", "Assign at least the total day and night guards required for this site.")
            outside_area_guards = self.guards_outside_deployment_area(guards, cleaned_data.get("region"))
            if outside_area_guards:
                self.add_error("guards", "Assign guards from this site's deployment area only: " + ", ".join(str(guard) for guard in outside_area_guards))
            conflicts = site_assignment_conflicts(guards, site=self.instance)
            if conflicts:
                conflict_messages = [f"{guard} is already assigned to {', '.join(sites)}" for guard, sites in conflicts]
                self.add_error("guards", "Only relievers may be assigned to more than one site. " + "; ".join(conflict_messages))
        return cleaned_data


class ShiftForm(StyledModelForm):
    class Meta:
        model = Shift
        fields = "__all__"
        widgets = {"start_time": TIME_WIDGET, "end_time": TIME_WIDGET}


class AssetForm(StyledModelForm):
    class Meta:
        model = Asset
        fields = "__all__"


class AssetAssignmentForm(StyledModelForm):
    class Meta:
        model = AssetAssignment
        fields = "__all__"
        widgets = {"assigned_date": DATE_WIDGET, "return_date": DATE_WIDGET}


class DeploymentForm(DateRangeValidationMixin, StyledModelForm):
    shift_type = forms.ChoiceField(choices=(("day", "Day"), ("night", "Night"), ("day_night", "Day and Night")), required=False)

    class Meta:
        model = Deployment
        fields = ["client", "site", "shift_type", "start_date", "end_date", "status"]
        widgets = {"start_date": DATE_WIDGET, "end_date": DATE_WIDGET}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["shift_type"].initial = self.instance.shift_coverage

    def clean(self):
        cleaned_data = super().clean()
        site = cleaned_data.get("site")
        client = cleaned_data.get("client")
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date") or start_date
        shift_type = cleaned_data.get("shift_type")

        if site and not client:
            cleaned_data["client"] = site.client
        elif site and client and site.client_id and site.client_id != client.pk:
            self.add_error("client", "Choose the client linked to the selected site.")

        if site and site.contract_id and start_date and end_date:
            contract = site.contract
            if start_date < contract.contract_start_date or end_date > contract.contract_end_date:
                self.add_error(
                    "start_date",
                    f"Deployment dates must be within the site contract period: {contract.contract_start_date} to {contract.contract_end_date}.",
                )
                self.add_error("end_date", "Adjust the end date to fit the selected site's contract period.")

        if shift_type:
            cleaned_data["shift_coverage"] = shift_type
            cleaned_data["shift"] = get_default_shift("night" if shift_type == "night" else "day")
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        shift_type = self.cleaned_data.get("shift_type")
        if shift_type:
            instance.shift_coverage = shift_type
            instance.shift = get_default_shift("night" if shift_type == "night" else "day")
        if commit:
            instance.save()
            self.save_m2m()
        return instance


DeploymentFormSet = modelformset_factory(Deployment, form=DeploymentForm, extra=1, can_delete=False)


class DeploymentAreaForm(DateRangeValidationMixin, StyledModelForm):
    class Meta:
        model = DeploymentArea
        fields = "__all__"
        widgets = {"start_date": DATE_WIDGET, "end_date": DATE_WIDGET}


class EmployeeDeploymentTransferForm(forms.Form):
    from_deployment_area = forms.ModelChoiceField(queryset=DeploymentArea.objects.none(), label="From Deployment Area")
    to_deployment_area = forms.ModelChoiceField(queryset=Region.objects.all().order_by("region_name"), label="To Deployment Area")
    start_date = forms.DateField(label="Transfer Date", widget=DATE_WIDGET)
    end_date = forms.DateField(required=False, widget=DATE_WIDGET)
    transferred_by_hr_manager = forms.ModelChoiceField(queryset=Employee.objects.filter(role__in=("manager", "hr_officer")), required=False)
    transfer_notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2, "class": "form-control"}))

    def __init__(self, *args, employee=None, **kwargs):
        self.employee = employee
        super().__init__(*args, **kwargs)
        if employee:
            today = timezone.localdate()
            self.fields["from_deployment_area"].queryset = employee.deployment_areas.filter(status="active", start_date__lte=today).filter(Q(end_date__isnull=True) | Q(end_date__gte=today))

    def save(self):
        from_area = self.cleaned_data["from_deployment_area"]
        from_area.end_date = self.cleaned_data.get("end_date") or self.cleaned_data["start_date"]
        from_area.status = "transferred"
        from_area.save(update_fields=["end_date", "status", "updated_at"])
        return DeploymentArea.objects.create(
            employee=self.employee,
            region=self.cleaned_data["to_deployment_area"],
            start_date=self.cleaned_data["start_date"],
            status="active",
            transferred_by_hr_manager=self.cleaned_data.get("transferred_by_hr_manager"),
            transfer_notes=self.cleaned_data.get("transfer_notes", ""),
        )


class DutyRosterUploadForm(forms.Form):
    site = forms.ModelChoiceField(queryset=Site.objects.all(), required=False)
    roster_file = forms.FileField()


class DutyRosterExportForm(forms.Form):
    site = forms.ModelChoiceField(queryset=Site.objects.select_related("region", "client").all())
    period_start = forms.DateField(widget=DATE_WIDGET)
    period_end = forms.DateField(widget=DATE_WIDGET)


class RoleForm(StyledModelForm):
    class Meta:
        model = Role
        fields = "__all__"


class PositionForm(StyledModelForm):
    class Meta:
        model = Position
        fields = "__all__"


class EmployeeForm(StyledModelForm):
    deployment_area = forms.ModelChoiceField(queryset=Region.objects.all().order_by("region_name"), required=False, label="Deployment Area")

    class Meta:
        model = Employee
        exclude = ("employee_number", "salary_scale", "armed_status", "authority_level")
        widgets = {"date_of_birth": DATE_WIDGET, "hire_date": DATE_WIDGET}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current_area = self.current_area
        if current_area:
            self.fields["deployment_area"].initial = current_area.region_id

    @property
    def current_area(self):
        if not self.instance or not self.instance.pk:
            return None
        today = timezone.localdate()
        return self.instance.deployment_areas.filter(status="active", start_date__lte=today).filter(Q(end_date__isnull=True) | Q(end_date__gte=today)).order_by("-start_date", "-deployment_area_id").first()

    def clean(self):
        cleaned_data = super().clean()
        role = cleaned_data.get("role")
        deployment_area = cleaned_data.get("deployment_area")
        current_area = self.current_area
        if role in {"guard", "supervisor"} and not deployment_area:
            self.add_error("deployment_area", "Choose a deployment area for this guard or supervisor.")
        if current_area and deployment_area and current_area.region_id != deployment_area.pk:
            self.add_error("deployment_area", "Use the Transfer button to move this employee to another deployment area.")
        return cleaned_data

    def save(self, commit=True):
        employee = super().save(commit=commit)
        deployment_area = self.cleaned_data.get("deployment_area")
        if commit and employee.role in {"guard", "supervisor"} and deployment_area and not self.current_area:
            DeploymentArea.objects.create(employee=employee, region=deployment_area, start_date=employee.hire_date or timezone.localdate(), status="active")
        return employee


class GuardForm(StyledModelForm):
    class Meta:
        model = Guard
        fields = "__all__"


class SupervisorForm(StyledModelForm):
    class Meta:
        model = Supervisor
        fields = "__all__"


class TrainingForm(DateRangeValidationMixin, StyledModelForm):
    class Meta:
        model = Training
        fields = "__all__"
        widgets = {"start_date": DATE_WIDGET, "end_date": DATE_WIDGET}


class AttendanceForm(StyledModelForm):
    class Meta:
        model = Attendance
        fields = "__all__"
        widgets = {"date": DATE_WIDGET, "time_in": TIME_WIDGET, "time_out": TIME_WIDGET}


class LeaveForm(DateRangeValidationMixin, StyledModelForm):
    class Meta:
        model = Leave
        fields = "__all__"
        widgets = {"start_date": DATE_WIDGET, "end_date": DATE_WIDGET}


class LeaveReviewForm(forms.Form):
    operations_manager = forms.ModelChoiceField(queryset=Employee.objects.filter(role__in=("manager", "operations_officer")), required=False)
    operations_status = forms.ChoiceField(choices=(("verified", "Verified"), ("rejected", "Rejected")), required=False)
    operations_feedback = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2, "class": "form-control"}))
    hr_manager = forms.ModelChoiceField(queryset=Employee.objects.filter(role__in=("manager", "hr_officer")), required=False)
    hr_decision = forms.ChoiceField(choices=(("approved", "Approved"), ("rejected", "Rejected")), required=False)
    feedback = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}))


class DisciplinaryActionForm(DateRangeValidationMixin, StyledModelForm):
    start_date_field = "offence_date"
    end_date_field = "concluded_on"

    class Meta:
        model = Disciplinary_Action
        fields = "__all__"
        widgets = {"offence_date": DATE_WIDGET, "action_date": DATE_WIDGET, "hearing_date": DATE_WIDGET, "concluded_on": DATE_WIDGET}

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("outcome") and not cleaned_data.get("steps_taken"):
            self.add_error("steps_taken", "State the disciplinary steps taken before recording an outcome.")
        return cleaned_data


class PerformanceEvaluationForm(DateRangeValidationMixin, StyledModelForm):
    start_date_field = "review_period_start"
    end_date_field = "review_period_end"

    class Meta:
        model = Performance_Evaluation
        fields = "__all__"
        widgets = {"date": DATE_WIDGET, "review_period_start": DATE_WIDGET, "review_period_end": DATE_WIDGET}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "evaluated_by" in self.fields:
            self.fields["evaluated_by"].queryset = Employee.objects.filter(
                Q(role__in=("supervisor", "manager", "operations_officer", "hr_officer", "administrator"))
                | Q(department__in=("admin", "hr"))
            ).exclude(role="guard").order_by("employee_number", "first_name", "last_name")
            self.fields["evaluated_by"].label_from_instance = lambda employee: employee.employee_number_name


class DocumentForm(StyledModelForm):
    class Meta:
        model = Document
        fields = "__all__"
        widgets = {"expiry_date": DATE_WIDGET}


class IncidentForm(StyledModelForm):
    class Meta:
        model = Incident
        fields = "__all__"
        widgets = {"date_time": DATETIME_WIDGET}


class PatrolLogForm(StyledModelForm):
    class Meta:
        model = Patrol_Log
        fields = "__all__"
        widgets = {"patrol_date": DATE_WIDGET}


class SalaryForm(StyledModelForm):
    class Meta:
        model = Salary
        fields = "__all__"
        widgets = {"pay_period": DATE_WIDGET}


class PayrollDeductionForm(StyledModelForm):
    class Meta:
        model = PayrollDeduction
        fields = "__all__"
        widgets = {"start_date": DATE_WIDGET, "end_date": DATE_WIDGET}


class AdvanceForm(StyledModelForm):
    class Meta:
        model = Advance
        fields = "__all__"
        widgets = {"request_date": DATE_WIDGET, "approval_date": DATE_WIDGET}


class ExpenseForm(StyledModelForm):
    class Meta:
        model = Expense
        fields = "__all__"
        widgets = {"expense_date": DATE_WIDGET, "description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "budget" in self.fields:
            self.fields["budget"].queryset = Budget.objects.filter(approval_status="approved").order_by("budget_title")
            self.fields["budget"].label_from_instance = self.format_approved_budget
        for field_name in ("requested_by", "verified_by", "approved_by", "spent_by"):
            if field_name in self.fields:
                self.fields[field_name].queryset = Employee.objects.filter(status="active").order_by("employee_number", "first_name", "last_name")
        if "recorded_at" in self.fields:
            self.fields["recorded_at"].required = False

    @staticmethod
    def format_approved_budget(budget):
        return f"{budget.budget_title} - {budget.allocated_amount:,.2f} - {budget.remaining_amount:,.2f}"


class BudgetForm(StyledModelForm):
    class Meta:
        model = Budget
        fields = "__all__"
        widgets = {"period_start": DATE_WIDGET, "period_end": DATE_WIDGET, "description": forms.Textarea(attrs={"rows": 3})}


class SupplierForm(StyledModelForm):
    class Meta:
        model = Supplier
        fields = "__all__"
        widgets = {"address": forms.Textarea(attrs={"rows": 2}), "due_diligence_notes": forms.Textarea(attrs={"rows": 3})}


class ProcurementRequisitionForm(StyledModelForm):
    class Meta:
        model = ProcurementRequisition
        fields = "__all__"
        widgets = {"required_date": DATE_WIDGET, "description": forms.Textarea(attrs={"rows": 3}), "justification": forms.Textarea(attrs={"rows": 3})}


class ProcurementApprovalForm(StyledModelForm):
    class Meta:
        model = ProcurementApproval
        fields = "__all__"
        widgets = {"decision_date": DATE_WIDGET, "comments": forms.Textarea(attrs={"rows": 3})}


class SupplierProformaItemPriceForm(StyledModelForm):
    class Meta:
        model = SupplierProformaItemPrice
        fields = "__all__"


class SupplierProformaInvoiceForm(StyledModelForm):
    class Meta:
        model = SupplierProformaInvoice
        fields = ["requisition", "supplier", "proforma_date", "valid_until", "supplier_reference", "status"]
        widgets = {"proforma_date": DATE_WIDGET, "valid_until": DATE_WIDGET}


class SupplierProformaInvoiceItemForm(StyledModelForm):
    class Meta:
        model = SupplierProformaInvoiceItem
        fields = ("catalog_item", "quantity", "unit_price", "discount_amount", "tax_rate")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["catalog_item"].queryset = SupplierProformaItemPrice.objects.filter(active=True).order_by("item_name")
        for field_name in ("unit_price", "discount_amount", "tax_rate"):
            if field_name in self.fields:
                self.fields[field_name].required = False


SupplierProformaInvoiceItemFormSet = inlineformset_factory(
    SupplierProformaInvoice,
    SupplierProformaInvoiceItem,
    form=SupplierProformaInvoiceItemForm,
    extra=1,
    can_delete=True,
)


class InvoiceBillableItemForm(StyledModelForm):
    class Meta:
        model = InvoiceBillableItem
        fields = ("item_name", "quantity", "unit_price", "taxable")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit_price"].required = False
        self.fields["unit_price"].widget.attrs["readonly"] = True
        self.fields["taxable"].required = False

    def clean(self):
        cleaned_data = super().clean()
        item_name = cleaned_data.get("item_name")
        if item_name:
            price_config = InvoiceBillableItemPrice.active_price_for(item_name)
            if not price_config:
                self.add_error("item_name", "Set an active unit price for this provisional billable item before invoicing it.")
            else:
                cleaned_data["unit_price"] = price_config.unit_price
                cleaned_data["taxable"] = price_config.taxable
        return cleaned_data


InvoiceBillableItemFormSet = inlineformset_factory(
    Invoice,
    InvoiceBillableItem,
    form=InvoiceBillableItemForm,
    extra=1,
    can_delete=True,
)


class InvoiceBillableItemPriceForm(StyledModelForm):
    class Meta:
        model = InvoiceBillableItemPrice
        fields = "__all__"


class InvoiceForm(DateRangeValidationMixin, StyledModelForm):
    INVOICE_MODE_CHOICES = (("consolidated", "One invoice"), ("split_sites", "Separate invoice per site"))
    invoice_mode = forms.ChoiceField(choices=INVOICE_MODE_CHOICES, initial="consolidated", required=False, label="Invoice Option")

    class Meta:
        model = Invoice
        fields = ["contract", "sites", "billable_products", "invoice_date", "due_date", "billing_start_date", "billing_end_date", "amendment_amount", "amendment_reason", "tax_rate", "status"]
        widgets = {"invoice_date": DATE_WIDGET, "due_date": DATE_WIDGET, "billing_start_date": DATE_WIDGET, "billing_end_date": DATE_WIDGET, "sites": forms.CheckboxSelectMultiple, "billable_products": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "tax_rate" in self.fields:
            self.fields["tax_rate"].required = False
        contract = self.contract_from_data_or_instance()
        if contract:
            if "sites" in self.fields:
                self.fields["sites"].queryset = contract.sites.all().order_by("site_name")
            if "billable_products" in self.fields:
                self.fields["billable_products"].queryset = contract.deliverables.all().order_by("item_name")

    def contract_from_data_or_instance(self):
        contract_id = self.data.get(self.add_prefix("contract")) if self.is_bound else None
        if contract_id:
            return Contract.objects.filter(pk=contract_id).first()
        if self.instance and self.instance.contract_id:
            return self.instance.contract
        return None

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("tax_rate") is None:
            cleaned_data["tax_rate"] = 18
        contract = cleaned_data.get("contract")
        sites = cleaned_data.get("sites")
        products = cleaned_data.get("billable_products")
        if contract and sites is not None:
            outside_sites = sites.exclude(contract=contract)
            if outside_sites.exists():
                self.add_error("sites", "Select only sites under the selected contract.")
        if contract and products is not None:
            outside_products = products.exclude(contract=contract)
            if outside_products.exists():
                self.add_error("billable_products", "Select only billable products under the selected contract.")
        return cleaned_data


class PaymeeForm(StyledModelForm):
    class Meta:
        model = Paymee
        fields = "__all__"


class PaymentForm(StyledModelForm):
    class Meta:
        model = Payment
        fields = "__all__"
        widgets = {"payment_date": DATE_WIDGET}


class PurchaseOrderForm(StyledModelForm):
    class Meta:
        model = PurchaseOrder
        fields = "__all__"
        widgets = {"order_date": DATE_WIDGET, "expected_delivery_date": DATE_WIDGET, "notes": forms.Textarea(attrs={"rows": 3})}


class GoodsReceivedNoteForm(StyledModelForm):
    class Meta:
        model = GoodsReceivedNote
        fields = "__all__"
        widgets = {"received_date": DATE_WIDGET, "condition_notes": forms.Textarea(attrs={"rows": 3})}


class SupplierInvoiceForm(StyledModelForm):
    class Meta:
        model = SupplierInvoice
        fields = "__all__"
        widgets = {"invoice_date": DATE_WIDGET, "due_date": DATE_WIDGET, "notes": forms.Textarea(attrs={"rows": 3})}


class SupplierPaymentForm(StyledModelForm):
    class Meta:
        model = SupplierPayment
        fields = "__all__"
        widgets = {"payment_date": DATE_WIDGET, "remarks": forms.Textarea(attrs={"rows": 3})}


class WebsiteAdvertisementForm(StyledModelForm):
    class Meta:
        model = WebsiteAdvertisement
        fields = "__all__"


class CompanyEventForm(StyledModelForm):
    class Meta:
        model = CompanyEvent
        fields = "__all__"
        widgets = {"event_date": DATE_WIDGET}


class WebsiteResourceForm(StyledModelForm):
    class Meta:
        model = WebsiteResource
        fields = "__all__"


class AssociatedLinkForm(StyledModelForm):
    class Meta:
        model = AssociatedLink
        fields = "__all__"


class JobPostingForm(StyledModelForm):
    class Meta:
        model = JobPosting
        fields = "__all__"
        widgets = {"deadline": DATE_WIDGET}


class PublicJobApplicationForm(StyledModelForm):
    class Meta:
        model = JobApplication
        fields = ["applicant_name", "phone_number", "email", "address", "qualification", "experience_summary", "cover_note", "resume"]
        widgets = {"experience_summary": forms.Textarea(attrs={"rows": 4}), "cover_note": forms.Textarea(attrs={"rows": 4})}


class JobApplicationForm(StyledModelForm):
    class Meta:
        model = JobApplication
        fields = "__all__"
        widgets = {"experience_summary": forms.Textarea(attrs={"rows": 3}), "cover_note": forms.Textarea(attrs={"rows": 3})}


# Dashboard-entered applications use the model default timestamp.
def _job_application_form_init(self, *args, **kwargs):
    super(JobApplicationForm, self).__init__(*args, **kwargs)
    if "submitted_at" in self.fields:
        self.fields["submitted_at"].required = False

JobApplicationForm.__init__ = _job_application_form_init


class PasswordResetManagementForm(forms.Form):
    new_password = forms.CharField(
        label="New password",
        min_length=8,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )
    confirm_password = forms.CharField(
        label="Confirm password",
        min_length=8,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_new_password(self):
        password = self.cleaned_data["new_password"]
        validate_password(password, self.user)
        return password

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get("new_password")
        confirm_password = cleaned_data.get("confirm_password")
        if new_password and confirm_password and new_password != confirm_password:
            raise forms.ValidationError("The two password fields did not match.")
        return cleaned_data




class PasswordExpiredChangeForm(forms.Form):
    current_password = forms.CharField(
        label="Current password",
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "current-password"}),
    )
    new_password = forms.CharField(
        label="New password",
        min_length=8,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )
    confirm_password = forms.CharField(
        label="Confirm password",
        min_length=8,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        password = self.cleaned_data["current_password"]
        if self.user and not self.user.check_password(password):
            raise forms.ValidationError("Current password is not correct.")
        return password

    def clean_new_password(self):
        password = self.cleaned_data["new_password"]
        validate_password(password, self.user)
        return password

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get("new_password")
        confirm_password = cleaned_data.get("confirm_password")
        if new_password and confirm_password and new_password != confirm_password:
            raise forms.ValidationError("The two password fields did not match.")
        return cleaned_data


class ForgotPasswordResetForm(forms.Form):
    username = forms.CharField(label="Username", max_length=150)
    email = forms.EmailField(label="Account email")
    new_password = forms.CharField(
        label="New password",
        min_length=8,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )
    confirm_password = forms.CharField(
        label="Confirm password",
        min_length=8,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_new_password(self):
        password = self.cleaned_data["new_password"]
        validate_password(password, self.user)
        return password

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get("new_password")
        confirm_password = cleaned_data.get("confirm_password")
        if new_password and confirm_password and new_password != confirm_password:
            raise forms.ValidationError("The two password fields did not match.")
        return cleaned_data




class AdminUserCreationForm(forms.Form):
    employee = forms.ModelChoiceField(
        label="Employee",
        queryset=Employee.objects.filter(status="active").filter(Q(employee_number__startswith="ADMIN") | Q(employee_number__startswith="ADM") | Q(employee_number__startswith="SUP")).order_by("employee_number", "first_name", "last_name"),
        empty_label="Select employee",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    groups = forms.ModelChoiceField(
        label="Role group",
        queryset=Group.objects.all().order_by("name"),
        required=False,
        empty_label="Select a role group",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    permissions = forms.ModelMultipleChoiceField(
        label="Additional permissions",
        queryset=Permission.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-control permission-select", "size": "5"}),
    )
    is_staff = forms.BooleanField(label="Staff login", required=False, initial=True)
    is_superuser = forms.BooleanField(label="Administrator", required=False)
    is_active = forms.BooleanField(label="Active account", required=False, initial=True)
    temporary_password = forms.CharField(
        label="Temporary password",
        min_length=8,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )
    confirm_password = forms.CharField(
        label="Confirm password",
        min_length=8,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_model = get_user_model()
        self.fields["employee"].label_from_instance = self.employee_label
        self.fields["permissions"].label_from_instance = self.permission_label
        group = self.selected_group()
        if group:
            self.fields["permissions"].queryset = role_group_permission_queryset(group).select_related("content_type").order_by("content_type__model", "codename")

    def selected_group(self):
        group_id = None
        if self.is_bound:
            group_id = self.data.get(self.add_prefix("groups"))
        else:
            group_id = self.initial.get("groups")
        if not group_id:
            return None
        return self.fields["groups"].queryset.filter(pk=group_id).first()

    @staticmethod
    def employee_label(employee):
        name = f"{employee.first_name} {employee.last_name}".strip()
        number = employee.employee_number or f"Employee #{employee.pk}"
        department = employee.get_department_display() if hasattr(employee, "get_department_display") else employee.department
        return f"{number} - {name} ({department})"

    @staticmethod
    def permission_label(permission):
        return permission.name

    def clean_employee(self):
        employee = self.cleaned_data["employee"]
        username = (employee.employee_number or "").strip()
        if not username:
            raise forms.ValidationError("Selected employee does not have an employee number yet.")
        if self.user_model.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("A system user already exists for this employee number.")
        email = (employee.email or "").strip()
        if email and self.user_model.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A system user already exists for this employee email.")
        return employee

    def clean_temporary_password(self):
        password = self.cleaned_data["temporary_password"]
        validate_password(password)
        return password

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("temporary_password")
        confirm = cleaned_data.get("confirm_password")
        if password and confirm and password != confirm:
            raise forms.ValidationError("The two password fields did not match.")
        if cleaned_data.get("is_superuser"):
            cleaned_data["is_staff"] = True
        return cleaned_data

    def save(self):
        employee = self.cleaned_data["employee"]
        user = self.user_model(
            username=employee.employee_number.strip(),
            first_name=employee.first_name,
            last_name=employee.last_name,
            email=employee.email,
            is_staff=self.cleaned_data.get("is_staff", True),
            is_superuser=self.cleaned_data.get("is_superuser", False),
            is_active=self.cleaned_data.get("is_active", True),
        )
        user.set_password(self.cleaned_data["temporary_password"])
        user.save()
        group = self.cleaned_data.get("groups")
        if group:
            sync_role_group_permissions(group)
            user.groups.set([group])
        else:
            user.groups.clear()
        user.user_permissions.set(self.cleaned_data.get("permissions"))
        return user










