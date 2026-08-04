import csv
import json
import io
import re
import zipfile
from xml.etree import ElementTree as ET
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.db import DatabaseError, connection, models, transaction
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.forms import modelformset_factory
from django.db.models import Q, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import (
    AdvanceForm,
    AssetForm,
    AssetAssignmentForm,
    AttendanceForm,
    BudgetForm,
    ClientForm,
    ContractForm,
    ContractDeliverableForm,
    ContractDeliverableFormSet,
    DeploymentForm,
    get_default_shift,
    DeploymentAreaForm,
    EmployeeDeploymentTransferForm,
    DutyRosterExportForm,
    DutyRosterUploadForm,
    DisciplinaryActionForm,
    DocumentForm,
    EmployeeForm,
    ExpenseForm,
    GuardForm,
    IncidentForm,
    WebsiteAdvertisementForm,
    CompanyEventForm,
    WebsiteResourceForm,
    AssociatedLinkForm,
    JobPostingForm,
    JobApplicationForm,
    PublicJobApplicationForm,
    InvoiceForm,
    InvoiceBillableItemPriceForm,
    InvoiceBillableItemFormSet,
    LeaveForm,
    LeaveReviewForm,
    PatrolLogForm,
    PaymeeForm,
    PayrollDeductionForm,
    AdminUserCreationForm,
    PasswordExpiredChangeForm,
    PasswordResetManagementForm,
    ForgotPasswordResetForm,
    PaymentForm,
    SupplierForm,
    ProcurementRequisitionForm,
    ProcurementApprovalForm,
    SupplierProformaItemPriceForm,
    SupplierProformaInvoiceForm,
    SupplierProformaInvoiceItemFormSet,
    PurchaseOrderForm,
    GoodsReceivedNoteForm,
    SupplierInvoiceForm,
    SupplierPaymentForm,
    PerformanceEvaluationForm,
    PositionForm,
    RegionForm,
    RoleForm,
    SalaryForm,
    ShiftForm,
    SiteForm,
    SupervisorForm,
    TrainingForm,
)
from .access import (
    can_access_model,
    redirect_to_first_allowed_module,
    require_model_access,
    user_allowed_models,
    require_view_access,
    role_group_permission_queryset,
)
from .models import (
    Advance,
    AuditLog,
    UserPasswordProfile,
    Asset,
    AssetAssignment,
    Attendance,
    Budget,
    BudgetNotification,
    Client,
    Contract,
    ContractDeliverable,
    Deployment,
    DeploymentArea,
    Region,
    Disciplinary_Action,
    DisciplinaryNotification,
    Document,
    Employee,
    Expense,
    ExpenseNotification,
    Guard,
    Incident,
    WebsiteAdvertisement,
    CompanyEvent,
    WebsiteResource,
    AssociatedLink,
    JobPosting,
    JobApplication,
    IncidentNotification,
    Invoice,
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
    ProcurementNotification,
    Performance_Evaluation,
    Position,
    Role,
    Salary,
    Shift,
    Site,
    Supervisor,
    Training,
)


