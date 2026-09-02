from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .forms_base import DATE_WIDGET, DateRangeValidationMixin, StyledModelForm
from .models import (
    DeploymentArea,
    Disciplinary_Action,
    Document,
    Employee,
    Guard,
    Leave,
    Performance_Evaluation,
    Position,
    Region,
    Role,
    Supervisor,
    Training,
)


class EmployeeDeploymentTransferForm(forms.Form):
    from_deployment_area = forms.ModelChoiceField(queryset=DeploymentArea.objects.none(), label="From Deployment Area")
    to_deployment_area = forms.ModelChoiceField(queryset=Region.objects.all().order_by("region_name"), label="To Deployment Area")
    start_date = forms.DateField(label="Transfer Date", widget=DATE_WIDGET)
    transferred_by_hr_manager = forms.ModelChoiceField(queryset=Employee.objects.filter(role__in=("manager", "hr_officer")), required=False)
    transfer_notes = forms.CharField(label="Reason", required=False, widget=forms.Textarea(attrs={"rows": 2, "class": "form-control"}))

    def __init__(self, *args, employee=None, **kwargs):
        self.employee = employee
        super().__init__(*args, **kwargs)
        if employee:
            today = timezone.localdate()
            self.fields["from_deployment_area"].queryset = employee.deployment_areas.filter(status="active", start_date__lte=today).filter(Q(end_date__isnull=True) | Q(end_date__gte=today))

    def save(self):
        with transaction.atomic():
            from_area = self.cleaned_data["from_deployment_area"]
            from_area.end_date = self.cleaned_data["start_date"]
            from_area.status = "completed"
            from_area.save(update_fields=["end_date", "status", "updated_at"])
            return DeploymentArea.objects.create(
                employee=self.employee,
                region=self.cleaned_data["to_deployment_area"],
                start_date=self.cleaned_data["start_date"],
                status="active",
                transferred_by_hr_manager=self.cleaned_data.get("transferred_by_hr_manager"),
                transfer_notes=self.cleaned_data.get("transfer_notes", ""),
            )


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


