from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

ROLE_GROUPS = {
    "Supervisor": {
        "models": {"attendance"},
        "views": set(),
    },
    "Human Resources": {
        "models": {
            "employees",
            "guards",
            "supervisors",
            "training",
            "leaves",
            "disciplinary-actions",
            "performance-evaluations",
            "documents",
            "payroll",
            "salaries",
            "advances",
            "payroll-deductions",
        },
        "views": {"payroll"},
    },
    "Operations Manager": {
        "models": {
            "clients",
            "contracts",
            "regions",
            "sites",
            "shifts",
            "assets",
            "asset-assignments",
            "incidents",
            "deployments",
            "deployment-areas",
            "attendance",
        },
        "views": {
            "asset_assignment_report",
            "asset_store_report",
            "contract_report",
            "duty_roster_export",
            "duty_roster_upload",
            "incident_notifications",
            "incident_notify",
            "incident_report",
        },
    },
    "Finance Officer": {
        "models": {
            "procurement",
            "suppliers",
            "procurement-requisitions",
            "procurement-approvals",
            "proforma-item-prices",
            "supplier-proformas",
            "purchase-orders",
            "goods-received-notes",
            "supplier-invoices",
            "supplier-payments",
            "procurement-notifications",
            "invoices",
            "paymees",
            "payments",
            "budgets",
            "expenses",
        },
        "views": {
            "aging_report",
            "budget_notifications",
            "budget_report",
            "expense_notifications",
            "expense_report",
            "invoice_document",
            "payment_receipt",
            "payment_reconciliation",
            "payout_api_batch",
            "payout_api_index",
            "procurement",
            "procurement_api_detail",
            "procurement_api_index",
            "procurement_api_list",
            "procurement_contact_supplier",
        },
    },
}

ROLE_GROUPS["Head of Finance"] = {
    "models": set(ROLE_GROUPS["Finance Officer"]["models"]),
    "views": set(ROLE_GROUPS["Finance Officer"]["views"]),
}
MODEL_PERMISSION_NAMES = {
    "attendance": "attendance",
    "employees": "employee",
    "guards": "guard",
    "supervisors": "supervisor",
    "training": "training",
    "leaves": "leave",
    "disciplinary-actions": "disciplinary_action",
    "performance-evaluations": "performance_evaluation",
    "documents": "document",
    "salaries": "salary",
    "advances": "advance",
    "payroll-deductions": "payrolldeduction",
    "clients": "client",
    "contracts": "contract",
    "regions": "region",
    "sites": "site",
    "shifts": "shift",
    "assets": "asset",
    "asset-assignments": "assetassignment",
    "incidents": "incident",
    "deployments": "deployment",
    "deployment-areas": "deploymentarea",
    "suppliers": "supplier",
    "procurement-requisitions": "procurementrequisition",
    "procurement-approvals": "procurementapproval",
    "proforma-item-prices": "supplierproformaitemprice",
    "supplier-proformas": "supplierproformainvoice",
    "purchase-orders": "purchaseorder",
    "goods-received-notes": "goodsreceivednote",
    "supplier-invoices": "supplierinvoice",
    "supplier-payments": "supplierpayment",
    "procurement-notifications": "procurementnotification",
    "invoices": "invoice",
    "paymees": "paymee",
    "payments": "payment",
    "budgets": "budget",
    "expenses": "expense",
}

ROLE_PERMISSION_ACTIONS = ("view", "add", "change", "delete")


def role_group_permission_queryset(group_or_name):
    group_name = getattr(group_or_name, "name", group_or_name) or ""
    model_slugs = ROLE_GROUPS.get(group_name, {}).get("models", set())
    model_names = [MODEL_PERMISSION_NAMES[slug] for slug in model_slugs if slug in MODEL_PERMISSION_NAMES]
    codenames = []
    for model_name in model_names:
        codenames.extend(f"{action}_{model_name}" for action in ROLE_PERMISSION_ACTIONS)
    return Permission.objects.filter(content_type__app_label="webCom", codename__in=codenames)


def sync_role_group_permissions(group):
    permissions = role_group_permission_queryset(group)
    if group.name in ROLE_GROUPS or group.name == "Head of Finance":
        group.permissions.set(permissions)
    return permissions

DEFAULT_USERS = {
    "supervisor": "Supervisor",
    "hr": "Human Resources",
    "operation_manager": "Operations Manager",
    "finance_officer": "Finance Officer",
    "head_of_finance": "Head of Finance",
}

DEFAULT_PASSWORD = "ChangeMe123!"


def user_group_names(user):
    if not getattr(user, "is_authenticated", False):
        return set()
    return set(user.groups.values_list("name", flat=True))


def user_allowed_models(user):
    if not getattr(user, "is_authenticated", False):
        return set()
    group_names = user_group_names(user)
    if user.is_superuser:
        return None
    restricted_groups = group_names.intersection(ROLE_GROUPS)
    if not restricted_groups:
        return None
    allowed = set()
    for group_name in restricted_groups:
        allowed.update(ROLE_GROUPS[group_name].get("models", set()))
    return allowed


def user_allowed_views(user):
    if not getattr(user, "is_authenticated", False):
        return set()
    group_names = user_group_names(user)
    if user.is_superuser:
        return None
    restricted_groups = group_names.intersection(ROLE_GROUPS)
    if not restricted_groups:
        return None
    allowed = set()
    for group_name in restricted_groups:
        allowed.update(ROLE_GROUPS[group_name].get("views", set()))
    return allowed


def can_access_model(user, model_name):
    allowed = user_allowed_models(user)
    return allowed is None or model_name in allowed


def can_access_view(user, view_name):
    allowed = user_allowed_views(user)
    return allowed is None or view_name in allowed


def require_model_access(request, model_name):
    if not can_access_model(request.user, model_name):
        raise PermissionDenied("You do not have permission to open this module.")


def require_view_access(request, view_name):
    if not can_access_view(request.user, view_name):
        raise PermissionDenied("You do not have permission to open this page.")


def first_allowed_model(user):
    allowed = user_allowed_models(user)
    if allowed is None:
        return None
    preferred = [
        "attendance",
        "employees",
        "payroll",
        "clients",
        "procurement",
        "invoices",
        "payments",
        "budgets",
    ]
    for model_name in preferred:
        if model_name in allowed:
            return model_name
    return next(iter(sorted(allowed)), None)


def redirect_to_first_allowed_module(user):
    model_name = first_allowed_model(user)
    if model_name:
        return redirect("webcom:list", model_name=model_name)
    raise PermissionDenied("No modules are assigned to this account.")


def sync_default_role_accounts(password=DEFAULT_PASSWORD):
    User = get_user_model()
    created = []
    updated = []
    for username, group_name in DEFAULT_USERS.items():
        group, _group_created = Group.objects.get_or_create(name=group_name)
        user, was_created = User.objects.get_or_create(username=username)
        user.is_staff = True
        if group_name == "Head of Finance":
            user.is_superuser = True
        if was_created or not user.has_usable_password():
            user.set_password(password)
        user.save()
        sync_role_group_permissions(group)
        user.groups.add(group)
        if was_created:
            created.append(username)
        else:
            updated.append(username)
    return created, updated



