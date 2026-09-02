import csv
import json
import io
import re
from urllib.parse import urlencode
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.db import DatabaseError, connection, models, transaction
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Sum
from django.forms import modelformset_factory
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms_finance import (
    AdvanceForm,
    BudgetForm,
    ExpenseForm,
    GoodsReceivedNoteForm,
    InvoiceBillableItemPriceForm,
    InvoiceForm,
    PaymentForm,
    PaymeeForm,
    PayrollDeductionForm,
    ProcurementApprovalForm,
    ProcurementRequisitionForm,
    PurchaseOrderForm,
    SalaryForm,
    SupplierForm,
    SupplierInvoiceForm,
    SupplierPaymentForm,
    SupplierProformaInvoiceForm,
    SupplierProformaItemPriceForm,
)
from .forms_hr import (
    DisciplinaryActionForm,
    DocumentForm,
    EmployeeForm,
    GuardForm,
    LeaveForm,
    PerformanceEvaluationForm,
    PositionForm,
    RoleForm,
    SupervisorForm,
    TrainingForm,
)
from .forms_operations import (
    AssetAssignmentForm,
    AssetForm,
    AttendanceForm,
    ClientForm,
    ContractForm,
    DeploymentAreaForm,
    DeploymentForm,
    IncidentForm,
    PatrolLogForm,
    RegionForm,
    ShiftForm,
    SiteForm,
)
from .forms_public import AssociatedLinkForm, CompanyEventForm, JobApplicationForm, JobPostingForm, WebsiteAdvertisementForm, WebsiteResourceForm
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
    "assets": {"model": Asset, "form": AssetForm, "title": "Assets", "icon": "AS", "fields": ["asset_type", "asset_name", "make", "model", "asset_number", "number_plate", "condition", "quantity", "assets_issued_out", "stock_balance", "low_stock_message"], "detail_fields": ["asset_id", "asset_type", "asset_name", "make", "model", "asset_number", "engine_number", "chassis_number", "number_plate", "police_number", "color", "size", "condition", "storage_location", "quantity", "assets_issued_out", "stock_balance", "low_stock_message", "notes", "created_at", "updated_at"], "required_only": False},
    "asset-assignments": {"model": AssetAssignment, "form": AssetAssignmentForm, "title": "Asset Accountability", "icon": "AA", "fields": ["asset", "quantity", "assigned_to", "issued_by", "received_by", "assigned_date", "status", "accountability_status"], "detail_fields": ["assignment_id", "asset", "quantity", "assigned_to", "guard", "driver", "site", "deployment", "issued_by", "received_by", "assigned_date", "acknowledged_at", "return_date", "status", "accountability_status", "condition_issued", "condition_returned", "notes", "accountability_notes", "created_at", "updated_at"], "entry_fields": ["asset", "quantity", "guard", "driver", "site", "deployment", "issued_by", "received_by", "assigned_date", "return_date", "status", "condition_issued", "condition_returned", "notes", "accountability_notes"], "required_only": False},
    "incidents": {"model": Incident, "form": IncidentForm, "title": "Incidents", "icon": "IN", "fields": ["incident_type", "site", "severity_level", "status", "date_time"], "detail_fields": ["incident_id", "site", "incident_type", "severity_level", "status", "date_time", "location", "reported_by", "reported_to", "description", "occurrence_summary", "immediate_action_taken", "notification_summary", "investigation_assigned_to", "investigation_findings", "corrective_action", "conclusion", "closed_by", "closed_at", "created_at", "updated_at"]},
    "patrol-logs": {"model": Patrol_Log, "form": PatrolLogForm, "title": "Patrol Logs", "icon": "PL", "fields": ["site", "patrol_date", "patrol_route", "quantity", "duration"]},
    "deployments": {"model": Deployment, "form": DeploymentForm, "title": "Deployments", "icon": "DP", "fields": ["client", "site", "shift_summary", "start_date", "end_date", "status"], "entry_fields": ["client", "site", "shift_type", "start_date", "end_date", "status"], "required_only": False},
    "deployment-areas": {"model": DeploymentArea, "form": DeploymentAreaForm, "title": "Deployment Areas", "icon": "DA", "fields": ["employee", "region", "start_date", "end_date", "status", "transferred_by_hr_manager"], "detail_fields": ["deployment_area_id", "employee", "region", "start_date", "end_date", "status", "transferred_by_hr_manager", "transfer_notes"], "entry_fields": ["employee", "region", "start_date", "end_date", "status", "transferred_by_hr_manager", "transfer_notes"], "required_only": False},
    "roles": {"model": Role, "form": RoleForm, "title": "Roles", "icon": "RO", "fields": ["role_name", "department", "description"]},
    "positions": {"model": Position, "form": PositionForm, "title": "Positions", "icon": "PO", "fields": ["position_title", "department", "grade_level", "salary_range_min", "salary_range_max"]},
    "employees": {"model": Employee, "form": EmployeeForm, "title": "Employees", "icon": "EM", "fields": ["employee_id", "employee_number", "first_name", "last_name", "role", "position", "department", "current_deployment_area", "daily_rate", "is_reliever", "status"], "detail_fields": ["employee_id", "employee_number", "first_name", "last_name", "date_of_birth", "gender", "phone_number", "email", "address", "national_id", "nssf_number", "role", "position", "department", "current_deployment_area", "daily_rate", "is_reliever", "payout_method", "bank_name", "bank_account_name", "bank_account_number", "mobile_money_provider", "mobile_money_number", "qualification", "hire_date", "status"], "entry_fields": ["first_name", "last_name", "date_of_birth", "gender", "phone_number", "email", "address", "national_id", "role", "position", "department", "hire_date", "deployment_area", "is_reliever", "payout_method", "bank_name", "bank_account_name", "bank_account_number", "mobile_money_provider", "mobile_money_number"], "ordering": ["employee_id"], "required_only": False},
    "guards": {"model": Guard, "form": GuardForm, "title": "Guards", "icon": "GD", "fields": ["employee", "qualification", "armed_status"]},
    "supervisors": {"model": Supervisor, "form": SupervisorForm, "title": "Supervisors", "icon": "SV", "fields": ["employee", "authority_level"]},
    "training": {"model": Training, "form": TrainingForm, "title": "Training", "icon": "TR", "fields": ["training_type", "trainee", "training_name", "provider", "start_date", "end_date"], "detail_fields": ["training_id", "training_type", "trainee", "training_name", "provider", "start_date", "end_date"], "entry_fields": ["training_type", "employee", "recruit", "training_name", "provider", "start_date", "end_date"], "required_only": False},
    "attendance": {"model": Attendance, "form": AttendanceForm, "title": "Attendance", "icon": "AT", "fields": ["site", "shift", "scheduled_guard", "present", "attended_guard", "date"]},
    "leaves": {"model": Leave, "form": LeaveForm, "title": "Leaves", "icon": "LV", "fields": ["employee", "leave_type", "start_date", "end_date", "application_status", "operations_verification_status", "approval_status"], "detail_fields": ["leave_id", "employee", "leave_type", "start_date", "end_date", "address_while_away", "emergency_contact", "hr_verifier", "supervisor", "hod", "stand_in_coworker", "reason", "application_status", "operations_verification_status", "verified_by", "operations_feedback", "operations_verified_at", "approval_status", "approved_by", "feedback", "hr_decided_at", "created_at", "updated_at"]},
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
    if model is Asset and field_name == "quantity":
        return "Store Quantity"
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
        "assets_issued_out": "Assets Issued Out",
        "engine_number": "Engine Number",
        "chassis_number": "Chassis Number",
        "number_plate": "Number Plate",
        "stock_balance": "Stock Balance",
        "low_stock_message": "Low Stock Message",
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
        {
            "object": obj,
            "pk": obj.pk,
            "cells": [{"field": field, "value": get_field_value(obj, field)} for field in fields],
            "values": [get_field_value(obj, field) for field in fields],
        }
        for obj in objects
    ]