MODEL_CONFIGS = {
    "audit-logs": {"model": AuditLog, "form": None, "title": "Audit Logs", "icon": "AL", "fields": ["audit_id", "created_at", "username", "action", "method", "status_code", "path", "ip_address"], "detail_fields": ["audit_id", "user", "username", "action", "path", "method", "status_code", "ip_address", "user_agent", "created_at"], "managed_table": True, "managed_message": "Audit logs are captured automatically from staff activity."},
    "website-adverts": {"model": WebsiteAdvertisement, "form": WebsiteAdvertisementForm, "title": "Website Adverts", "icon": "WA", "fields": ["title", "placement", "starts_on", "ends_on", "is_active"], "detail_fields": ["title", "placement", "message", "call_to_action", "target_url", "starts_on", "ends_on", "is_active", "created_at", "updated_at"], "required_only": False},
    "company-events": {"model": CompanyEvent, "form": CompanyEventForm, "title": "Company Events", "icon": "CE", "fields": ["title", "event_date", "location", "is_public"], "detail_fields": ["title", "event_date", "location", "summary", "is_public", "created_at", "updated_at"], "required_only": False},
    "website-resources": {"model": WebsiteResource, "form": WebsiteResourceForm, "title": "Website Resources", "icon": "WR", "fields": ["title", "resource_type", "url", "is_public"], "detail_fields": ["title", "resource_type", "summary", "url", "is_public", "created_at", "updated_at"], "required_only": False},
    "associated-links": {"model": AssociatedLink, "form": AssociatedLinkForm, "title": "Associated Links", "icon": "AL", "fields": ["title", "category", "url", "is_public"], "detail_fields": ["title", "category", "url", "description", "is_public", "created_at", "updated_at"], "required_only": False},
    "job-postings": {"model": JobPosting, "form": JobPostingForm, "title": "Job Postings", "icon": "JP", "fields": ["title", "department", "location", "employment_type", "deadline", "is_online", "is_active"], "detail_fields": ["title", "department", "location", "employment_type", "summary", "requirements", "deadline", "is_online", "is_active", "posted_at", "created_at", "updated_at"], "required_only": False},
    "job-applications": {"model": JobApplication, "form": JobApplicationForm, "title": "Job Applications", "icon": "JA", "fields": ["job", "application_mode", "applicant_name", "phone_number", "status", "submitted_at"], "detail_fields": ["job", "application_mode", "applicant_name", "phone_number", "email", "address", "qualification", "experience_summary", "cover_note", "resume", "received_by", "status", "submitted_at", "updated_at"], "required_only": False},
    "clients": {"model": Client, "form": ClientForm, "title": "Clients", "icon": "CL", "fields": ["client_id", "client_name", "contact_person", "phone_number", "email"], "detail_fields": ["client_id", "client_name", "contact_person", "phone_number", "email", "address"]},
    "contracts": {"model": Contract, "form": ContractForm, "title": "Contracts", "icon": "CT", "fields": ["contract_id", "contract_number", "client", "client_name", "day_shift_guards", "night_shift_guards", "number_of_guards", "rate_per_guard", "contract_value"], "detail_fields": ["contract_id", "contract_number", "client", "client_name", "contact_person", "phone_number", "email", "address", "contract_start_date", "contract_end_date", "number_of_sites", "day_shift_guards", "night_shift_guards", "number_of_guards", "rate_per_guard", "guard_contract_value", "deliverables_value", "contract_value", "contract_status"]},
    "regions": {"model": Region, "form": RegionForm, "title": "Regions", "icon": "RG", "fields": ["region_id", "region_name", "description"], "required_only": False},
    "sites": {"model": Site, "form": SiteForm, "title": "Sites", "icon": "ST", "fields": ["site_id", "region", "client", "contract", "site_name", "site_address", "day_shift_guards", "night_shift_guards", "number_of_guards"], "detail_fields": ["site_id", "region", "client", "contract", "site_name", "site_address", "day_shift_guards", "night_shift_guards", "number_of_guards"], "required_only": False},
    "shifts": {"model": Shift, "form": ShiftForm, "title": "Shifts", "icon": "SH", "fields": ["shift_type", "start_time", "end_time", "hours_per_shift"]},
    "assets": {"model": Asset, "form": AssetForm, "title": "Assets", "icon": "AS", "fields": ["asset_type", "asset_name", "asset_number", "quantity"]},
    "asset-assignments": {"model": AssetAssignment, "form": AssetAssignmentForm, "title": "Asset Assignments", "icon": "AA", "fields": ["asset", "quantity", "assigned_to", "site", "deployment", "status"]},
    "incidents": {"model": Incident, "form": IncidentForm, "title": "Incidents", "icon": "IN", "fields": ["incident_type", "site", "severity_level", "status", "date_time"], "detail_fields": ["incident_id", "site", "incident_type", "severity_level", "status", "date_time", "location", "reported_by", "reported_to", "description", "occurrence_summary", "immediate_action_taken", "notification_summary", "investigation_assigned_to", "investigation_findings", "corrective_action", "conclusion", "closed_by", "closed_at", "created_at", "updated_at"]},
    "patrol-logs": {"model": Patrol_Log, "form": PatrolLogForm, "title": "Patrol Logs", "icon": "PL", "fields": ["site", "patrol_date", "patrol_route", "quantity", "duration"]},
    "deployments": {"model": Deployment, "form": DeploymentForm, "title": "Deployments", "icon": "DP", "fields": ["client", "site", "shift_summary", "start_date", "end_date", "status"], "entry_fields": ["client", "site", "shift_type", "start_date", "end_date", "status"], "required_only": False},
    "deployment-areas": {"model": DeploymentArea, "form": DeploymentAreaForm, "title": "Deployment Areas", "icon": "DA", "fields": ["employee", "region", "start_date", "end_date", "status", "transferred_by_hr_manager"], "detail_fields": ["deployment_area_id", "employee", "region", "start_date", "end_date", "status", "transferred_by_hr_manager", "transfer_notes"], "entry_fields": ["employee", "region", "start_date", "end_date", "status", "transferred_by_hr_manager", "transfer_notes"], "required_only": False},
    "roles": {"model": Role, "form": RoleForm, "title": "Roles", "icon": "RO", "fields": ["role_name", "department", "description"]},
    "positions": {"model": Position, "form": PositionForm, "title": "Positions", "icon": "PO", "fields": ["position_title", "department", "grade_level", "salary_range_min", "salary_range_max"]},
    "employees": {"model": Employee, "form": EmployeeForm, "title": "Employees", "icon": "EM", "fields": ["employee_id", "employee_number", "first_name", "last_name", "role", "position", "department", "current_deployment_area", "daily_rate", "is_reliever", "status", "account_status_display"], "detail_fields": ["employee_id", "employee_number", "first_name", "last_name", "date_of_birth", "gender", "phone_number", "email", "address", "national_id", "nssf_number", "role", "position", "department", "current_deployment_area", "daily_rate", "is_reliever", "payout_method", "bank_name", "bank_account_name", "bank_account_number", "mobile_money_provider", "mobile_money_number", "qualification", "hire_date", "status", "account_status_display"], "entry_fields": ["first_name", "last_name", "date_of_birth", "gender", "phone_number", "email", "address", "national_id", "role", "position", "department", "hire_date", "deployment_area", "is_reliever", "payout_method", "bank_name", "bank_account_name", "bank_account_number", "mobile_money_provider", "mobile_money_number", "status"], "ordering": ["employee_id"], "required_only": False},
    "guards": {"model": Guard, "form": GuardForm, "title": "Guards", "icon": "GD", "fields": ["employee", "qualification", "armed_status"]},
    "supervisors": {"model": Supervisor, "form": SupervisorForm, "title": "Supervisors", "icon": "SV", "fields": ["employee", "authority_level"]},
    "training": {"model": Training, "form": TrainingForm, "title": "Training", "icon": "TR", "fields": ["training_type", "trainee", "training_name", "provider", "start_date", "end_date"], "detail_fields": ["training_id", "training_type", "trainee", "training_name", "provider", "start_date", "end_date"], "entry_fields": ["training_type", "employee", "recruit", "training_name", "provider", "start_date", "end_date"], "required_only": False},
    "attendance": {"model": Attendance, "form": AttendanceForm, "title": "Attendance", "icon": "AT", "fields": ["site", "shift", "scheduled_guard", "present", "attended_guard", "date"]},
    "leaves": {"model": Leave, "form": LeaveForm, "title": "Leaves", "icon": "LV", "fields": ["employee", "leave_type", "start_date", "end_date", "operations_verification_status", "approval_status"], "detail_fields": ["leave_id", "employee", "leave_type", "start_date", "end_date", "reason", "operations_verification_status", "verified_by", "operations_feedback", "operations_verified_at", "approval_status", "approved_by", "feedback", "hr_decided_at", "created_at", "updated_at"]},
    "disciplinary-actions": {"model": Disciplinary_Action, "form": DisciplinaryActionForm, "title": "Disciplinary Actions", "icon": "DA", "fields": ["employee", "offence_committed", "offence_date", "status", "outcome", "approval_status"], "detail_fields": ["action_id", "employee", "offence_committed", "offence_date", "reported_by", "action_date", "description", "investigation_notes", "hearing_date", "hearing_notes", "steps_taken", "outcome", "conclusion", "concluded_on", "handled_by", "status", "approval_status", "reason"], "required_only": False},
    "performance-evaluations": {"model": Performance_Evaluation, "form": PerformanceEvaluationForm, "title": "Performance Evaluations", "icon": "PE", "fields": ["employee", "review_period_start", "review_period_end", "overall_score", "rating", "status", "evaluated_by"], "detail_fields": ["eval_id", "employee", "date", "review_period_start", "review_period_end", "evaluated_by", "job_knowledge", "quality_of_work", "productivity", "reliability_attendance", "communication", "teamwork", "discipline_compliance", "customer_service", "initiative_problem_solving", "safety_security_awareness", "overall_score", "rating", "strengths", "areas_for_improvement", "goals", "training_recommendations", "supervisor_comments", "employee_comments", "status", "comments"], "required_only": False},
    "documents": {"model": Document, "form": DocumentForm, "title": "Documents", "icon": "DC", "fields": ["employee", "doc_type", "file_path", "expiry_date"], "detail_fields": ["employee", "doc_type", "file_path", "expiry_date", "created_at", "updated_at"], "ordering": ["employee__first_name", "employee__last_name", "expiry_date", "-created_at"], "required_only": False},
    "salaries": {"model": Salary, "form": SalaryForm, "title": "Salaries", "icon": "SA", "fields": ["employee", "shifts_worked", "standard_shifts", "overtime_shifts", "daily_rate", "basic_salary", "overtime_pay", "allowances", "loan_deduction", "medical_deduction", "advance_recovery", "ledger_installment_amount", "ledger_advance_balance", "total_deductions", "total_salary", "pay_period"], "detail_fields": ["employee", "pay_period", "period_start_date", "period_end_date", "shifts_worked", "standard_shifts", "overtime_shifts", "daily_rate", "overtime_daily_rate", "basic_salary", "overtime_pay", "allowances", "loan_deduction", "medical_deduction", "other_payroll_deductions", "advance_recovery", "ledger_installment_amount", "ledger_advance_balance", "deductions", "bonus", "gross_pay", "total_deductions", "total_salary"], "required_only": False, "managed_table": True},
    "advances": {"model": Advance, "form": AdvanceForm, "title": "Advances", "icon": "AD", "fields": ["advance_id", "employee", "amount_requested", "installment_amount", "balance", "approval_status", "status", "disbursement_date"], "detail_fields": ["advance_id", "employee", "amount_requested", "installment_amount", "balance", "approval_status", "approved_by", "disbursement_date", "status", "created_at", "updated_at"], "entry_fields": ["employee", "amount_requested", "approval_status", "approved_by", "disbursement_date", "status"], "required_only": False},
    "payroll-deductions": {"model": PayrollDeduction, "form": PayrollDeductionForm, "title": "Payroll Deductions", "icon": "PD", "fields": ["employee", "category", "description", "amount", "start_date", "end_date", "status"], "detail_fields": ["employee", "category", "description", "amount", "start_date", "end_date", "status", "created_at", "updated_at"], "required_only": False},
    "invoices": {"model": Invoice, "form": InvoiceForm, "title": "Invoices", "icon": "IV", "fields": ["invoice_number", "contract", "client", "invoiced_site_count", "invoice_date", "due_date", "deployed_guards", "rate_per_guard", "contract_amount", "tax_amount", "total_amount", "status"], "detail_fields": ["invoice_number", "contract", "client", "invoiced_sites", "invoice_date", "due_date", "billing_start_date", "billing_end_date", "description", "deployed_guards", "rate_per_guard", "contract_amount", "tax_rate", "tax_amount", "total_amount", "status", "created_at", "updated_at"], "entry_fields": ["contract", "sites", "billable_products", "invoice_date", "due_date", "billing_start_date", "billing_end_date", "tax_rate", "status"], "required_only": False},
    "invoice-item-prices": {"model": InvoiceBillableItemPrice, "form": InvoiceBillableItemPriceForm, "title": "Provisional Item Prices", "icon": "IP", "fields": ["item_name", "unit_price", "taxable", "active"], "detail_fields": ["item_name", "unit_price", "taxable", "active", "created_at", "updated_at"], "entry_fields": ["item_name", "unit_price", "taxable", "active"], "required_only": False},
    "paymees": {"model": Paymee, "form": PaymeeForm, "title": "Receivables", "icon": "PY", "fields": ["invoice", "client", "total_amount", "amount_paid", "balance_amount", "due_date", "aging_days", "status"], "detail_fields": ["invoice", "client", "total_amount", "amount_paid", "balance_amount", "overpaid_amount", "due_date", "last_payment_date", "aging_days", "payment_terms", "currency", "status", "created_at", "updated_at"], "entry_fields": ["invoice", "payment_terms", "currency"], "required_only": False, "managed_table": True, "managed_message": "Receivables are captured automatically from payment records."},
    "payments": {"model": Payment, "form": PaymentForm, "title": "Payments", "icon": "PM", "fields": ["invoice", "payment_date", "amount", "payment_method"]},
    "budgets": {"model": Budget, "form": BudgetForm, "title": "Budgets", "icon": "BG", "fields": ["budget_code", "budget_title", "department", "fiscal_year", "requested_amount", "allocated_amount", "spent_amount", "remaining_amount", "utilization_rate", "approval_status"], "detail_fields": ["budget_code", "budget_title", "department", "budget_category", "fiscal_year", "period_start", "period_end", "requested_amount", "allocated_amount", "spent_amount", "remaining_amount", "utilization_rate", "low_balance_threshold", "requested_by", "verified_by", "approved_by", "verification_status", "approval_status", "approved_at", "created_at", "updated_at"], "entry_fields": ["budget_title", "department", "budget_category", "fiscal_year", "period_start", "period_end", "requested_amount", "allocated_amount", "low_balance_threshold", "requested_by", "verified_by", "approved_by", "verification_status", "approval_status"], "required_only": False},
    "expenses": {"model": Expense, "form": ExpenseForm, "title": "Expenses", "icon": "EX", "fields": ["expense_code", "requisition_title", "budget", "requested_amount", "amount", "variance_amount", "expense_date", "requested_by", "approval_status", "accountability_status"], "detail_fields": ["expense_code", "requisition_title", "budget", "category", "requested_amount", "amount", "variance_amount", "expense_date", "payment_method", "description", "receipt_reference", "requested_by", "spent_by", "verified_by", "approved_by", "verification_status", "approval_status", "status", "accountability_status", "approved_at", "accountability_due_at", "accounted_at", "recorded_at", "created_at", "updated_at"], "entry_fields": ["budget", "requisition_title", "category", "requested_amount", "expense_date", "payment_method", "requested_by", "verified_by", "approved_by", "verification_status", "approval_status", "amount", "description", "receipt_reference", "spent_by", "status"], "required_only": False},
    "suppliers": {"model": Supplier, "form": SupplierForm, "title": "Suppliers", "icon": "SP", "fields": ["supplier_code", "supplier_name", "contact_person", "phone_number", "email", "status"], "detail_fields": ["supplier_code", "supplier_name", "contact_person", "phone_number", "email", "address", "tax_identification_number", "bank_name", "bank_account_name", "bank_account_number", "status", "due_diligence_notes", "created_at", "updated_at"], "entry_fields": ["supplier_name", "contact_person", "phone_number", "email", "address", "tax_identification_number", "bank_name", "bank_account_name", "bank_account_number", "status", "due_diligence_notes"], "required_only": False},
    "procurement-requisitions": {"model": ProcurementRequisition, "form": ProcurementRequisitionForm, "title": "Procurement Requisitions", "icon": "RQ", "fields": ["requisition_number", "title", "budget", "department", "required_date", "estimated_amount", "requested_by", "approval_assigned_to", "preferred_supplier", "status"], "detail_fields": ["requisition_number", "budget", "title", "category", "description", "requested_by", "approval_assigned_to", "viewer", "preferred_supplier", "department", "required_date", "estimated_amount", "approved_amount", "justification", "supplier_contacted_at", "status", "created_at", "updated_at"], "entry_fields": ["budget", "title", "category", "description", "requested_by", "approval_assigned_to", "viewer", "preferred_supplier", "department", "required_date", "estimated_amount", "justification", "status"], "required_only": False},
    "procurement-approvals": {"model": ProcurementApproval, "form": ProcurementApprovalForm, "title": "Procurement Approvals", "icon": "PA", "fields": ["requisition", "approved_by", "decision", "approved_amount", "decided_at"], "detail_fields": ["requisition", "approved_by", "decision", "approved_amount", "comments", "decided_at", "created_at", "updated_at"], "entry_fields": ["requisition", "approved_by", "decision", "approved_amount", "comments"], "required_only": False},
    "proforma-item-prices": {"model": SupplierProformaItemPrice, "form": SupplierProformaItemPriceForm, "title": "Proforma Item Prices", "icon": "PP", "fields": ["item_name", "unit_price", "tax_rate", "discount_allowed", "active"], "detail_fields": ["item_name", "unit_price", "tax_rate", "discount_allowed", "active", "created_at", "updated_at"], "entry_fields": ["item_name", "unit_price", "tax_rate", "discount_allowed", "active"], "required_only": False},
    "supplier-proformas": {"model": SupplierProformaInvoice, "form": SupplierProformaInvoiceForm, "title": "Supplier Proformas", "icon": "PF", "fields": ["proforma_number", "requisition", "supplier", "proforma_date", "subtotal_amount", "tax_amount", "total_amount", "status", "purchase_order"], "detail_fields": ["proforma_number", "requisition", "supplier", "proforma_date", "valid_until", "subtotal_amount", "tax_amount", "total_amount", "payment_terms", "status", "purchase_order", "created_at", "updated_at"], "entry_fields": ["requisition", "supplier", "proforma_date", "valid_until", "payment_terms", "status"], "required_only": False},
    "purchase-orders": {"model": PurchaseOrder, "form": PurchaseOrderForm, "title": "Purchase Orders", "icon": "PO", "fields": ["po_number", "requisition", "supplier", "order_date", "expected_delivery_date", "total_amount", "status"], "detail_fields": ["po_number", "requisition", "supplier", "order_date", "expected_delivery_date", "currency", "subtotal_amount", "tax_amount", "total_amount", "payment_terms", "status", "prepared_by", "notes", "created_at", "updated_at"], "entry_fields": ["requisition", "supplier", "order_date", "expected_delivery_date", "currency", "subtotal_amount", "tax_amount", "payment_terms", "status", "prepared_by", "notes"], "required_only": False},
    "goods-received-notes": {"model": GoodsReceivedNote, "form": GoodsReceivedNoteForm, "title": "Goods Received Notes", "icon": "GR", "fields": ["grn_number", "purchase_order", "received_date", "received_by", "quantity_summary", "status"], "detail_fields": ["grn_number", "purchase_order", "received_date", "received_by", "delivery_note_number", "quantity_summary", "condition_notes", "status", "created_at", "updated_at"], "entry_fields": ["purchase_order", "received_date", "received_by", "delivery_note_number", "quantity_summary", "condition_notes", "status"], "required_only": False},
    "supplier-invoices": {"model": SupplierInvoice, "form": SupplierInvoiceForm, "title": "Supplier Invoices", "icon": "SI", "fields": ["invoice_number", "supplier", "purchase_order", "due_date", "total_amount", "amount_paid", "balance_amount", "status"], "detail_fields": ["invoice_number", "purchase_order", "goods_received_note", "supplier", "invoice_date", "due_date", "subtotal_amount", "tax_amount", "total_amount", "amount_paid", "balance_amount", "status", "approved_by", "notes", "created_at", "updated_at"], "entry_fields": ["invoice_number", "purchase_order", "goods_received_note", "supplier", "invoice_date", "due_date", "subtotal_amount", "tax_amount", "status", "approved_by", "notes"], "required_only": False},
    "supplier-payments": {"model": SupplierPayment, "form": SupplierPaymentForm, "title": "Supplier Payments", "icon": "PP", "fields": ["supplier_invoice", "payment_date", "amount", "payment_method", "approval_status", "payment_status", "paid_by"], "detail_fields": ["supplier_invoice", "payment_date", "amount", "payment_method", "transaction_ref", "approval_status", "payment_status", "approved_by", "paid_by", "paid_at", "remarks", "created_at", "updated_at"], "entry_fields": ["supplier_invoice", "payment_date", "amount", "payment_method", "transaction_ref", "approval_status", "approved_by", "paid_by", "remarks"], "required_only": False},
    "procurement-notifications": {"model": ProcurementNotification, "form": None, "title": "Procurement Notifications", "icon": "PN", "fields": ["recipient", "recipient_group", "notification_type", "message", "status", "notified_at"], "detail_fields": ["recipient", "recipient_group", "notification_type", "related_model", "related_object_id", "message", "status", "notified_at", "created_at", "updated_at"], "managed_table": True, "managed_message": "Procurement notifications are created automatically by workflow actions."},
}

