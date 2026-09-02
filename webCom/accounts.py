from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .access import role_group_permission_queryset
from .forms_accounts import AdminUserCreationForm, ForgotPasswordResetForm, PasswordExpiredChangeForm, PasswordResetManagementForm
from .models import Employee, UserPasswordProfile
from .views import render_page

def _employee_account_maps():
    employees = Employee.objects.only("employee_id", "employee_number", "first_name", "last_name", "email", "status")
    by_email = {}
    by_employee_number = {}
    for employee in employees:
        if employee.email:
            by_email[employee.email.strip().lower()] = employee
        if employee.employee_number:
            by_employee_number[employee.employee_number.strip().lower()] = employee
    return by_email, by_employee_number


def _matched_employee_for_user(user, by_email, by_employee_number):
    email_key = (user.email or "").strip().lower()
    username_key = (user.username or "").strip().lower()
    if email_key and email_key in by_email:
        return by_email[email_key]
    if username_key and username_key in by_employee_number:
        return by_employee_number[username_key]
    return None


def password_profile_for(user):
    profile, _created = UserPasswordProfile.objects.get_or_create(user=user)
    return profile


def password_status_for(user):
    profile = password_profile_for(user)
    if profile.force_password_change:
        return "Reset required"
    if profile.is_expired:
        return "Expired"
    if profile.days_until_expiry <= 14:
        return "Expiring soon"
    return "Current"

def _password_management_rows():
    User = get_user_model()
    by_email, by_employee_number = _employee_account_maps()
    rows = []
    users = User.objects.prefetch_related("groups__permissions", "user_permissions").filter(Q(username__iexact="Trans") | Q(first_name__iexact="Namakula", last_name__iexact="Jenifah")).order_by("id")
    for user in users:
        employee = _matched_employee_for_user(user, by_email, by_employee_number)
        groups = ", ".join(group.name for group in user.groups.all()) or "No role group"
        permission_names = sorted({
            permission.name
            for group in user.groups.all()
            for permission in group.permissions.all()
        } | {permission.name for permission in user.user_permissions.all()})
        permissions = f"{len(permission_names)} permission{'s' if len(permission_names) != 1 else ''}" if permission_names else "No permission assigned"
        profile = password_profile_for(user)
        rows.append(
            {
                "user": user,
                "groups": groups,
                "permissions": permissions,
                "employee": employee,
                "employee_status": employee.get_status_display() if employee else "No linked employee",
                "is_terminated_staff": bool(employee and employee.status == "terminated"),
                "password_profile": profile,
                "password_status": password_status_for(user),
                "password_changed_at": profile.password_changed_at,
                "password_expires_at": profile.expires_at,
                "password_days_left": profile.days_until_expiry,
            }
        )
    return rows


def _deactivate_terminated_staff_accounts():
    User = get_user_model()
    by_email, by_employee_number = _employee_account_maps()
    users = User.objects.filter(is_active=True)
    changed = []
    for user in users:
        employee = _matched_employee_for_user(user, by_email, by_employee_number)
        if employee and employee.status == "terminated":
            user.is_active = False
            user.save(update_fields=["is_active"])
            changed.append(user.username)
    return changed