def summarize_group_values(values, empty="-"):
    cleaned = []
    for value in values:
        value = str(value or "").strip()
        if value and value not in cleaned:
            cleaned.append(value)
    if not cleaned:
        return empty
    if len(cleaned) == 1:
        return cleaned[0]
    sample = ", ".join(cleaned[:3])
    suffix = "" if len(cleaned) <= 3 else f" + {len(cleaned) - 3} more"
    return f"{len(cleaned)} values: {sample}{suffix}"


class AssetStockGroup:
    def __init__(self, assets):
        self.assets = list(assets)
        self.primary_asset = self.assets[0]
        self.pk = self.primary_asset.pk
        self.asset_type = self.primary_asset.asset_type
        self.asset_name = self.primary_asset.asset_name
        self.asset_name_key = self.normalized_asset_name(self.asset_name)
        self.asset_count = len(self.assets)
        self.make = summarize_group_values(asset.make for asset in self.assets)
        self.model = summarize_group_values(asset.model for asset in self.assets)
        self.asset_number = summarize_group_values(asset.asset_number for asset in self.assets)
        self.engine_number = summarize_group_values(asset.engine_number for asset in self.assets)
        self.chassis_number = summarize_group_values(asset.chassis_number for asset in self.assets)
        self.number_plate = summarize_group_values(asset.number_plate for asset in self.assets)
        self.condition = self._shared_choice_value("condition")
        self.quantity = sum(asset.quantity for asset in self.assets)
        self.assets_issued_out = sum(asset.assets_issued_out for asset in self.assets)
        self.stock_balance = self.quantity - self.assets_issued_out
        self.low_stock_message = self._low_stock_message()

    def get_asset_type_display(self):
        return self.primary_asset.get_asset_type_display()

    def get_condition_display(self):
        if self.condition == "mixed":
            return "Mixed"
        if not self.condition:
            return "-"
        return dict(Asset.CONDITION_CHOICES).get(self.condition, self.condition)

    @property
    def is_low_stock(self):
        return self.stock_balance <= 10

    @property
    def group_query(self):
        return urlencode({
            "asset_type": self.asset_type,
            "asset_name": self.asset_name_key,
        })

    @staticmethod
    def normalized_asset_name(asset_name):
        return " ".join((asset_name or "").lower().split())

    def _shared_choice_value(self, field_name):
        values = {getattr(asset, field_name) for asset in self.assets if getattr(asset, field_name)}
        if not values:
            return ""
        if len(values) == 1:
            return values.pop()
        return "mixed"

    def _low_stock_message(self):
        if not self.is_low_stock:
            return "-"
        asset_name = self.asset_name or self.get_asset_type_display()
        return f"{self.get_asset_type_display()} is low please refill more {asset_name}"