MODULE_GROUPS = {
    "payroll": {
        "title": "Payroll",
        "icon": "PR",
        "items": ["salaries", "advances", "payroll-deductions"],
    },
    "procurement": {
        "title": "Procurement",
        "icon": "PC",
        "items": ["suppliers", "procurement-requisitions", "procurement-approvals", "proforma-item-prices", "supplier-proformas", "purchase-orders", "goods-received-notes", "supplier-invoices", "supplier-payments", "procurement-notifications"],
    },
}

DEPARTMENTS = [
    {"name": "Operations", "items": ["clients", "contracts", "sites", "shifts", "assets", "asset-assignments", "incidents", "deployments", "attendance"]},
    {"name": "Human Resources", "items": ["employees", "training", "leaves", "disciplinary-actions", "performance-evaluations", "documents"]},
    {"name": "Finance", "items": ["payroll", "procurement", "invoices", "paymees", "payments", "budgets", "expenses"]},
]

def get_config(model_name):
    try:
        return MODEL_CONFIGS[model_name]
    except KeyError as exc:
        raise Http404("The requested section does not exist.") from exc


def managed_table_message(config):
    return config.get("managed_message", f"{config['title']} is managed automatically.")


def sync_receivables_from_payments():
    invoices = Invoice.objects.exclude(status="cancelled").select_related("client", "contract")
    for invoice in invoices:
        receivable, _created = Paymee.objects.get_or_create(invoice=invoice)
        receivable.save(update_fields=[
            "client",
            "total_amount",
            "amount_paid",
            "due_date",
            "last_payment_date",
            "status",
            "updated_at",
        ])
def format_display_value(value):
    if value in (None, ""):
        return "-"
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, Decimal):
        places = max(-value.as_tuple().exponent, 0)
        return f"{value:,.{places}f}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return value


def get_field_value(obj, field_name):
    display_method = getattr(obj, f"get_{field_name}_display", None)
    if callable(display_method):
        return format_display_value(display_method())
    value = getattr(obj, field_name)
    if callable(value):
        value = value()
    return format_display_value(value)

def get_field_label(model, field_name):
    custom_labels = {
        "ledger_installment_amount": "Installment Limit",
        "advance_recovery": "Salary Advance Recovery",
        "loan_deduction": "Loan Deduction",
        "medical_deduction": "Medical Deduction",
        "other_payroll_deductions": "Other Payroll Deductions",
        "ledger_advance_balance": "Balance",
        "standard_shifts": "Standard Shifts",
        "overtime_shifts": "Overtime Shifts",
        "overtime_daily_rate": "Overtime Daily Rate",
        "region": "Deployment Area",
        "shift_summary": "Shift Type",
        "account_status_display": "Account Access",
    }
    if field_name in custom_labels:
        return custom_labels[field_name]
    try:
        label = str(model._meta.get_field(field_name).verbose_name)
        return label[:1].upper() + label[1:]
    except Exception:
        return field_name.replace("_", " ").title()

def build_rows(objects, fields):
    return [
        {"object": obj, "pk": obj.pk, "values": [get_field_value(obj, field) for field in fields]}
        for obj in objects
    ]


def csv_import_fields(config):
    form = config["form"](required_only=config.get("required_only", True))
    field_names = config.get("entry_fields") or list(form.fields.keys())
    return [field_name for field_name in field_names if field_name in form.fields]


def normalize_csv_row(row):
    return {str(key).strip(): (value.strip() if isinstance(value, str) else value) for key, value in row.items() if key}


def prepare_csv_form_data(form_class, config, row):
    form = form_class(required_only=config.get("required_only", True))
    data = {}
    for field_name in csv_import_fields(config):
        value = row.get(field_name, "")
        field = form.fields[field_name]
        if getattr(field.widget, "allow_multiple_selected", False):
            data[field_name] = [item.strip() for item in re.split(r"[;,]", value or "") if item.strip()]
        else:
            data[field_name] = value
    return data


def build_required_only_form(form_class):
    class RequiredOnlyForm(form_class):
        def __init__(self, *args, **kwargs):
            kwargs["required_only"] = True
            super().__init__(*args, **kwargs)

    return RequiredOnlyForm


def build_create_formset(config):
    form_class = config["form"] if not config.get("required_only", True) else build_required_only_form(config["form"])
    kwargs = {
        "form": form_class,
        "extra": 1,
        "can_delete": False,
    }
    if config.get("entry_fields"):
        kwargs["fields"] = config["entry_fields"]
    return modelformset_factory(config["model"], **kwargs)


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

def build_navigation(user=None, active_model=None):
    departments = []
    for department in DEPARTMENTS:
        items = []
        for item_name in department["items"]:
            if not can_access_model(user, item_name):
                continue
            if item_name in MODULE_GROUPS:
                group = MODULE_GROUPS[item_name]
                items.append(
                    {
                        "name": item_name,
                        "title": group["title"],
                        "icon": group["icon"],
                        "active": active_model == item_name or active_model in group["items"],
                    }
                )
                continue
            config = get_config(item_name)
            items.append(
                {
                    "name": item_name,
                    "title": config["title"],
                    "icon": config["icon"],
                    "active": item_name == active_model,
                }
            )
        if not items:
            continue
        department_active = any(item["active"] for item in items)
        departments.append({"name": department["name"], "items": items, "active": department_active})
    return departments


def base_context(user=None, active_model=None):
    return {"sidebar_departments": build_navigation(user, active_model), "active_model": active_model}


def render_page(request, template, context=None, active_model=None):
    page_context = base_context(request.user, active_model)
    page_context["is_popup_form"] = request.GET.get("popup") == "1"
    if context:
        page_context.update(context)
    return render(request, template, page_context)


def build_department_cards(user=None):
    departments = []
    for department in DEPARTMENTS:
        items = []
        total = 0
        for item_name in department["items"]:
            if not can_access_model(user, item_name):
                continue
            if item_name in MODULE_GROUPS:
                group = MODULE_GROUPS[item_name]
                count = sum(get_config(child_name)["model"].objects.count() for child_name in group["items"])
                title = group["title"]
                icon = group["icon"]
            else:
                config = get_config(item_name)
                count = config["model"].objects.count()
                title = config["title"]
                icon = config["icon"]
            total += count
            items.append({"name": item_name, "title": title, "icon": icon, "count": count})
        if items:
            departments.append({"name": department["name"], "items": items, "total": total})
    return departments





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

def public_site_context():
    today = timezone.localdate()
    try:
        adverts = [advert for advert in WebsiteAdvertisement.objects.all() if advert.is_current]
        events = CompanyEvent.objects.filter(is_public=True).order_by("-event_date")[:6]
        resources = WebsiteResource.objects.filter(is_public=True).order_by("resource_type", "title")[:8]
        links = AssociatedLink.objects.filter(is_public=True).order_by("category", "title")[:8]
        jobs = JobPosting.objects.filter(is_active=True, is_online=True).order_by("deadline", "-posted_at")[:5]
    except DatabaseError:
        adverts = []
        events = []
        resources = []
        links = []
        jobs = []
    return {
        "adverts": adverts,
        "events": events,
        "resources": resources,
        "associated_links": links,
        "open_jobs": [job for job in jobs if job.is_open],
        "today": today,
    }


def public_render(request, template, context=None):
    page_context = public_site_context()
    if context:
        page_context.update(context)
    return render(request, template, page_context)


def public_home(request):
    try:
        stats = {
            "clients": Client.objects.count(),
            "sites": Site.objects.count(),
            "guards": Employee.objects.filter(role__in=("guard", "supervisor"), status="active").count(),
            "regions": Region.objects.count(),
        }
    except DatabaseError:
        stats = {"clients": 0, "sites": 0, "guards": 0, "regions": 0}
    context = {
        "title": "Home",
        "active_public": "home",
        "stats": stats,
    }
    return public_render(request, "webCom/public_home.html", context)


def system_health(request):
    errors = getattr(settings, "VERCEL_CONFIGURATION_ERRORS", [])
    if errors:
        return JsonResponse(
            {"ok": False, "configuration": "error", "errors": errors},
            status=503,
        )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError as exc:
        return JsonResponse(
            {"ok": False, "database": "error", "error_type": exc.__class__.__name__},
            status=503,
        )
    return JsonResponse({"ok": True, "database": "ok"})

def public_page(request, page):
    page_data = {
        "who-we-are": {
            "title": "Who We Are",
            "active_public": "who-we-are",
            "heading": "A disciplined security partner built for Ugandan operations.",
            "body": "Turyans Security Company provides trained personnel, site supervision, incident reporting, and operational accountability for clients that need dependable protection.",
        },
        "what-we-do": {
            "title": "What We Do",
            "active_public": "what-we-do",
            "heading": "Security services that connect people, sites, assets, and evidence.",
            "body": "We support guarding, deployment planning, patrol tracking, attendance control, incident response, asset handling, and finance-backed service documentation.",
        },
        "where-we-work": {
            "title": "Where We Work",
            "active_public": "where-we-work",
            "heading": "Coverage across client sites and deployment regions.",
            "body": "Our operations are organized around regions, client sites, shifts, and supervisor accountability so teams can be placed where clients need them most.",
        },
        "impact": {
            "title": "Impact",
            "active_public": "impact",
            "heading": "Safer premises, clearer records, and faster operational decisions.",
            "body": "The company system connects deployments, attendance, incidents, training, assets, invoices, payments, budgets, and accountability reports into one working record.",
        },
        "resources": {
            "title": "Resources",
            "active_public": "resources",
            "heading": "Resources and associated links for clients, applicants, and partners.",
            "body": "Find company updates, client guidance, application information, and associated links maintained by the team.",
        },
    }
    if page not in page_data:
        raise Http404("The requested website page does not exist.")
    return public_render(request, "webCom/public_page.html", page_data[page])


