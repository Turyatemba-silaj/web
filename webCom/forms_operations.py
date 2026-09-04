from django import forms
from django.db.models import Q
from django.forms import inlineformset_factory, modelformset_factory
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from .forms_base import DATE_WIDGET, DATETIME_WIDGET, TIME_WIDGET, DateRangeValidationMixin, StyledModelForm, get_default_shift
from .models import (
    Asset,
    AssetAssignment,
    Attendance,
    Client,
    Contract,
    ContractDeliverable,
    Deployment,
    DeploymentArea,
    Employee,
    Incident,
    Patrol_Log,
    Region,
    Shift,
    Site,
    site_assignment_conflicts,
)


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
        selected_region = Region.objects.filter(pk=region_id).first()
        site_for_regions = Site(
            region=selected_region,
            site_name=self.data.get(self.add_prefix("site_name"), "") if self.is_bound else getattr(self.instance, "site_name", ""),
            site_address=self.data.get(self.add_prefix("site_address"), "") if self.is_bound else getattr(self.instance, "site_address", ""),
        )
        region_ids = list(site_for_regions.deployment_area_regions().values_list("pk", flat=True)) or [region_id]
        return (
            base_queryset.filter(
                Q(deployment_areas__region_id__in=region_ids, deployment_areas__status="active", deployment_areas__start_date__lte=today)
                & (Q(deployment_areas__end_date__isnull=True) | Q(deployment_areas__end_date__gte=today))
                | Q(pk__in=assigned_ids)
            )
            .distinct()
            .order_by("employee_number", "first_name", "last_name")
        )

    @staticmethod
    def guards_outside_deployment_area(guards, site):
        if not site or guards is None:
            return []
        today = timezone.localdate()
        guard_ids = [guard.pk for guard in guards]
        region_ids = list(site.deployment_area_regions().values_list("pk", flat=True))
        if not region_ids:
            return list(guards)
        area_guard_ids = set(
            Employee.objects.filter(
                pk__in=guard_ids,
                deployment_areas__region_id__in=region_ids,
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
            site_for_area = Site(
                region=cleaned_data.get("region"),
                site_name=cleaned_data.get("site_name") or "",
                site_address=cleaned_data.get("site_address") or "",
            )
            outside_area_guards = self.guards_outside_deployment_area(guards, site_for_area)
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
    TYPE_FIELD_GROUPS = {
        "vehicle": ("asset_type", "asset_name", "make", "model", "engine_number", "chassis_number", "number_plate", "color", "condition", "storage_location", "quantity", "notes"),
        "equipment": ("asset_type", "asset_name", "make", "model", "asset_number", "condition", "storage_location", "quantity", "notes"),
        "uniform": ("asset_type", "asset_name", "color", "size", "condition", "storage_location", "quantity", "notes"),
        "gun": ("asset_type", "make", "asset_name", "model", "asset_number", "police_number", "condition", "storage_location", "quantity", "notes"),
        "weapon": ("asset_type", "asset_name", "make", "model", "asset_number", "police_number", "condition", "storage_location", "quantity", "notes"),
        "other": ("asset_type", "asset_name", "asset_number", "make", "model", "condition", "storage_location", "quantity", "notes"),
    }

    class Meta:
        model = Asset
        fields = [
            "asset_type",
            "asset_name",
            "make",
            "model",
            "asset_number",
            "engine_number",
            "chassis_number",
            "number_plate",
            "police_number",
            "color",
            "size",
            "condition",
            "storage_location",
            "quantity",
            "notes",
        ]
        labels = {
            "asset_type": "Asset Type",
            "asset_name": "Asset Name",
            "asset_number": "Serial Number / Asset Number",
            "make": "Make",
            "model": "Model",
            "engine_number": "Engine Number",
            "chassis_number": "Chassis Number",
            "number_plate": "Number Plate",
            "police_number": "Police Number",
            "color": "Color",
            "size": "Size",
            "condition": "Condition",
            "storage_location": "Storage Location",
            "quantity": "Quantity In Store",
            "notes": "Asset Notes",
        }
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Condition, storage location, batch details, or other remarks"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["asset_type"].choices = [("", "Select...")] + list(self.fields["asset_type"].choices)
        self.fields["asset_name"].widget.attrs.setdefault("placeholder", "e.g. AK-47, Radio handset, Patrol vehicle, Uniform shirt")
        self.fields["asset_number"].widget.attrs.setdefault("placeholder", "Serial, plate, tag, or batch number")
        self.fields["engine_number"].widget.attrs.setdefault("placeholder", "Vehicle engine number")
        self.fields["chassis_number"].widget.attrs.setdefault("placeholder", "Vehicle chassis number")
        self.fields["number_plate"].widget.attrs.setdefault("placeholder", "Vehicle number plate")
        self.fields["police_number"].widget.attrs.setdefault("placeholder", "Police registration/reference number")
        self.fields["color"].widget.attrs.setdefault("placeholder", "e.g. Navy blue, black, khaki")
        self.fields["size"].widget.attrs.setdefault("placeholder", "e.g. S, M, L, XL, 42, 44")
        self.fields["storage_location"].widget.attrs.setdefault("placeholder", "Store, armoury, branch, or site")
        for field_name in self.fields:
            self.fields[field_name].required = field_name in ("asset_type", "quantity")

    def visible_fields_for_type(self, asset_type):
        return self.TYPE_FIELD_GROUPS.get(asset_type) or tuple(self.fields)

    def clean(self):
        cleaned_data = super().clean()
        asset_type = cleaned_data.get("asset_type")
        required_by_type = {
            "uniform": ("asset_name", "color", "size", "condition"),
            "gun": ("make", "asset_name", "model", "asset_number", "police_number", "condition"),
            "vehicle": ("asset_name", "make", "model", "engine_number", "chassis_number", "number_plate", "condition"),
            "equipment": ("asset_name", "asset_number", "condition"),
            "weapon": ("asset_name", "asset_number", "condition"),
            "other": ("asset_name", "condition"),
        }
        for field_name in required_by_type.get(asset_type, ()):
            if not cleaned_data.get(field_name):
                self.add_error(field_name, "This field is required for this asset type.")
        return cleaned_data


class AssetAssignmentForm(StyledModelForm):
    class Meta:
        model = AssetAssignment
        fields = [
            "asset",
            "quantity",
            "guard",
            "driver",
            "site",
            "deployment",
            "issued_by",
            "received_by",
            "assigned_date",
            "return_date",
            "status",
            "condition_issued",
            "condition_returned",
            "notes",
            "accountability_notes",
        ]
        labels = {
            "guard": "Accountable Employee",
            "driver": "Accountable Driver",
            "site": "Accountable Site",
            "assigned_date": "Issue Date",
            "return_date": "Return Date",
            "status": "Asset Status",
            "condition_issued": "Condition Issued",
            "condition_returned": "Condition Returned",
            "notes": "Issue Notes",
            "accountability_notes": "Accountability Notes",
        }
        widgets = {
            "assigned_date": DATE_WIDGET,
            "return_date": DATE_WIDGET,
            "notes": forms.Textarea(attrs={"rows": 2}),
            "accountability_notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        active_staff = Employee.objects.exclude(status="inactive").order_by("employee_number", "first_name", "last_name")
        issuers = active_staff.exclude(role="guard")
        if "guard" in self.fields:
            self.fields["guard"].queryset = active_staff
        if "driver" in self.fields:
            self.fields["driver"].queryset = active_staff
        if "issued_by" in self.fields:
            self.fields["issued_by"].queryset = issuers if issuers.exists() else active_staff
        if "received_by" in self.fields:
            self.fields["received_by"].queryset = active_staff
        for field_name in ("guard", "driver", "site", "deployment", "issued_by", "received_by", "condition_returned"):
            if field_name in self.fields and hasattr(self.fields[field_name], "empty_label"):
                self.fields[field_name].empty_label = "Select..."
        if "condition_returned" in self.fields:
            self.fields["condition_returned"].required = False
        if "accountability_notes" in self.fields:
            self.fields["accountability_notes"].required = False

    def clean(self):
        cleaned_data = super().clean()
        asset = cleaned_data.get("asset")
        if not asset:
            return cleaned_data

        assignment_fields = {
            "guard": cleaned_data.get("guard"),
            "driver": cleaned_data.get("driver"),
            "site": cleaned_data.get("site"),
            "deployment": cleaned_data.get("deployment"),
        }
        required_target = {
            "uniform": ("guard", "employee"),
            "vehicle": ("driver", "driver"),
            "gun": ("site", "site"),
            "weapon": ("site", "site"),
            "equipment": ("site", "site"),
        }.get(asset.asset_type)

        if required_target:
            field_name, label = required_target
            if not assignment_fields[field_name]:
                self.add_error(field_name, f"{asset.get_asset_type_display()} assets must be assigned to a {label}.")
            for other_field, value in assignment_fields.items():
                if other_field != field_name and value:
                    self.add_error(other_field, f"{asset.get_asset_type_display()} assets can only be assigned to a {label}.")
        elif not any(assignment_fields.values()):
            self.add_error("asset", "Choose who or where this asset is assigned to.")
        return cleaned_data


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


class DutyRosterUploadForm(forms.Form):
    site = forms.ModelChoiceField(queryset=Site.objects.all(), required=False)
    roster_file = forms.FileField()


class DutyRosterExportForm(forms.Form):
    site = forms.ModelChoiceField(queryset=Site.objects.select_related("region", "client").all())
    period_start = forms.DateField(widget=DATE_WIDGET)
    period_end = forms.DateField(widget=DATE_WIDGET)


class AttendanceForm(StyledModelForm):
    class Meta:
        model = Attendance
        fields = "__all__"
        widgets = {"date": DATE_WIDGET, "time_in": TIME_WIDGET, "time_out": TIME_WIDGET}


class IncidentGuardsOnDutyMixin:
    def selected_incident_site(self):
        site_id = self.data.get("site") if self.is_bound else None
        if site_id:
            return Site.objects.filter(pk=site_id).select_related("region").first()
        if getattr(self.instance, "site_id", None):
            return self.instance.site
        return None

    def selected_incident_date(self):
        raw_date_time = self.data.get("date_time") if self.is_bound else None
        if raw_date_time:
            parsed = parse_datetime(raw_date_time)
            if parsed:
                return parsed.date()
        if getattr(self.instance, "date_time", None):
            return timezone.localtime(self.instance.date_time).date()
        return timezone.localdate()

    @staticmethod
    def deployment_area_regions_for_site(site):
        return site.deployment_area_regions() if site else Region.objects.none()

    @staticmethod
    def deployment_area_guard_choices(site):
        regions = IncidentGuardsOnDutyMixin.deployment_area_regions_for_site(site)
        if not regions.exists():
            return []
        guards = (
            Employee.objects.filter(
                role__in=("guard", "supervisor"),
                status__in=("active", "on_leave"),
                deployment_areas__region__in=regions,
                deployment_areas__status="active",
            )
            .distinct()
            .order_by("employee_number", "first_name", "last_name")
        )
        return [(str(guard.pk), str(guard)) for guard in guards]

    def setup_guards_on_duty_field(self):
        site = self.selected_incident_site()
        choices = self.deployment_area_guard_choices(site)
        existing_names = [name.strip() for name in (self.instance.guards_on_duty or "").replace(",", "\n").splitlines() if name.strip()]
        selected = [value for value, label in choices if label in existing_names]
        existing_choice_values = set(value for value, _label in choices)
        for name in existing_names:
            if name not in dict(choices).values():
                value = f"saved:{name}"
                choices.append((value, name))
                selected.append(value)
                existing_choice_values.add(value)
        self.fields["guards_on_duty"].choices = choices
        if selected:
            self.initial["guards_on_duty"] = selected
        if not site:
            self.fields["guards_on_duty"].help_text = "Select a site to show guards from its deployment area."
        elif not choices:
            self.fields["guards_on_duty"].help_text = "No active guards or supervisors are assigned to this site's deployment area."
        else:
            self.fields["guards_on_duty"].help_text = "Showing guards assigned to the selected site's deployment area."

    def save_guards_on_duty(self, incident):
        guard_values = self.cleaned_data.get("guards_on_duty") or []
        guard_ids = [value for value in guard_values if str(value).isdigit()]
        saved_names = [str(value).replace("saved:", "", 1) for value in guard_values if str(value).startswith("saved:")]
        guards = Employee.objects.filter(pk__in=guard_ids).order_by("employee_number", "first_name", "last_name")
        guard_names = [str(guard) for guard in guards]
        incident.guards_on_duty = ", ".join([*guard_names, *saved_names])


class IncidentForm(IncidentGuardsOnDutyMixin, StyledModelForm):
    reported_by = forms.ChoiceField(
        choices=(),
        required=True,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    reported_to = forms.ChoiceField(
        choices=(),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    guards_on_duty = forms.MultipleChoiceField(
        choices=(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-control", "size": 5}),
    )

    class Meta:
        model = Incident
        fields = [
            "site",
            "incident_type",
            "date_time",
            "location",
            "severity_level",
            "status",
            "reported_by",
            "reported_to",
            "description",
            "guards_on_duty",
        ]
        labels = {
            "reported_by": "Reported By",
            "guards_on_duty": "Guards On Duty",
        }
        widgets = {
            "date_time": DATETIME_WIDGET,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_reported_by_field()
        self.setup_reported_to_field()
        self.setup_guards_on_duty_field()

    def setup_reported_by_field(self):
        supervisors = Employee.objects.filter(role="supervisor").exclude(status="inactive").order_by("employee_number", "first_name", "last_name")
        managers = Employee.objects.filter(role="manager").exclude(status="inactive").order_by("employee_number", "first_name", "last_name")
        employees = [*supervisors, *managers]
        choices = [("", "Select employee..."), *[(str(employee.pk), str(employee)) for employee in employees]]
        existing_name = (self.instance.reported_by or "").strip()
        selected = ""
        for value, label in choices:
            if label == existing_name:
                selected = value
                break
        if existing_name and not selected:
            selected = f"saved:{existing_name}"
            choices.append((selected, existing_name))
        self.fields["reported_by"].choices = choices
        if selected:
            self.initial["reported_by"] = selected
        self.fields["reported_by"].help_text = "Employees listed from supervisors to managers."

    def setup_reported_to_field(self):
        choices = [
            ("", "Select recipient..."),
            ("Control Room", "Control Room"),
            ("Manager", "Manager"),
            ("Police", "Police"),
        ]
        existing_value = (self.instance.reported_to or "").strip()
        selected = existing_value if existing_value in dict(choices) else ""
        if existing_value and not selected:
            selected = existing_value
            choices.append((existing_value, existing_value))
        self.fields["reported_to"].choices = choices
        if selected:
            self.initial["reported_to"] = selected

    def save(self, commit=True):
        incident = super().save(commit=False)
        reported_by = self.cleaned_data.get("reported_by") or ""
        if str(reported_by).startswith("saved:"):
            incident.reported_by = str(reported_by).replace("saved:", "", 1)
        elif str(reported_by).isdigit():
            reporter = Employee.objects.filter(pk=reported_by).first()
            incident.reported_by = str(reporter) if reporter else ""
        self.save_guards_on_duty(incident)
        if commit:
            incident.save()
            self.save_m2m()
        return incident


class IncidentManagementForm(IncidentGuardsOnDutyMixin, StyledModelForm):
    reported_to = forms.ChoiceField(
        choices=(),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    guards_on_duty = forms.MultipleChoiceField(
        choices=(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-control", "size": 5}),
    )

    class Meta:
        model = Incident
        fields = [
            "reported_to",
            "alleged_stolen_items",
            "commencement_of_investigations",
            "police_case_reference",
            "suspects",
            "guards_on_duty",
            "affected_items_details",
            "occurrence_summary",
            "immediate_action_taken",
            "investigation_assigned_to",
            "investigation_findings",
            "corrective_action",
            "conclusion",
            "closed_by",
        ]
        labels = {
            "reported_to": "Reported To",
            "alleged_stolen_items": "Alleged Stolen Items / Incident Details",
            "commencement_of_investigations": "Commencement Of Investigations",
            "police_case_reference": "Police Case Reference",
            "suspects": "Suspect(s)",
            "guards_on_duty": "Guards On Duty",
            "affected_items_details": "Details Of Missing / Affected Items",
            "occurrence_summary": "Occurrence Summary",
            "immediate_action_taken": "Immediate Action Taken",
            "investigation_assigned_to": "Investigation Assigned To",
            "investigation_findings": "Investigation Findings",
            "corrective_action": "Corrective Action",
            "conclusion": "Conclusion",
            "closed_by": "Closed By",
        }
        widgets = {
            "commencement_of_investigations": DATE_WIDGET,
            "alleged_stolen_items": forms.Textarea(attrs={"rows": 3}),
            "suspects": forms.Textarea(attrs={"rows": 2}),
            "affected_items_details": forms.Textarea(attrs={"rows": 4}),
            "occurrence_summary": forms.Textarea(attrs={"rows": 3}),
            "immediate_action_taken": forms.Textarea(attrs={"rows": 3}),
            "investigation_findings": forms.Textarea(attrs={"rows": 4}),
            "corrective_action": forms.Textarea(attrs={"rows": 4}),
            "conclusion": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, action="", user=None, **kwargs):
        self.action = action
        self.user = user
        super().__init__(*args, **kwargs)
        self.setup_reported_to_field()
        investigators = Employee.objects.exclude(status="inactive").order_by("employee_number", "first_name", "last_name")
        self.fields["investigation_assigned_to"].queryset = investigators
        self.fields["investigation_assigned_to"].empty_label = "Select investigator..."
        self.setup_guards_on_duty_field()
        for field_name in self.fields:
            self.fields[field_name].required = False

    def setup_reported_to_field(self):
        choices = [
            ("", "Select recipient..."),
            ("Control Room", "Control Room"),
            ("Manager", "Manager"),
            ("Police", "Police"),
        ]
        existing_value = (self.instance.reported_to or "").strip()
        selected = existing_value if existing_value in dict(choices) else ""
        if existing_value and not selected:
            selected = existing_value
            choices.append((existing_value, existing_value))
        self.fields["reported_to"].choices = choices
        if selected:
            self.initial["reported_to"] = selected

    def clean(self):
        cleaned_data = super().clean()
        required_by_action = {
            "assign_investigation": ("investigation_assigned_to",),
            "save_findings": ("investigation_findings",),
            "record_action": ("corrective_action",),
            "resolve": ("investigation_findings", "corrective_action", "conclusion"),
            "close": ("conclusion",),
        }
        for field_name in required_by_action.get(self.action, ()):
            if not cleaned_data.get(field_name):
                self.add_error(field_name, "This field is required for this action.")
        return cleaned_data

    def save(self, commit=True):
        incident = super().save(commit=False)
        self.save_guards_on_duty(incident)
        if self.action == "assign_investigation":
            incident.status = "investigating"
        elif self.action == "save_findings" and incident.status in ("reported", "notified"):
            incident.status = "investigating"
        elif self.action == "record_action":
            incident.status = "action_taken"
        elif self.action == "resolve":
            incident.status = "resolved"
        elif self.action == "close":
            incident.status = "closed"
            incident.closed_at = timezone.now()
            if not incident.closed_by:
                full_name = self.user.get_full_name() if self.user and hasattr(self.user, "get_full_name") else ""
                incident.closed_by = full_name or getattr(self.user, "username", "") or "System"
        if commit:
            incident.save()
        return incident


class PatrolLogForm(StyledModelForm):
    class Meta:
        model = Patrol_Log
        fields = "__all__"
        widgets = {"patrol_date": DATE_WIDGET}
