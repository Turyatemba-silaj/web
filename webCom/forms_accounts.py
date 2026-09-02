from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.auth.password_validation import validate_password
from django.db.models import Q

from .access import role_group_permission_queryset, sync_role_group_permissions
from .models import Employee


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