def password_management(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Only system administrators can manage staff account access.")

    create_user_form = AdminUserCreationForm()

    if request.method == "POST" and request.POST.get("action") == "deactivate_terminated":
        changed = _deactivate_terminated_staff_accounts()
        if changed:
            messages.success(request, f"Deactivated {len(changed)} terminated staff account(s): {', '.join(changed[:8])}.")
        else:
            messages.info(request, "No active Django user accounts matched terminated staff records.")
        return redirect("webcom:password_management")

    if request.method == "POST" and request.POST.get("action") == "create_user":
        create_user_form = AdminUserCreationForm(request.POST)
        if create_user_form.is_valid():
            new_user = create_user_form.save()
            password_profile_for(new_user).mark_changed(admin_reset=True, force_change=True)
            messages.success(request, f"Created user {new_user.username}. Temporary password must be changed at first login.")
            return redirect("webcom:password_management")
        messages.error(request, "User account was not created. Review the highlighted fields.")

    role_permission_options = {}
    for group in create_user_form.fields["groups"].queryset:
        permissions = role_group_permission_queryset(group).select_related("content_type").order_by("content_type__model", "codename")
        role_permission_options[str(group.pk)] = [
            {"value": str(permission.pk), "label": create_user_form.permission_label(permission)}
            for permission in permissions
        ]

    rows = _password_management_rows()
    context = {
        "rows": rows,
        "create_user_form": create_user_form,
        "role_permission_options": role_permission_options,
        "total_users": len(rows),
        "active_users": sum(1 for row in rows if row["user"].is_active),
        "blocked_users": sum(1 for row in rows if not row["user"].is_active),
        "terminated_staff_accounts": sum(1 for row in rows if row["is_terminated_staff"]),
    }
    return render_page(request, "webCom/password_management.html", context, "password-management")

def password_management_action(request, user_id):
    if not request.user.is_superuser:
        raise PermissionDenied("Only system administrators can manage staff account access.")
    if request.method != "POST":
        return redirect("webcom:password_management")

    User = get_user_model()
    managed_user = get_object_or_404(User, pk=user_id)
    action = request.POST.get("action")
    by_email, by_employee_number = _employee_account_maps()
    employee = _matched_employee_for_user(managed_user, by_email, by_employee_number)

    if action == "activate":
        if employee and employee.status == "terminated":
            messages.error(request, f"{managed_user.username} is linked to a terminated employee record. Reactivate the employee first if this account should be restored.")
        else:
            managed_user.is_active = True
            managed_user.save(update_fields=["is_active"])
            messages.success(request, f"Activated {managed_user.username}.")
    elif action == "block":
        if managed_user.pk == request.user.pk:
            messages.error(request, "You cannot block your own active administrator account.")
        else:
            managed_user.is_active = False
            managed_user.save(update_fields=["is_active"])
            messages.success(request, f"Blocked {managed_user.username}.")
    elif action == "reset_password":
        form = PasswordResetManagementForm(request.POST, user=managed_user)
        if form.is_valid():
            managed_user.set_password(form.cleaned_data["new_password"])
            managed_user.save(update_fields=["password"])
            password_profile_for(managed_user).mark_changed(admin_reset=True, force_change=True)
            messages.success(request, f"Temporary password set for {managed_user.username}. The user must change it at next login.")
        else:
            errors = "; ".join(error for field_errors in form.errors.values() for error in field_errors)
            messages.error(request, f"Password reset failed for {managed_user.username}: {errors}")
    else:
        messages.error(request, "Unknown account management action.")

    return redirect("webcom:password_management")

def password_expired(request):
    if not request.user.is_authenticated:
        return redirect("webcom:login")
    profile = password_profile_for(request.user)
    form = PasswordExpiredChangeForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        request.user.set_password(form.cleaned_data["new_password"])
        request.user.save(update_fields=["password"])
        profile.mark_changed(force_change=False)
        update_session_auth_hash(request, request.user)
        messages.success(request, "Your password has been updated.")
        return redirect("webcom:home")
    context = {
        "form": form,
        "profile": profile,
        "title": "Password Reset Required",
    }
    return render(request, "webCom/password_expired.html", context)


def forgot_password(request):
    User = get_user_model()
    matched_user = None
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        email = (request.POST.get("email") or "").strip().lower()
        matched_user = User.objects.filter(username=username, email__iexact=email, is_active=True).first()
    form = ForgotPasswordResetForm(request.POST or None, user=matched_user)
    if request.method == "POST":
        if not matched_user:
            form.add_error(None, "No active account matched that username and email.")
        elif form.is_valid():
            matched_user.set_password(form.cleaned_data["new_password"])
            matched_user.save(update_fields=["password"])
            password_profile_for(matched_user).mark_changed(force_change=False)
            messages.success(request, "Password reset successfully. You can sign in with the new password.")
            return redirect("webcom:login")
    return render(request, "webCom/forgot_password.html", {"form": form, "title": "Forgot Password"})