def careers(request):
    jobs = [job for job in JobPosting.objects.filter(is_active=True, is_online=True).order_by("deadline", "-posted_at") if job.is_open]
    context = {
        "title": "Careers",
        "active_public": "careers",
        "jobs": jobs,
    }
    return public_render(request, "webCom/careers.html", context)


def job_detail(request, pk):
    job = get_object_or_404(JobPosting, pk=pk, is_active=True, is_online=True)
    if not job.is_open:
        messages.info(request, "This job posting is no longer open for online applications.")
        return redirect("webcom:careers")
    form = PublicJobApplicationForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        application = form.save(commit=False)
        application.job = job
        application.application_mode = "online"
        application.save()
        messages.success(request, "Your application has been received. Our team will review it and contact shortlisted candidates.")
        return redirect("webcom:job_detail", pk=job.pk)
    context = {
        "title": job.title,
        "active_public": "careers",
        "job": job,
        "form": form,
    }
    return public_render(request, "webCom/job_detail.html", context)
def dashboard_percent(value, total):
    if not total:
        return 0
    return min(100, int((Decimal(value) / Decimal(total)) * 100))


def money_total(model, field):
    return model.objects.aggregate(total=Sum(field))["total"] or Decimal("0.00")


def pie_chart_style(items):
    colors = ["#0f766e", "#2563eb", "#16803d", "#b45309", "#b91c1c", "#64748b"]
    for index, item in enumerate(items):
        item["color"] = colors[index % len(colors)]
        item["percent"] = 0
    total = sum(item["value"] for item in items)
    if not total:
        return "#e8eef5"
    stops = []
    cursor = 0
    for index, item in enumerate(items):
        percent = dashboard_percent(item["value"], total)
        end = 100 if index == len(items) - 1 else min(100, cursor + percent)
        color = item["color"]
        stops.append(f"{color} {cursor}% {end}%")
        item["percent"] = max(0, end - cursor)
        cursor = end
    return f"conic-gradient({', '.join(stops)})"


def trend_points(values, width=320, height=120, padding=14):
    if not values:
        return ""
    high = max(values) or 1
    usable_width = width - (padding * 2)
    usable_height = height - (padding * 2)
    step = usable_width / max(len(values) - 1, 1)
    points = []
    for index, value in enumerate(values):
        x = padding + (step * index)
        y = padding + (usable_height - ((float(value) / float(high)) * usable_height))
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def daily_count_rows(model, date_field, days=7):
    today = timezone.localdate()
    rows = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        lookup = {f"{date_field}__date": day}
        if date_field.endswith("_date") or date_field in ("invoice_date", "payment_date"):
            lookup = {date_field: day}
        rows.append({"label": day.strftime("%b %d"), "value": model.objects.filter(**lookup).count()})
    values = [row["value"] for row in rows]
    return {"rows": rows, "points": trend_points(values), "max": max(values) if values else 0}


def home(request):
    if user_allowed_models(request.user) is not None:
        return redirect_to_first_allowed_module(request.user)
    dashboard_departments = build_department_cards(request.user)
    total_records = sum(department["total"] for department in dashboard_departments)
    active_clients = Client.objects.filter(contracts__contract_status="active").distinct().count()
    employee_count = Employee.objects.count()
    active_employee_count = Employee.objects.filter(status="active").count()
    open_incidents = Incident.objects.exclude(status__in=("resolved", "closed")).count()
    high_risk_incidents = Incident.objects.filter(severity_level__in=("high", "critical")).exclude(status__in=("resolved", "closed")).count()
    active_deployments = Deployment.objects.filter(status="active").count()
    pending_leaves = Leave.objects.filter(approval_status="pending").count()
    pending_advances = Advance.objects.filter(approval_status="pending").count()
    draft_invoices = Invoice.objects.filter(status="draft").count()
    invoice_total = money_total(Invoice, "total_amount")
    payment_total = money_total(Payment, "amount")
    outstanding_total = max(invoice_total - payment_total, Decimal("0.00"))
    collection_rate = dashboard_percent(payment_total, invoice_total)
    budget_allocated = money_total(Budget, "allocated_amount")
    budget_spent = money_total(Budget, "spent_amount")
    budget_remaining = max(budget_allocated - budget_spent, Decimal("0.00"))
    budget_rate = dashboard_percent(budget_spent, budget_allocated)

    summary_cards = [
        {"label": "Total Records", "value": total_records, "accent": "teal", "caption": "All managed records"},
        {"label": "Active Clients", "value": active_clients, "accent": "green", "caption": "Clients with active contracts"},
        {"label": "Active Staff", "value": active_employee_count, "accent": "blue", "caption": f"{employee_count} employees recorded"},
        {"label": "Open Incidents", "value": open_incidents, "accent": "red", "caption": f"{high_risk_incidents} high risk"},
    ]
    analysis_cards = [
        {"label": "Collection Rate", "value": f"{collection_rate}%", "note": f"UGX {payment_total:,.0f} collected from UGX {invoice_total:,.0f} invoiced."},
        {"label": "Budget Use", "value": f"{budget_rate}%", "note": f"UGX {budget_spent:,.0f} spent from UGX {budget_allocated:,.0f} allocated."},
        {"label": "Deployment Coverage", "value": active_deployments, "note": f"{active_deployments} active deployments across {Site.objects.count()} client sites."},
    ]

    department_pie = [{"label": department["name"], "value": department["total"]} for department in dashboard_departments]
    department_pie_style = pie_chart_style(department_pie)

    severity_pie = []
    for key, label in Incident.SEVERITY_LEVEL_CHOICES:
        severity_pie.append({"label": label, "value": Incident.objects.filter(severity_level=key).count()})
    severity_pie_style = pie_chart_style(severity_pie)

    cluster_max = max(invoice_total, payment_total, outstanding_total, budget_allocated, budget_spent, budget_remaining, Decimal("1.00"))
    clustered_bars = [
        {
            "label": "Finance",
            "bars": [
                {"label": "Invoiced", "value": invoice_total, "height": dashboard_percent(invoice_total, cluster_max), "tone": "blue"},
                {"label": "Collected", "value": payment_total, "height": dashboard_percent(payment_total, cluster_max), "tone": "green"},
                {"label": "Outstanding", "value": outstanding_total, "height": dashboard_percent(outstanding_total, cluster_max), "tone": "red"},
            ],
        },
        {
            "label": "Budget",
            "bars": [
                {"label": "Allocated", "value": budget_allocated, "height": dashboard_percent(budget_allocated, cluster_max), "tone": "blue"},
                {"label": "Spent", "value": budget_spent, "height": dashboard_percent(budget_spent, cluster_max), "tone": "amber"},
                {"label": "Remaining", "value": budget_remaining, "height": dashboard_percent(budget_remaining, cluster_max), "tone": "green"},
            ],
        },
    ]

    trend_charts = [
        {"title": "Incident Trend", "subtitle": "New incidents over 7 days", **daily_count_rows(Incident, "created_at")},
        {"title": "Invoice Trend", "subtitle": "Invoices raised over 7 days", **daily_count_rows(Invoice, "invoice_date")},
        {"title": "Payment Trend", "subtitle": "Payments received over 7 days", **daily_count_rows(Payment, "payment_date")},
    ]

    context = {
        "dashboard_departments": dashboard_departments,
        "summary_cards": summary_cards,
        "analysis_cards": analysis_cards,
        "department_pie": department_pie,
        "department_pie_style": department_pie_style,
        "severity_pie": severity_pie,
        "severity_pie_style": severity_pie_style,
        "clustered_bars": clustered_bars,
        "trend_charts": trend_charts,
    }
    return render_page(request, "webCom/home.html", context)



def sync_salary_table():
    for employee in Employee.objects.all():
        salary, _created = Salary.objects.get_or_create(
            employee=employee,
            defaults={"basic_salary": Decimal("0.00")},
        )
        salary.update_basic_salary()
        salary.save(update_fields=["basic_salary", "updated_at"])


