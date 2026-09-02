from django import forms
from django.forms import inlineformset_factory

from .forms_base import DATE_WIDGET, DateRangeValidationMixin, StyledModelForm
from .models import (
    Advance,
    Budget,
    Contract,
    Employee,
    Expense,
    GoodsReceivedNote,
    Invoice,
    InvoiceBillableItem,
    InvoiceBillableItemPrice,
    Paymee,
    PayrollDeduction,
    Payment,
    ProcurementApproval,
    ProcurementRequisition,
    PurchaseOrder,
    Salary,
    Supplier,
    SupplierInvoice,
    SupplierPayment,
    SupplierProformaInvoice,
    SupplierProformaInvoiceItem,
    SupplierProformaItemPrice,
)


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
