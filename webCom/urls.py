from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import user_passes_test
from django.urls import path

from . import views

app_name = "webcom"

staff_required = user_passes_test(lambda user: user.is_authenticated and user.is_staff, login_url="webcom:login")

urlpatterns = [
    path("", views.public_home, name="public_home"),
    path("system/health/", views.system_health, name="system_health"),
    path("staff/login/", auth_views.LoginView.as_view(template_name="webCom/login.html"), name="login"),
    path("staff/logout/", auth_views.LogoutView.as_view(next_page="webcom:public_home"), name="logout"),
    path("who-we-are/", views.public_page, {"page": "who-we-are"}, name="who_we_are"),
    path("what-we-do/", views.public_page, {"page": "what-we-do"}, name="what_we_do"),
    path("where-we-work/", views.public_page, {"page": "where-we-work"}, name="where_we_work"),
    path("impact/", views.public_page, {"page": "impact"}, name="impact"),
    path("resources/", views.public_page, {"page": "resources"}, name="resources"),
    path("careers/", views.careers, name="careers"),
    path("careers/<int:pk>/", views.job_detail, name="job_detail"),
    path("dashboard/", staff_required(views.home), name="home"),
    path("staff/password-management/", staff_required(views.password_management), name="password_management"),
    path("staff/password-management/<int:user_id>/", staff_required(views.password_management_action), name="password_management_action"),
    path("deployments/upload-roster/", staff_required(views.duty_roster_upload), name="duty_roster_upload"),
    path("deployments/export-roster/", staff_required(views.duty_roster_export), name="duty_roster_export"),
    path("contracts/<int:pk>/report/", staff_required(views.contract_report), name="contract_report"),
    path("salaries/<int:pk>/payslip/", staff_required(views.salary_payslip), name="salary_payslip"),
    path("invoices/<int:pk>/invoice/", staff_required(views.invoice_document), name="invoice_document"),
    path("payments/<int:pk>/receipt/", staff_required(views.payment_receipt), name="payment_receipt"),
    path("payments/<int:pk>/reconciliation/", staff_required(views.payment_reconciliation), name="payment_reconciliation"),
    path("purchase-orders/<int:pk>/lpo/", staff_required(views.purchase_order_lpo_report), name="purchase_order_lpo_report"),
    path("training/<int:pk>/certificate/", staff_required(views.training_certificate), name="training_certificate"),
    path("leaves/<int:pk>/review/", staff_required(views.leave_review), name="leave_review"),
    path("employees/<int:pk>/transfer/", staff_required(views.employee_transfer), name="employee_transfer"),
    path("incidents/report/", staff_required(views.incident_report), name="incident_report"),
    path("incidents/notifications/", staff_required(views.incident_notifications), name="incident_notifications"),
    path("disciplinary-actions/notifications/", staff_required(views.disciplinary_notifications), name="disciplinary_notifications"),
    path("budgets/report/", staff_required(views.budget_report), name="budget_report"),
    path("budgets/notifications/", staff_required(views.budget_report), name="budget_notifications"),
    path("expenses/report/", staff_required(views.expense_report), name="expense_report"),
    path("expenses/notifications/", staff_required(views.expense_report), name="expense_notifications"),
    path("finance/aging-report/", staff_required(views.aging_report), name="aging_report"),
    path("api/procurement/", staff_required(views.procurement_api_index), name="procurement_api_index"),
    path("api/payouts/", staff_required(views.payout_api_index), name="payout_api_index"),
    path("api/payouts/<str:batch_type>/", staff_required(views.payout_api_batch), name="payout_api_batch"),
    path("api/procurement/<str:model_name>/", staff_required(views.procurement_api_list), name="procurement_api_list"),
    path("api/procurement/<str:model_name>/<int:pk>/", staff_required(views.procurement_api_detail), name="procurement_api_detail"),
    path("incidents/<int:pk>/notify/", staff_required(views.incident_notify), name="incident_notify"),
    path("procurement-requisitions/<int:pk>/contact-supplier/", staff_required(views.procurement_contact_supplier), name="procurement_contact_supplier"),
    path("asset-assignments/report/", staff_required(views.asset_assignment_report), name="asset_assignment_report"),
    path("assets/report/", staff_required(views.asset_store_report), name="asset_store_report"),
    path("<str:model_name>/", staff_required(views.model_list), name="list"),
    path("<str:model_name>/add/", staff_required(views.model_create), name="create"),
    path("<str:model_name>/import/", staff_required(views.model_import), name="import"),
    path("<str:model_name>/export/", staff_required(views.model_export), name="export"),
    path("<str:model_name>/<int:pk>/", staff_required(views.model_detail), name="detail"),
    path("<str:model_name>/<int:pk>/edit/", staff_required(views.model_update), name="update"),
    path("<str:model_name>/<int:pk>/delete/", staff_required(views.model_delete), name="delete"),
]