def payroll_dashboard(request):
    sync_salary_table()
    salaries = Salary.objects.select_related("employee").all().order_by("employee__first_name", "employee__last_name")
    advances = Advance.objects.select_related("employee", "approved_by").all().order_by("-created_at")
    deductions = PayrollDeduction.objects.select_related("employee").filter(status="active").order_by("employee__first_name", "employee__last_name", "category")
    salary_rows = list(salaries[:8])
    advance_rows = list(advances[:8])
    deduction_rows = list(deductions[:8])
    gross_payroll = sum((salary.gross_pay for salary in salaries), Decimal("0.00"))
    net_payroll = sum((salary.net_pay for salary in salaries), Decimal("0.00"))
    outstanding_advances = sum((advance.balance for advance in advances), Decimal("0.00"))
    pending_advances = advances.filter(approval_status="pending").count()
    loan_total = sum((salary.loan_deduction for salary in salaries), Decimal("0.00"))
    medical_total = sum((salary.medical_deduction for salary in salaries), Decimal("0.00"))

    context = {
        "title": "Payroll",
        "module_cards": [
            {"label": "Salary Records", "value": salaries.count(), "caption": "Employee salary profiles", "model_name": "salaries", "accent": "blue"},
            {"label": "Advances", "value": advances.count(), "caption": f"{pending_advances} pending approval", "model_name": "advances", "accent": "amber"},
            {"label": "Gross Payroll", "value": f"UGX {gross_payroll:,.0f}", "caption": "Current salary period", "model_name": "salaries", "accent": "green"},
            {"label": "Outstanding Advances", "value": f"UGX {outstanding_advances:,.0f}", "caption": "Recoverable balances", "model_name": "advances", "accent": "red"},
            {"label": "Loan Deductions", "value": f"UGX {loan_total:,.0f}", "caption": "Active loan deductions", "model_name": "payroll-deductions", "accent": "amber"},
            {"label": "Medical Deductions", "value": f"UGX {medical_total:,.0f}", "caption": "Active medical deductions", "model_name": "payroll-deductions", "accent": "blue"},
        ],
        "salary_rows": salary_rows,
        "advance_rows": advance_rows,
        "deduction_rows": deduction_rows,
        "gross_payroll": gross_payroll,
        "net_payroll": net_payroll,
        "outstanding_advances": outstanding_advances,
        "loan_total": loan_total,
        "medical_total": medical_total,
    }
    return render_page(request, "webCom/payroll_dashboard.html", context, "payroll")

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

    return list(
        site.guards.filter(
            role__in=("guard", "supervisor"),
            status="active",
        ).order_by("employee_number", "first_name", "last_name")
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
    available_guards = (
        Employee.objects.filter(
            role__in=("guard", "supervisor"),
            status="active",
            deployment_areas__region=site.region,
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

def refresh_budget_audit_notifications():
    """Refresh budget accountability alerts for screens that audit finance records."""
    approved_budgets = Budget.objects.filter(approval_status="approved")
    for budget in approved_budgets:
        budget.save(update_fields=["spent_amount", "updated_at"])


def budget_report(request):
    require_view_access(request, "budget_report")
    refresh_budget_audit_notifications()
    budgets = Budget.objects.select_related(
        "requested_by",
        "verified_by",
        "approved_by",
    ).order_by("-fiscal_year", "department", "budget_title")
    report_rows = []
    total_requested = Decimal("0.00")
    total_allocated = Decimal("0.00")
    total_spent = Decimal("0.00")
    total_remaining = Decimal("0.00")
    for budget in budgets:
        total_requested += budget.requested_amount
        total_allocated += budget.allocated_amount
        total_spent += budget.spent_amount
        total_remaining += budget.remaining_amount
        report_rows.append({
            "budget": budget,
            "expense_count": budget.expenses.exclude(status="rejected").count(),
        })
    context = {
        "title": "Budget Report",
        "report_rows": report_rows,
        "total_requested": total_requested,
        "total_allocated": total_allocated,
        "total_spent": total_spent,
        "total_remaining": total_remaining,
    }
    return render_page(request, "webCom/budget_report.html", context, "budgets")


budget_notifications = budget_report
def refresh_expense_accountability_notifications():
    approved_expenses = Expense.objects.filter(approval_status="approved")
    for expense in approved_expenses:
        expense.save(update_fields=["accountability_status", "accounted_at", "updated_at"])


def expense_report(request):
    require_view_access(request, "expense_report")
    refresh_expense_accountability_notifications()
    budgets = Budget.objects.prefetch_related("expenses").order_by("-fiscal_year", "department", "budget_title")
    report_rows = []
    total_budget_allocated = Decimal("0.00")
    total_requested = Decimal("0.00")
    total_accounted = Decimal("0.00")
    total_variance = Decimal("0.00")
    pending_count = 0
    overdue_count = 0
    for budget in budgets:
        budget_expenses = budget.expenses.exclude(status="rejected").order_by("expense_date", "expense_id")
        total_budget_allocated += budget.allocated_amount
        for expense in budget_expenses:
            total_requested += expense.requested_amount
            total_accounted += expense.amount
            total_variance += expense.variance_amount
            if expense.accountability_status in ("not_due", "pending_accountability"):
                pending_count += 1
            if expense.accountability_status == "overdue":
                overdue_count += 1
            report_rows.append({
                "budget": budget,
                "expense": expense,
            })
    context = {
        "title": "Expense Accountability Report",
        "report_rows": report_rows,
        "total_budget_allocated": total_budget_allocated,
        "total_requested": total_requested,
        "total_accounted": total_accounted,
        "total_variance": total_variance,
        "pending_count": pending_count,
        "overdue_count": overdue_count,
    }
    return render_page(request, "webCom/expense_report.html", context, "expenses")


expense_notifications = expense_report


def aging_months(aging_days):
    if aging_days <= 0:
        return 1
    return max(1, int((aging_days + 29) / 30))


def client_customer_code(client):
    return f"CL{client.client_id:06d}"


def client_area(client):
    areas = list(
        Region.objects.filter(sites__client=client)
        .distinct()
        .order_by("region_name")
        .values_list("region_name", flat=True)
    )
    return ", ".join(areas) if areas else "-"


def finance_debt_collector():
    collector = (
        Employee.objects.filter(status="active")
        .filter(Q(role="finance_officer") | Q(department="finance"))
        .order_by("first_name", "last_name")
        .first()
    )
    return str(collector) if collector else "-"


def aging_report(request):
    require_view_access(request, "aging_report")
    sync_receivables_from_payments()
    today = timezone.localdate()
    collector_name = finance_debt_collector()
    receivables = (
        Paymee.objects.select_related("invoice", "client", "invoice__contract")
        .exclude(status__in=("paid", "overpaid", "cancelled"))
        .order_by("client__client_name", "due_date", "invoice_id")
    )
    grouped = {}
    total_receipts = Decimal("0.00")
    total_invoices = Decimal("0.00")
    total_balance_due = Decimal("0.00")

    for receivable in receivables:
        balance = receivable.balance_amount
        if balance <= 0:
            continue
        client = receivable.client
        row = grouped.setdefault(
            client.pk,
            {
                "customer_code": client_customer_code(client),
                "customer_name": client.client_name,
                "area": client_area(client),
                "manager": client.contact_person or "-",
                "debt_collector": collector_name,
                "receipts": Decimal("0.00"),
                "invoices": Decimal("0.00"),
                "balance_due": Decimal("0.00"),
                "months": 1,
                "max_aging_days": 0,
            },
        )
        row["receipts"] += receivable.amount_paid
        row["invoices"] += receivable.total_amount
        row["balance_due"] += balance
        row["max_aging_days"] = max(row["max_aging_days"], receivable.aging_days)
        row["months"] = aging_months(row["max_aging_days"])
        total_receipts += receivable.amount_paid
        total_invoices += receivable.total_amount
        total_balance_due += balance

    report_rows = sorted(grouped.values(), key=lambda row: row["customer_name"].lower())
    context = {
        "title": "Receivables Aging Report",
        "report_date": today,
        "company_name": getattr(settings, "COMPANY_NAME", "TURYANS SECURITY COMPANY (U) LIMITED"),
        "report_rows": report_rows,
        "total_receipts": total_receipts,
        "total_invoices": total_invoices,
        "total_balance_due": total_balance_due,
    }
    return render_page(request, "webCom/aging_report.html", context, "paymees")

def document_status(document, today=None):
    today = today or timezone.localdate()
    if not document.expiry_date:
        return "No Expiry"
    if document.expiry_date < today:
        return "Expired"
    if document.expiry_date <= today + timedelta(days=30):
        return "Expiring Soon"
    return "Valid"


def document_register_context():
    today = timezone.localdate()
    documents = Document.objects.select_related("employee").order_by("employee__first_name", "employee__last_name", "doc_type", "expiry_date")
    employee_groups = []
    grouped = {}
    expired_count = 0
    expiring_count = 0
    for document in documents:
        status = document_status(document, today)
        if status == "Expired":
            expired_count += 1
        elif status == "Expiring Soon":
            expiring_count += 1
        employee = document.employee
        group = grouped.setdefault(employee.pk, {"employee": employee, "documents": []})
        group["documents"].append({"object": document, "status": status})
    employee_groups = list(grouped.values())
    return {
        "title": "Documents",
        "model_name": "documents",
        "documents": documents,
        "employee_groups": employee_groups,
        "document_count": documents.count(),
        "employee_count": len(employee_groups),
        "expired_count": expired_count,
        "expiring_count": expiring_count,
    }

PROCUREMENT_API_MODELS = {
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
}
API_DEFAULT_PAGE_SIZE = 50
API_MAX_PAGE_SIZE = 200


def api_error(message, status=400, code="bad_request"):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def parse_positive_int(value, default, maximum=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < 1:
        return default
    if maximum is not None:
        return min(parsed, maximum)
    return parsed


def model_field_for(model, field_name):
    try:
        return model._meta.get_field(field_name)
    except Exception:
        return None


def api_scalar(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def api_attribute_value(obj, field_name):
    value = getattr(obj, field_name)
    if callable(value):
        value = value()
    field = model_field_for(obj.__class__, field_name)
    if field is not None and getattr(field, "many_to_one", False):
        if value is None:
            return None
        return {"id": value.pk, "display": str(value)}
    if isinstance(value, models.Model):
        return {"id": value.pk, "display": str(value)}
    return api_scalar(value)


def api_field_schema(model, field_name):
    field = model_field_for(model, field_name)
    label = get_field_label(model, field_name)
    if field is None:
        return {"name": field_name, "label": label, "type": "computed", "filterable": False}
    choices = [{"value": value, "label": label} for value, label in (getattr(field, "choices", None) or [])]
    return {
        "name": field_name,
        "label": label,
        "type": field.get_internal_type(),
        "required": not getattr(field, "blank", True) and not getattr(field, "null", True),
        "filterable": field.get_internal_type() in {"CharField", "EmailField", "DateField", "DateTimeField", "BooleanField"} or getattr(field, "many_to_one", False),
        "choices": choices,
    }


def requested_api_fields(request, config, detail=False):
    default_fields = config.get("detail_fields" if detail else "fields", config.get("fields", []))
    requested = [item.strip() for item in request.GET.get("fields", "").split(",") if item.strip()]
    if not requested:
        return default_fields
    allowed = set(config.get("detail_fields", [])) | set(config.get("fields", []))
    return [field for field in requested if field in allowed]


def procurement_api_payload(request, obj, fields):
    attributes = {field: api_attribute_value(obj, field) for field in fields}
    detail_url = request.build_absolute_uri(f"/api/procurement/{obj._api_model_name}/{obj.pk}/") if hasattr(obj, "_api_model_name") else None
    links = {"self": detail_url} if detail_url else {}
    if getattr(obj, "_api_model_name", "") == "purchase-orders":
        links["lpo_report"] = request.build_absolute_uri(f"/purchase-orders/{obj.pk}/lpo/")
    return {
        "id": obj.pk,
        "display": str(obj),
        "attributes": attributes,
        "links": links,
    }


def searchable_fields(model):
    names = []
    for field in model._meta.fields:
        if field.get_internal_type() in {"CharField", "TextField", "EmailField"}:
            names.append(field.name)
    return names


def apply_procurement_api_filters(queryset, request, config):
    model = config["model"]
    query = request.GET.get("q", "").strip()
    if query:
        search_query = Q()
        for field_name in searchable_fields(model):
            search_query |= Q(**{f"{field_name}__icontains": query})
        if search_query:
            queryset = queryset.filter(search_query)

    status = request.GET.get("status", "").strip()
    if status and model_field_for(model, "status") is not None:
        queryset = queryset.filter(status=status)

    supplier = request.GET.get("supplier", "").strip()
    if supplier and model_field_for(model, "supplier") is not None:
        queryset = queryset.filter(supplier_id=supplier)

    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    date_field = next((name for name in ("order_date", "required_date", "proforma_date", "invoice_date", "payment_date", "received_date", "created_at") if model_field_for(model, name) is not None), None)
    if date_field and date_from:
        queryset = queryset.filter(**{f"{date_field}__gte": date_from})
    if date_field and date_to:
        queryset = queryset.filter(**{f"{date_field}__lte": date_to})

    return queryset


def procurement_api_metadata(request, model_name, config, fields):
    return {
        "module": model_name,
        "title": config["title"],
        "fields": [api_field_schema(config["model"], field) for field in fields],
        "filters": {
            "q": "Search text fields",
            "status": "Exact workflow status where available",
            "supplier": "Supplier primary key where available",
            "date_from": "Inclusive start date on the module date field",
            "date_to": "Inclusive end date on the module date field",
            "fields": "Comma-separated response fields",
            "page": "Page number",
            "page_size": f"Records per page, max {API_MAX_PAGE_SIZE}",
        },
        "links": {
            "list": request.build_absolute_uri(f"/api/procurement/{model_name}/"),
        },
    }


def procurement_api_index(request):
    require_view_access(request, "procurement_api_index")
    if request.method != "GET":
        return api_error("Only GET is supported by this API endpoint.", status=405, code="method_not_allowed")
    modules = []
    for model_name in sorted(PROCUREMENT_API_MODELS):
        config = get_config(model_name)
        modules.append({
            "name": model_name,
            "title": config["title"],
            "count": config["model"].objects.count(),
            "records_url": request.build_absolute_uri(f"/api/procurement/{model_name}/"),
            "web_url": request.build_absolute_uri(f"/{model_name}/"),
        })
    return JsonResponse({
        "api": "procurement",
        "version": "1.1",
        "security": "Authenticated staff session required. API reads are permission controlled and write workflows remain in the audited web forms.",
        "modules": modules,
    })


def procurement_api_list(request, model_name):
    require_view_access(request, "procurement_api_list")
    if request.method != "GET":
        return api_error("Only GET is supported by this API endpoint.", status=405, code="method_not_allowed")
    if model_name not in PROCUREMENT_API_MODELS:
        raise Http404("The requested procurement API module does not exist.")
    config = get_config(model_name)
    fields = requested_api_fields(request, config)
    ordering = config.get("ordering") or [config["model"]._meta.pk.name]
    queryset = config["model"].objects.all().order_by(*ordering)
    queryset = apply_procurement_api_filters(queryset, request, config)
    total_count = queryset.count()
    page_size = parse_positive_int(request.GET.get("page_size"), API_DEFAULT_PAGE_SIZE, API_MAX_PAGE_SIZE)
    page = parse_positive_int(request.GET.get("page"), 1)
    offset = (page - 1) * page_size
    objects = list(queryset[offset:offset + page_size])
    for obj in objects:
        obj._api_model_name = model_name
    next_page = page + 1 if offset + page_size < total_count else None
    previous_page = page - 1 if page > 1 else None
    return JsonResponse({
        "metadata": procurement_api_metadata(request, model_name, config, fields),
        "pagination": {
            "count": total_count,
            "page": page,
            "page_size": page_size,
            "next": request.build_absolute_uri(f"/api/procurement/{model_name}/?page={next_page}&page_size={page_size}") if next_page else None,
            "previous": request.build_absolute_uri(f"/api/procurement/{model_name}/?page={previous_page}&page_size={page_size}") if previous_page else None,
        },
        "results": [procurement_api_payload(request, obj, fields) for obj in objects],
    })


def procurement_api_detail(request, model_name, pk):
    require_view_access(request, "procurement_api_detail")
    if request.method != "GET":
        return api_error("Only GET is supported by this API endpoint.", status=405, code="method_not_allowed")
    if model_name not in PROCUREMENT_API_MODELS:
        raise Http404("The requested procurement API module does not exist.")
    config = get_config(model_name)
    obj = get_object_or_404(config["model"], pk=pk)
    obj._api_model_name = model_name
    fields = requested_api_fields(request, config, detail=True)
    return JsonResponse({
        "metadata": procurement_api_metadata(request, model_name, config, fields),
        "result": procurement_api_payload(request, obj, fields),
    })

PAYOUT_API_BATCHES = {
    "salaries": "Employee salary payouts",
    "advances": "Employee salary advance payouts",
    "supplier-payments": "Approved supplier invoice payments",
    "client-payments": "Incoming client payment references",
}


def payout_channel_details(employee=None, supplier=None, method=None):
    method = method or getattr(employee, "payout_method", "") or "bank_transfer"
    if method == "mobile_money":
        mobile_number = getattr(employee, "mobile_money_number", "") or getattr(employee, "phone_number", "")
        missing = []
        if not (getattr(employee, "mobile_money_provider", "") if employee else ""):
            missing.append("mobile_money_provider")
        if not mobile_number:
            missing.append("mobile_money_number")
        return {
            "channel": "mobile_money",
            "mobile_money": {
                "provider": getattr(employee, "mobile_money_provider", "") if employee else "",
                "number": mobile_number,
                "account_name": str(employee) if employee else "",
            },
            "bank_account": None,
            "missing_fields": missing,
        }
    bank_owner = employee or supplier
    missing = []
    bank_name = getattr(bank_owner, "bank_name", "") if bank_owner else ""
    account_name = getattr(bank_owner, "bank_account_name", "") if bank_owner else ""
    account_number = getattr(bank_owner, "bank_account_number", "") if bank_owner else ""
    if not bank_name:
        missing.append("bank_name")
    if not account_name:
        missing.append("bank_account_name")
    if not account_number:
        missing.append("bank_account_number")
    return {
        "channel": "bank_transfer",
        "bank_account": {
            "bank_name": bank_name,
            "account_name": account_name,
            "account_number": account_number,
        },
        "mobile_money": None,
        "missing_fields": missing,
    }


def payout_record(record_type, record_id, reference, direction, amount, currency, party, channel_details, status, date_value, links=None):
    missing_fields = channel_details.pop("missing_fields", [])
    return {
        "type": record_type,
        "id": record_id,
        "reference": reference,
        "direction": direction,
        "amount": str(amount or Decimal("0.00")),
        "currency": currency or "UGX",
        "party": party,
        "channel": channel_details["channel"],
        "bank_account": channel_details["bank_account"],
        "mobile_money": channel_details["mobile_money"],
        "status": status,
        "date": api_scalar(date_value),
        "ready_for_export": not missing_fields,
        "missing_fields": missing_fields,
        "links": links or {},
    }


def apply_payout_common_filters(records, request):
    method = request.GET.get("method", "").strip()
    ready = request.GET.get("ready", "").strip().lower()
    if method:
        records = [record for record in records if record["channel"] == method]
    if ready in {"1", "true", "yes"}:
        records = [record for record in records if record["ready_for_export"]]
    elif ready in {"0", "false", "no"}:
        records = [record for record in records if not record["ready_for_export"]]
    return records


def date_in_range(value, date_from, date_to):
    if not value:
        return True
    if date_from and str(value) < date_from:
        return False
    if date_to and str(value) > date_to:
        return False
    return True


def salary_payout_records(request):
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    records = []
    salaries = Salary.objects.select_related("employee").all().order_by("employee__employee_number", "salary_id")
    for salary in salaries:
        period_end = salary.period_end_date
        if not date_in_range(period_end, date_from, date_to):
            continue
        employee = salary.employee
        channel_details = payout_channel_details(employee=employee, method=employee.payout_method)
        records.append(payout_record(
            "salary",
            salary.pk,
            f"SAL-{salary.pk:06d}",
            "outbound",
            salary.net_pay,
            "UGX",
            {"id": employee.pk, "name": str(employee), "employee_number": employee.employee_number, "phone_number": employee.phone_number},
            channel_details,
            employee.status,
            period_end,
            {"self": request.build_absolute_uri(f"/salaries/{salary.pk}/"), "payslip": request.build_absolute_uri(f"/salaries/{salary.pk}/payslip/")},
        ))
    return records


def advance_payout_records(request):
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    status = request.GET.get("status", "").strip()
    advances = Advance.objects.select_related("employee").filter(approval_status="approved").order_by("-disbursement_date", "-advance_id")
    if status:
        advances = advances.filter(status=status)
    records = []
    for advance in advances:
        payout_date = advance.disbursement_date or advance.created_at.date()
        if not date_in_range(payout_date, date_from, date_to):
            continue
        employee = advance.employee
        channel_details = payout_channel_details(employee=employee, method=employee.payout_method)
        records.append(payout_record(
            "advance",
            advance.pk,
            f"ADV-{advance.pk:06d}",
            "outbound",
            advance.amount_requested,
            "UGX",
            {"id": employee.pk, "name": str(employee), "employee_number": employee.employee_number, "phone_number": employee.phone_number},
            channel_details,
            advance.status,
            payout_date,
            {"self": request.build_absolute_uri(f"/advances/{advance.pk}/")},
        ))
    return records


def supplier_payment_payout_records(request):
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    status = request.GET.get("status", "").strip()
    payments = SupplierPayment.objects.select_related("supplier_invoice", "supplier_invoice__supplier").all().order_by("-payment_date", "-supplier_payment_id")
    if status:
        payments = payments.filter(approval_status=status)
    records = []
    for payment in payments:
        if not date_in_range(payment.payment_date, date_from, date_to):
            continue
        supplier = payment.supplier_invoice.supplier
        method = "mobile_money" if payment.payment_method == "mobile_money" else "bank_transfer"
        channel_details = payout_channel_details(supplier=supplier, method=method)
        records.append(payout_record(
            "supplier_payment",
            payment.pk,
            payment.transaction_ref or f"SUPPAY-{payment.pk:06d}",
            "outbound",
            payment.amount,
            "UGX",
            {"id": supplier.pk, "name": supplier.supplier_name, "supplier_code": supplier.supplier_code, "phone_number": supplier.phone_number},
            channel_details,
            payment.payment_status,
            payment.payment_date,
            {"self": request.build_absolute_uri(f"/supplier-payments/{payment.pk}/")},
        ))
    return records


def client_payment_records(request):
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    method = request.GET.get("method", "").strip()
    payments = Payment.objects.select_related("invoice", "invoice__client").all().order_by("-payment_date", "-payment_id")
    if method:
        payments = payments.filter(payment_method=method)
    records = []
    for payment in payments:
        if not date_in_range(payment.payment_date, date_from, date_to):
            continue
        client = payment.invoice.client
        channel = "bank_transfer" if payment.payment_method == "bank_transfer" else payment.payment_method
        records.append({
            "type": "client_payment",
            "id": payment.pk,
            "reference": payment.transaction_ref or f"PAY-{payment.pk:06d}",
            "direction": "inbound",
            "amount": str(payment.amount),
            "currency": "UGX",
            "party": {"id": client.pk, "name": client.client_name, "phone_number": client.phone_number, "email": client.email},
            "channel": channel,
            "bank_account": None,
            "mobile_money": None,
            "status": "received",
            "date": api_scalar(payment.payment_date),
            "ready_for_export": bool(payment.transaction_ref),
            "missing_fields": [] if payment.transaction_ref else ["transaction_ref"],
            "links": {"self": request.build_absolute_uri(f"/payments/{payment.pk}/"), "receipt": request.build_absolute_uri(f"/payments/{payment.pk}/receipt/")},
        })
    return records


def payout_api_records(request, batch_type):
    builders = {
        "salaries": salary_payout_records,
        "advances": advance_payout_records,
        "supplier-payments": supplier_payment_payout_records,
        "client-payments": client_payment_records,
    }
    return builders[batch_type](request)


def payout_api_index(request):
    require_view_access(request, "payout_api_index")
    if request.method != "GET":
        return api_error("Only GET is supported by this API endpoint.", status=405, code="method_not_allowed")
    return JsonResponse({
        "api": "finance-payouts",
        "version": "1.0",
        "security": "Authenticated finance staff session required. These endpoints prepare bank and mobile money batches; they do not transmit funds.",
        "batches": [
            {"name": name, "title": title, "records_url": request.build_absolute_uri(f"/api/payouts/{name}/")}
            for name, title in PAYOUT_API_BATCHES.items()
        ],
        "filters": {
            "method": "bank_transfer or mobile_money where applicable",
            "ready": "true/false readiness for export",
            "status": "Workflow status where applicable",
            "date_from": "Inclusive start date",
            "date_to": "Inclusive end date",
            "page": "Page number",
            "page_size": f"Records per page, max {API_MAX_PAGE_SIZE}",
        },
    })


def payout_api_batch(request, batch_type):
    require_view_access(request, "payout_api_batch")
    if request.method != "GET":
        return api_error("Only GET is supported by this API endpoint.", status=405, code="method_not_allowed")
    if batch_type not in PAYOUT_API_BATCHES:
        raise Http404("The requested payout API batch does not exist.")
    records = apply_payout_common_filters(payout_api_records(request, batch_type), request)
    total_count = len(records)
    page_size = parse_positive_int(request.GET.get("page_size"), API_DEFAULT_PAGE_SIZE, API_MAX_PAGE_SIZE)
    page = parse_positive_int(request.GET.get("page"), 1)
    offset = (page - 1) * page_size
    page_records = records[offset:offset + page_size]
    next_page = page + 1 if offset + page_size < total_count else None
    previous_page = page - 1 if page > 1 else None
    return JsonResponse({
        "metadata": {
            "batch": batch_type,
            "title": PAYOUT_API_BATCHES[batch_type],
            "direction": "inbound" if batch_type == "client-payments" else "outbound",
            "links": {"index": request.build_absolute_uri("/api/payouts/")},
        },
        "pagination": {
            "count": total_count,
            "page": page,
            "page_size": page_size,
            "next": request.build_absolute_uri(f"/api/payouts/{batch_type}/?page={next_page}&page_size={page_size}") if next_page else None,
            "previous": request.build_absolute_uri(f"/api/payouts/{batch_type}/?page={previous_page}&page_size={page_size}") if previous_page else None,
        },
        "totals": {
            "amount": str(sum((Decimal(record["amount"]) for record in records), Decimal("0.00"))),
            "ready_count": sum(1 for record in records if record["ready_for_export"]),
            "not_ready_count": sum(1 for record in records if not record["ready_for_export"]),
        },
        "results": page_records,
    })

def procurement_dashboard(request):
    register_items = [
        "suppliers",
        "procurement-requisitions",
        "supplier-proformas",
        "purchase-orders",
        "goods-received-notes",
        "supplier-invoices",
        "supplier-payments",
    ]
    module_cards = []
    for item_name in register_items:
        config = get_config(item_name)
        module_cards.append({
            "model_name": item_name,
            "label": config["title"],
            "value": config["model"].objects.count(),
            "caption": "records",
            "accent": "blue",
        })
    pending_requisitions = ProcurementRequisition.objects.filter(status="submitted").select_related("requested_by", "approval_assigned_to")[:8]
    approved_requisitions = ProcurementRequisition.objects.filter(status="approved").select_related("preferred_supplier")[:8]
    pending_payments = SupplierPayment.objects.filter(approval_status="pending").select_related("supplier_invoice", "supplier_invoice__supplier")[:8]
    supplier_invoices_ready = SupplierInvoice.objects.filter(status="approved").select_related("supplier")[:8]
    notifications = ProcurementNotification.objects.select_related("recipient")[:8]
    context = {
        "title": "Procurement",
        "module_cards": module_cards,
        "pending_requisitions": pending_requisitions,
        "approved_requisitions": approved_requisitions,
        "pending_payments": pending_payments,
        "supplier_invoices_ready": supplier_invoices_ready,
        "notifications": notifications,
    }
    return render_page(request, "webCom/procurement_dashboard.html", context, "procurement")
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
    for obj in model.objects.all().order_by(*ordering):
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
    for asset in assets:
        assigned_quantity = sum(
            assignment.quantity
            for assignment in asset.assignments.filter(status="assigned")
        )
        report_rows.append(
            {
                "asset": asset,
                "total_quantity": asset.quantity,
                "assigned_quantity": assigned_quantity,
                "store_quantity": asset.quantity - assigned_quantity,
                "notes": asset.notes or "-",
            }
        )

    context = {
        "title": "Asset Store Report",
        "report_rows": report_rows,
    }
    return render_page(request, "webCom/asset_store_report.html", context, "assets")

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
        assigned_total = 0
        assigned_rows = asset.assignments.filter(status="assigned").order_by("assigned_date", "pk")
        for assignment in assigned_rows:
            assigned_total += assignment.quantity
            report_rows.append(
                {
                    "asset": asset,
                    "quantity": assignment.quantity,
                    "status": assignment.get_status_display(),
                    "assigned_to": assignment.assigned_to,
                    "site": assignment.site or "-",
                    "deployment": assignment.deployment or "-",
                    "assigned_date": assignment.assigned_date,
                    "return_date": assignment.return_date or "-",
                }
            )

        unassigned_quantity = asset.quantity - assigned_total
        if unassigned_quantity > 0:
            report_rows.append(
                {
                    "asset": asset,
                    "quantity": unassigned_quantity,
                    "status": "Unassigned",
                    "assigned_to": "-",
                    "site": "-",
                    "deployment": "-",
                    "assigned_date": "-",
                    "return_date": "-",
                }
            )

    context = {
        "title": "Asset Assignment Report",
        "report_rows": report_rows,
    }
    return render_page(request, "webCom/asset_assignment_report.html", context, "asset-assignments")

def training_certificate(request, pk):
    training = get_object_or_404(Training.objects.select_related("employee"), pk=pk)
    certificate_no = training.ensure_certificate_number()
    training_manager = Employee.objects.filter(status="active", role="manager").order_by("first_name", "last_name").first()
    context = {
        "title": f"Certificate - {training.trainee}",
        "training": training,
        "certificate_no": certificate_no,
        "training_manager_name": str(training_manager) if training_manager else "Training Manager",
    }
    return render_page(request, "webCom/training_certificate.html", context, "training")

def salary_payslip(request, pk):
    require_model_access(request, "salaries")
    salary = get_object_or_404(Salary.objects.select_related("employee"), pk=pk)
    salary.update_basic_salary()
    salary.save(update_fields=["basic_salary", "updated_at"])
    salary.recover_advances()
    context = {
        "title": f"Payslip - {salary.employee}",
        "salary": salary,
        "employee": salary.employee,
        "earnings": [
            ("Basic Salary", salary.basic_salary),
            ("Allowances", salary.allowances),
            ("Overtime Pay", salary.overtime_pay),
            ("Bonus", salary.bonus),
        ],
        "deductions": [
            ("NSSF Employee Contribution (5%)", salary.nssf_employee),
            ("PAYE", salary.paye),
            ("Loan Deduction", salary.loan_deduction),
            ("Medical Deduction", salary.medical_deduction),
            ("Salary Advance Recovery", salary.advance_recovery),
            ("Other Payroll Deductions", salary.other_payroll_deductions),
            ("Other Deductions", salary.deductions),
        ],
        "advance_balance": salary.ledger_advance_balance,
        "employer_contributions": [
            ("NSSF Employer Contribution (10%)", salary.nssf_employer),
        ],
        "payroll_notes": [],
    }
    return render_page(request, "webCom/salary_payslip.html", context, "salaries")

def invoice_document(request, pk):
    require_model_access(request, "invoices")
    invoice = get_object_or_404(Invoice.objects.select_related("client", "contract").prefetch_related("sites", "billable_products", "provisional_items"), pk=pk)
    invoice.save(update_fields=invoice_update_fields())
    payments = invoice.payments.all().order_by("payment_date", "payment_id")
    amount_paid = sum((payment.amount for payment in payments), Decimal("0.00"))
    balance_due = invoice.total_amount - amount_paid
    context = {
        "title": invoice.invoice_number or invoice.generate_invoice_number(),
        "company_name": getattr(settings, "COMPANY_NAME", "TURYANS SECURITY COMPANY (U) LIMITED"),
        "invoice": invoice,
        "client": invoice.client,
        "contract": invoice.contract,
        "invoice_lines": invoice.invoice_line_items(),
        "payments": payments,
        "amount_paid": amount_paid,
        "balance_due": balance_due,
    }
    return render_page(request, "webCom/invoice_document.html", context, "invoices")

def purchase_order_lpo_lines(purchase_order):
    proforma = getattr(purchase_order, "proforma_invoice", None)
    if proforma:
        return [
            {
                "description": item.description or item.catalog_item or "Item",
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "discount_amount": item.discount_amount,
                "tax_rate": item.tax_rate,
                "tax_amount": item.tax_amount,
                "line_total": item.line_total,
                "total_amount": item.total_amount,
            }
            for item in proforma.items.select_related("catalog_item").all().order_by("item_id")
        ]
    return [
        {
            "description": purchase_order.requisition.description or purchase_order.requisition.title,
            "quantity": 1,
            "unit_price": purchase_order.subtotal_amount,
            "discount_amount": Decimal("0.00"),
            "tax_rate": "-",
            "tax_amount": purchase_order.tax_amount,
            "line_total": purchase_order.subtotal_amount,
            "total_amount": purchase_order.total_amount,
        }
    ]


def purchase_order_lpo_report(request, pk):
    require_model_access(request, "purchase-orders")
    purchase_order = get_object_or_404(
        PurchaseOrder.objects.select_related("requisition", "supplier", "prepared_by"),
        pk=pk,
    )
    context = {
        "title": purchase_order.po_number or purchase_order.generate_po_number(),
        "company_name": getattr(settings, "COMPANY_NAME", "TURYANS SECURITY COMPANY (U) LIMITED"),
        "purchase_order": purchase_order,
        "supplier": purchase_order.supplier,
        "requisition": purchase_order.requisition,
        "proforma": getattr(purchase_order, "proforma_invoice", None),
        "lpo_lines": purchase_order_lpo_lines(purchase_order),
    }
    return render_page(request, "webCom/purchase_order_lpo_report.html", context, "purchase-orders")
def payment_reconciliation_rows(invoice, current_payment=None):
    payments = list(invoice.payments.all().order_by("payment_date", "payment_id"))
    running_balance = invoice.total_amount
    rows = []
    for payment in payments:
        running_balance -= payment.amount
        rows.append({
            "payment": payment,
            "is_current": current_payment is not None and payment.pk == current_payment.pk,
            "balance_after": running_balance,
        })
    return rows


def payment_document_context(payment):
    invoice = payment.invoice
    invoice.save(update_fields=invoice_update_fields())
    payment_record, _created = Paymee.objects.get_or_create(invoice=invoice)
    payment_record.save(update_fields=["client", "total_amount", "amount_paid", "due_date", "last_payment_date", "status", "updated_at"])
    prior_paid = sum(
        (item.amount for item in invoice.payments.filter(payment_date__lt=payment.payment_date)),
        Decimal("0.00"),
    )
    prior_paid += sum(
        (item.amount for item in invoice.payments.filter(payment_date=payment.payment_date, payment_id__lt=payment.payment_id)),
        Decimal("0.00"),
    )
    total_paid_to_date = prior_paid + payment.amount
    balance_before = invoice.total_amount - prior_paid
    balance_after = invoice.total_amount - total_paid_to_date
    return {
        "company_name": getattr(settings, "COMPANY_NAME", "TURYANS SECURITY COMPANY (U) LIMITED"),
        "payment": payment,
        "invoice": invoice,
        "client": invoice.client,
        "contract": invoice.contract,
        "payment_record": payment_record,
        "receipt_number": f"RCT-{payment.payment_id:06d}",
        "statement_number": f"REC-{payment.payment_id:06d}",
        "prior_paid": prior_paid,
        "balance_before": balance_before,
        "total_paid_to_date": total_paid_to_date,
        "balance_after": balance_after,
        "reconciliation_rows": payment_reconciliation_rows(invoice, payment),
    }


def payment_receipt(request, pk):
    require_view_access(request, "payment_receipt")
    payment = get_object_or_404(Payment.objects.select_related("invoice", "invoice__client", "invoice__contract"), pk=pk)
    context = payment_document_context(payment)
    context["title"] = f"Receipt {context['receipt_number']}"
    return render_page(request, "webCom/payment_receipt.html", context, "payments")


def payment_reconciliation(request, pk):
    require_view_access(request, "payment_reconciliation")
    payment = get_object_or_404(Payment.objects.select_related("invoice", "invoice__client", "invoice__contract"), pk=pk)
    context = payment_document_context(payment)
    context["title"] = f"Reconciliation {context['statement_number']}"
    return render_page(request, "webCom/payment_reconciliation.html", context, "payments")


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


def employee_transfer(request, pk):
    require_model_access(request, "employees")
    employee = get_object_or_404(Employee, pk=pk, role__in=("guard", "supervisor"))
    form = EmployeeDeploymentTransferForm(request.POST or None, employee=employee)

    if request.method == "POST" and form.is_valid():
        new_area = form.save()
        sync_salary_table()
        messages.success(request, f"{employee} transferred to {new_area.region} successfully.")
        return redirect("webcom:detail", model_name="employees", pk=employee.pk)

    context = {
        "model_name": "employees",
        "title": f"Transfer {employee}",
        "form": form,
        "object": employee,
        "pk": employee.pk,
        "submit_label": "Transfer",
    }
    return render_page(request, "webCom/model_form.html", context, "employees")

def build_leave_feedback_message(leave):
    decision = leave.get_approval_status_display()
    operations_status = leave.get_operations_verification_status_display()
    return (
        f"Your {leave.get_leave_type_display()} request from {leave.start_date} to {leave.end_date} has been {decision}.\n\n"
        f"Operations verification: {operations_status}.\n"
        f"Operations feedback: {leave.operations_feedback or '-'}\n"
        f"Human Resource feedback: {leave.feedback or '-'}"
    )


def deliver_leave_feedback(leave):
    if not leave.employee.email:
        return False, "Employee has no email address; feedback was saved on the leave request."
    try:
        send_mail(
            subject=f"Leave request {leave.get_approval_status_display()}",
            message=build_leave_feedback_message(leave),
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[leave.employee.email],
            fail_silently=False,
        )
    except Exception as exc:
        return False, f"Feedback was saved, but email delivery failed: {str(exc)[:255]}"
    return True, "Feedback email sent to the employee."


def leave_review(request, pk):
    require_model_access(request, "leaves")
    leave = get_object_or_404(Leave.objects.select_related("employee", "verified_by", "approved_by"), pk=pk)
    form = LeaveReviewForm(request.POST or None, leave=leave)

    if request.method == "POST" and form.is_valid():
        leave = form.save()
        delivered, delivery_message = deliver_leave_feedback(leave)
        if delivered:
            messages.success(request, f"Leave request reviewed successfully. {delivery_message}")
        else:
            messages.warning(request, f"Leave request reviewed successfully. {delivery_message}")
        return redirect("webcom:detail", model_name="leaves", pk=leave.pk)

    context = {
        "model_name": "leaves",
        "title": f"Review Leave - {leave.employee}",
        "form": form,
        "object": leave,
        "pk": leave.pk,
        "submit_label": "Submit Review",
    }
    return render_page(request, "webCom/model_form.html", context, "leaves")


def disciplinary_action_has_feedback(action):
    return bool(action.steps_taken or action.conclusion or action.outcome != "pending")


def build_disciplinary_feedback_message(action):
    lines = [
        f"Disciplinary feedback for {action.employee}.",
        f"Offence committed: {action.offence_committed}.",
        f"Status: {action.get_status_display()}.",
    ]
    if action.steps_taken:
        lines.append(f"Steps taken: {action.steps_taken}")
    if action.outcome != "pending":
        lines.append(f"Outcome: {action.get_outcome_display()}.")
    if action.conclusion:
        lines.append(f"Conclusion: {action.conclusion}")
    if action.handled_by_id:
        lines.append(f"Handled by: {action.handled_by}.")
    return "\n".join(lines)


def deliver_disciplinary_notification(notification):
    if not notification.recipient.email:
        notification.status = "pending"
        notification.delivery_note = "Recipient has no email address. Message saved in disciplinary notifications."
        notification.save(update_fields=["status", "delivery_note", "updated_at"])
        return False

    try:
        send_mail(
            subject=f"Disciplinary feedback: {notification.disciplinary_action.offence_committed}",
            message=notification.message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[notification.recipient.email],
            fail_silently=False,
        )
        notification.status = "sent"
        notification.delivery_note = "Feedback message sent to employee."
    except Exception as exc:
        notification.status = "failed"
        notification.delivery_note = str(exc)[:255]
    notification.notified_at = timezone.now()
    notification.save(update_fields=["status", "delivery_note", "notified_at", "updated_at"])
    return notification.status == "sent"


def invoice_item_price_payload():
    prices = InvoiceBillableItemPrice.objects.filter(active=True)
    return json.dumps({
        price.item_name: {
            "unit_price": str(price.unit_price),
            "taxable": price.taxable,
        }
        for price in prices
    })

def invoice_item_post_data(request):
    if request.method != "POST":
        return None
    if "items-TOTAL_FORMS" in request.POST and "items-INITIAL_FORMS" in request.POST:
        return request.POST
    item_indexes = set()
    for key in request.POST:
        match = re.match(r"items-(\d+)-", key)
        if match:
            item_indexes.add(int(match.group(1)))
    if not item_indexes:
        return None
    data = request.POST.copy()
    data["items-TOTAL_FORMS"] = str(max(item_indexes) + 1)
    data["items-INITIAL_FORMS"] = "0"
    data.setdefault("items-MIN_NUM_FORMS", "0")
    data.setdefault("items-MAX_NUM_FORMS", "1000")
    return data

def invoice_update_fields():
    return ["invoice_number", "client", "deployed_guards", "rate_per_guard", "contract_amount", "tax_amount", "total_amount", "description", "updated_at"]


def create_split_site_invoices(form):
    data = form.cleaned_data
    contract = data["contract"]
    invoices = []
    for site in contract.sites.all().order_by("site_name", "site_id"):
        invoice = Invoice.objects.create(
            contract=contract,
            client=contract.client,
            invoice_date=data["invoice_date"],
            due_date=data["due_date"],
            billing_start_date=data["billing_start_date"],
            billing_end_date=data["billing_end_date"],
            amendment_amount=Decimal("0.00"),
            amendment_reason="",
            tax_rate=data["tax_rate"],
            status=data["status"],
        )
        invoice.sites.set([site])
        invoice.save(update_fields=invoice_update_fields())
        invoices.append(invoice)
    return invoices


def invoice_item_formset_has_rows(formset):
    return any(
        form.cleaned_data and not form.cleaned_data.get("DELETE") and form.cleaned_data.get("item_name")
        for form in formset.forms
        if hasattr(form, "cleaned_data")
    )



def proforma_item_price_payload():
    prices = SupplierProformaItemPrice.objects.filter(active=True)
    return json.dumps({
        str(price.pk): {"unit_price": str(price.unit_price), "tax_rate": str(price.tax_rate), "discount_allowed": price.discount_allowed}
        for price in prices
    })

def proforma_item_formset_has_rows(formset):
    return any(
        form.cleaned_data and not form.cleaned_data.get("DELETE") and form.cleaned_data.get("catalog_item")
        for form in formset.forms
        if hasattr(form, "cleaned_data")
    )


def proforma_formset_total(formset):
    subtotal = Decimal("0.00")
    tax_total = Decimal("0.00")
    for form in formset.forms:
        if not hasattr(form, "cleaned_data") or not form.cleaned_data or form.cleaned_data.get("DELETE"):
            continue
        catalog_item = form.cleaned_data.get("catalog_item")
        quantity = form.cleaned_data.get("quantity") or Decimal("0.00")
        if not catalog_item or quantity <= 0:
            continue
        gross_amount = (quantity * catalog_item.unit_price).quantize(Decimal("0.01"))
        discount_amount = Decimal("0.00")
        if catalog_item.discount_allowed:
            discount_rate = SupplierProformaInvoiceItem.discount_rate_for_quantity(quantity)
            discount_amount = (gross_amount * (discount_rate / Decimal("100"))).quantize(Decimal("0.01"))
        line_total = (gross_amount - discount_amount).quantize(Decimal("0.01"))
        tax_amount = (line_total * ((catalog_item.tax_rate or Decimal("0.00")) / Decimal("100"))).quantize(Decimal("0.01"))
        subtotal += line_total
        tax_total += tax_amount
    return subtotal + tax_total


def validate_proforma_total_against_requisition(form, formset):
    requisition = form.cleaned_data.get("requisition")
    if not requisition or not hasattr(formset, "forms"):
        return True
    approved_limit = requisition.approved_amount or requisition.estimated_amount
    submitted_total = proforma_formset_total(formset)
    if approved_limit and submitted_total > approved_limit:
        form.add_error(None, f"Supplier proforma total {submitted_total:,.2f} exceeds the approved requisition amount {approved_limit:,.2f}.")
        return False
    return True
def notify_disciplinary_employee(action):
    if not disciplinary_action_has_feedback(action):
        return None
    notification, _created = DisciplinaryNotification.objects.update_or_create(
        disciplinary_action=action,
        recipient=action.employee,
        defaults={
            "message": build_disciplinary_feedback_message(action),
            "status": "pending",
            "delivery_note": "",
            "notified_at": timezone.now(),
        },
    )
    deliver_disciplinary_notification(notification)
    return notification


def disciplinary_notifications(request):
    notifications = DisciplinaryNotification.objects.select_related(
        "disciplinary_action",
        "recipient",
    ).order_by("-notified_at", "-notification_id")
    context = {
        "title": "Disciplinary Notifications",
        "notifications": notifications,
    }
    return render_page(request, "webCom/disciplinary_notifications.html", context, "disciplinary-actions")



def procurement_contact_supplier(request, pk):
    require_view_access(request, "procurement_contact_supplier")
    requisition = get_object_or_404(ProcurementRequisition, pk=pk)
    if request.method != "POST":
        return redirect("webcom:detail", model_name="procurement-requisitions", pk=requisition.pk)
    try:
        requisition.mark_supplier_contacted()
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Supplier contacted and procurement notifications sent.")
    return redirect("webcom:list", model_name="procurement")

def model_create(request, model_name):
    config = get_config(model_name)
    require_model_access(request, model_name)
    if config.get("managed_table"):
        messages.info(request, managed_table_message(config))
        return redirect("webcom:list", model_name=model_name)
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
    return render_page(request, "webCom/model_form.html", context, model_name)
def model_update(request, model_name, pk):
    config = get_config(model_name)
    require_model_access(request, model_name)
    if config.get("managed_table"):
        messages.info(request, managed_table_message(config))
        return redirect("webcom:list", model_name=model_name)
    obj = get_object_or_404(config["model"], pk=pk)
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

def incident_notify(request, pk):
    require_view_access(request, "incident_notify")
    incident = get_object_or_404(Incident, pk=pk)
    recipients = get_incident_authority_recipients()

    if not recipients:
        messages.warning(request, "No active supervisors, managers, or human resource staff were found.")
        return redirect("webcom:list", model_name="incidents")

    message = build_incident_notification_message(incident)
    if incident.status == "reported":
        incident.status = "notified"
    incident.notification_summary = f"Authorities notified on {timezone.now().strftime('%Y-%m-%d %H:%M')}."
    incident.save(update_fields=["status", "notification_summary", "updated_at"])
    delivered_count = 0
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
        if notification.status == "sent":
            delivered_count += 1

    messages.success(
        request,
        f"Incident notification prepared for {len(recipients)} authority contact(s); {delivered_count} email(s) sent.",
    )
    return redirect("webcom:incident_notifications")


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





