def asset_stock_groups(assets):
    groups = {}
    for asset in assets:
        key = (asset.asset_type, AssetStockGroup.normalized_asset_name(asset.asset_name))
        groups.setdefault(key, []).append(asset)
    return [
        AssetStockGroup(group_assets)
        for group_assets in groups.values()
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
    site_count = Site.objects.count()
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
        {"label": "Collection Rate", "value": f"{collection_rate}%", "progress": collection_rate, "tone": "green", "note": f"UGX {payment_total:,.0f} collected from UGX {invoice_total:,.0f} invoiced."},
        {"label": "Budget Use", "value": f"{budget_rate}%", "progress": budget_rate, "tone": "amber", "note": f"UGX {budget_spent:,.0f} spent from UGX {budget_allocated:,.0f} allocated."},
        {"label": "Deployment Coverage", "value": active_deployments, "progress": dashboard_percent(active_deployments, site_count or 1), "tone": "blue", "note": f"{active_deployments} active deployments across {site_count} client sites."},
    ]
    queue_cards = [
        {"label": "Incidents", "value": open_incidents, "caption": f"{high_risk_incidents} high risk", "href": "incidents", "tone": "red"},
        {"label": "Leave Reviews", "value": pending_leaves, "caption": "Pending approval", "href": "leaves", "tone": "amber"},
        {"label": "Advances", "value": pending_advances, "caption": "Awaiting decision", "href": "advances", "tone": "blue"},
        {"label": "Draft Invoices", "value": draft_invoices, "caption": "To finalize", "href": "invoices", "tone": "green"},
    ]
    recent_incidents = Incident.objects.select_related("site").order_by("-date_time", "-incident_id")[:5]
    recent_payments = Payment.objects.select_related("invoice", "invoice__client").order_by("-payment_date", "-payment_id")[:5]
    quick_actions = [
        {"label": "Incident", "icon": "!", "url_name": "webcom:create", "model": "incidents", "tone": "red"},
        {"label": "Deployment", "icon": "+", "url_name": "webcom:create", "model": "deployments", "tone": "blue"},
        {"label": "Payment", "icon": "$", "url_name": "webcom:create", "model": "payments", "tone": "green"},
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
        "queue_cards": queue_cards,
        "quick_actions": quick_actions,
        "recent_incidents": recent_incidents,
        "recent_payments": recent_payments,
        "department_pie": department_pie,
        "department_pie_style": department_pie_style,
        "severity_pie": severity_pie,
        "severity_pie_style": severity_pie_style,
        "clustered_bars": clustered_bars,
        "trend_charts": trend_charts,
    }
    return render_page(request, "webCom/home.html", context)