class LeaveForm(DateRangeValidationMixin, StyledModelForm):
    class Meta:
        model = Leave
        fields = (
            "employee",
            "leave_type",
            "start_date",
            "end_date",
            "address_while_away",
            "emergency_contact",
            "hr_verifier",
            "supervisor",
            "hod",
            "stand_in_coworker",
            "reason",
        )
        labels = {
            "reason": "Description",
            "start_date": "From Date",
            "end_date": "To Date",
            "hr_verifier": "HR Verifier",
            "hod": "HOD",
            "stand_in_coworker": "Stand-In Coworker",
        }
        widgets = {
            "start_date": DATE_WIDGET,
            "end_date": DATE_WIDGET,
            "reason": forms.Textarea(attrs={"class": "form-control", "rows": 4, "placeholder": "Enter Leave Description ..."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        active_staff = Employee.objects.exclude(status="inactive").order_by("employee_number", "first_name", "last_name")
        hr_staff = active_staff.filter(Q(role__in=("hr_officer", "manager", "administrator")) | Q(department="hr"))
        supervisors = active_staff.filter(Q(role__in=("supervisor", "manager", "operations_officer", "administrator")) | Q(department="operations")).exclude(role="guard")
        hods = active_staff.filter(Q(role__in=("manager", "administrator", "hr_officer", "operations_officer")) | Q(department__in=("admin", "hr", "operations"))).exclude(role="guard")

        self.fields["employee"].queryset = active_staff
        self.fields["hr_verifier"].queryset = hr_staff
        self.fields["supervisor"].queryset = supervisors if supervisors.exists() else active_staff
        self.fields["hod"].queryset = hods if hods.exists() else active_staff
        self.fields["stand_in_coworker"].queryset = active_staff
        self.fields["leave_type"].choices = [("", "Select...")] + list(self.fields["leave_type"].choices)
        for field_name in ("employee", "supervisor", "hod", "stand_in_coworker"):
            self.fields[field_name].empty_label = "Select..."
        self.fields["address_while_away"].widget.attrs.setdefault("placeholder", "Address while away")
        self.fields["emergency_contact"].widget.attrs.setdefault("placeholder", "Emergency contact")

        selected_employee = self.initial.get("employee") or getattr(self.instance, "employee_id", None)
        if self.data:
            selected_employee = self.data.get(self.add_prefix("employee")) or selected_employee
        if selected_employee:
            self.fields["stand_in_coworker"].queryset = active_staff.exclude(pk=selected_employee)

        default_verifier = hr_staff.first()
        if default_verifier and not self.initial.get("hr_verifier") and not getattr(self.instance, "hr_verifier_id", None):
            self.initial["hr_verifier"] = default_verifier.pk

        self.fields["hr_verifier"].disabled = True
        self.fields["hr_verifier"].required = False
        self.fields["supervisor"].required = True
        self.fields["hod"].required = True
        self.fields["stand_in_coworker"].required = True
        for field_name in ("address_while_away", "emergency_contact"):
            self.fields[field_name].required = True

    def save(self, commit=True):
        leave = super().save(commit=False)
        if not leave.hr_verifier_id:
            leave.hr_verifier = self.fields["hr_verifier"].queryset.first()
        if commit:
            leave.save()
            self.save_m2m()
        return leave


class LeaveReviewForm(forms.Form):
    operations_manager = forms.ModelChoiceField(queryset=Employee.objects.filter(role__in=("manager", "operations_officer")), required=False)
    operations_status = forms.ChoiceField(choices=(("verified", "Verified"), ("rejected", "Rejected")), required=False)
    operations_feedback = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2, "class": "form-control"}))
    hr_manager = forms.ModelChoiceField(queryset=Employee.objects.filter(role__in=("manager", "hr_officer")), required=False)
    hr_decision = forms.ChoiceField(choices=(("approved", "Approved"), ("rejected", "Rejected")), required=False)
    feedback = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}))

    def __init__(self, *args, **kwargs):
        self.leave = kwargs.pop("leave", None)
        super().__init__(*args, **kwargs)
        if self.leave:
            self.fields["operations_manager"].initial = self.leave.verified_by
            self.fields["operations_status"].initial = "" if self.leave.operations_verification_status == "pending" else self.leave.operations_verification_status
            self.fields["operations_feedback"].initial = self.leave.operations_feedback
            self.fields["hr_manager"].initial = self.leave.approved_by
            self.fields["hr_decision"].initial = "" if self.leave.approval_status == "pending" else self.leave.approval_status
            self.fields["feedback"].initial = self.leave.feedback

    def clean(self):
        cleaned_data = super().clean()
        operations_status = cleaned_data.get("operations_status")
        operations_manager = cleaned_data.get("operations_manager")
        hr_decision = cleaned_data.get("hr_decision")
        hr_manager = cleaned_data.get("hr_manager")

        if not self.leave:
            raise ValidationError("Leave request is required.")
        if not operations_status:
            self.add_error("operations_status", "Choose an operations decision.")
        if operations_status and not operations_manager:
            self.add_error("operations_manager", "Choose the operations manager.")
        if operations_status == "verified" and not hr_decision:
            self.add_error("hr_decision", "Choose the HR decision.")
        if hr_decision and not hr_manager:
            self.add_error("hr_manager", "Choose the HR manager.")
        return cleaned_data

    def save(self):
        return self.leave.process_review(
            operations_manager=self.cleaned_data["operations_manager"],
            operations_status=self.cleaned_data["operations_status"],
            operations_feedback=self.cleaned_data.get("operations_feedback", ""),
            hr_manager=self.cleaned_data.get("hr_manager"),
            hr_decision=self.cleaned_data.get("hr_decision", ""),
            feedback=self.cleaned_data.get("feedback", ""),
        )


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
