from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.utils import timezone
from django.core.validators import MaxValueValidator, MinValueValidator

class PayrollDeduction(models.Model):
    """Recurring or one-off payroll deductions such as staff loans and medical deductions."""
    deduction_id = models.AutoField(primary_key=True)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="payroll_deductions")
    DEDUCTION_CATEGORY_CHOICES = [
        ("loan", "Loan"),
        ("medical", "Medical"),
        ("other", "Other"),
    ]
    category = models.CharField(max_length=30, choices=DEDUCTION_CATEGORY_CHOICES)
    description = models.CharField(max_length=255, blank=True, default="")
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField(blank=True, null=True)
    STATUS_CHOICES = [
        ("active", "Active"),
        ("inactive", "Inactive"),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def applies_to_period(self, start_date, end_date):
        if self.status != "active":
            return False
        if self.start_date and self.start_date > end_date:
            return False
        if self.end_date and self.end_date < start_date:
            return False
        return True

    def clean(self):
        super().clean()
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError("Deduction end date cannot be earlier than start date.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_category_display()} - {self.employee} ({self.amount})"

    class Meta:
        db_table = "payroll_deductions"
        ordering = ["employee__employee_number", "category", "start_date"]

class Salary(models.Model):
    """Manages employee salary information"""
    salary_id = models.AutoField(primary_key=True)
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name='salary')
    basic_salary = models.DecimalField(max_digits=10, decimal_places=2)
    allowances = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    deductions = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    overtime_pay = models.DecimalField(max_digits=10, decimal_places=2, default=0, editable=False)
    bonus = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    PAY_PERIOD_CHOICES = [
        ('monthly', 'Monthly'),
        ('weekly', 'Weekly'),
        ('biweekly', 'Biweekly'),
    ]
    pay_period = models.CharField(max_length=20, choices=PAY_PERIOD_CHOICES, default='monthly')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def get_period_dates(self):
        today = timezone.localdate()
        if self.pay_period == "weekly":
            start_date = today - timezone.timedelta(days=today.weekday())
            end_date = start_date + timezone.timedelta(days=6)
        elif self.pay_period == "biweekly":
            week_start = today - timezone.timedelta(days=today.weekday())
            if (week_start.isocalendar().week % 2) == 0:
                start_date = week_start
            else:
                start_date = week_start - timezone.timedelta(days=7)
            end_date = start_date + timezone.timedelta(days=13)
        else:
            start_date = today.replace(day=1)
            next_month = (start_date.replace(day=28) + timezone.timedelta(days=4)).replace(day=1)
            end_date = next_month - timezone.timedelta(days=1)
        return start_date, end_date

    @property
    def period_start_date(self):
        return self.get_period_dates()[0]

    @property
    def period_end_date(self):
        return self.get_period_dates()[1]

    @property
    def shifts_worked(self):
        if not self.employee_id:
            return 0
        start_date, end_date = self.get_period_dates()
        attendances = Attendance.objects.select_related("site", "shift", "attended_guard").filter(
            attended_guard=self.employee,
            present=True,
            date__range=(start_date, end_date),
        ).order_by("date", "shift__shift_type", "attendance_id")
        earned_shifts = set()
        for attendance in attendances:
            if not attendance.is_payable_shift:
                continue
            shift_key = (attendance.date, attendance.shift.shift_type if attendance.shift_id else None)
            earned_shifts.add(shift_key)
        return len(earned_shifts)

    @property
    def days_worked(self):
        return self.shifts_worked

    @property
    def salary_scale(self):
        return self.daily_rate

    @property
    def daily_rate(self):
        return self.employee.daily_rate if self.employee_id else Decimal("0.00")

    @property
    def standard_shifts(self):
        return min(self.shifts_worked, 26)

    @property
    def overtime_shifts(self):
        return max(self.shifts_worked - 26, 0)

    @property
    def overtime_multiplier(self):
        return Decimal("1.50")

    @property
    def overtime_daily_rate(self):
        return (self.daily_rate * self.overtime_multiplier).quantize(Decimal("0.01"))

    def calculate_basic_salary(self):
        if self.employee_id and self.employee.uses_fixed_monthly_salary:
            return self.employee.fixed_monthly_salary
        return self.standard_shifts * self.daily_rate

    def calculate_overtime_pay(self):
        if self.employee_id and self.employee.uses_fixed_monthly_salary:
            return Decimal("0.00")
        return (self.overtime_shifts * self.overtime_daily_rate).quantize(Decimal("0.01"))

    def update_basic_salary(self):
        self.basic_salary = self.calculate_basic_salary()
        self.overtime_pay = self.calculate_overtime_pay()

    @property
    def gross_pay(self):
        return self.basic_salary + self.allowances + self.overtime_pay + self.bonus

    @property
    def advance_recovery_rate(self):
        return Decimal("0.33")

    @property
    def advance_recovery_limit(self):
        return (self.gross_pay * self.advance_recovery_rate).quantize(Decimal("0.01"))

    def current_advance_recoveries(self):
        start_date, end_date = self.get_period_dates()
        return self.advance_recoveries.filter(period_start_date=start_date, period_end_date=end_date)

    @property
    def advance_recovery(self):
        return sum(recovery.amount for recovery in self.current_advance_recoveries())

    @property
    def ledger_installment_amount(self):
        recoveries = self.current_advance_recoveries()
        if recoveries.exists():
            return sum(recovery.installment_amount for recovery in recoveries)
        return self.advance_installment_amount

    @property
    def ledger_advance_balance(self):
        recoveries = self.current_advance_recoveries()
        if recoveries.exists():
            return sum(recovery.balance for recovery in recoveries)
        return self.advance_balance

    @property
    def payable_advances(self):
        if not self.employee_id:
            return Advance.objects.none()
        return self.employee.advances.exclude(approval_status="rejected").filter(status__in=["disbursed", "recovered"])

    @property
    def advance_installment_amount(self):
        return sum(advance.installment_amount for advance in self.payable_advances)

    @property
    def advance_balance(self):
        return sum(advance.balance for advance in self.payable_advances)

    def active_payroll_deductions(self):
        if not self.employee_id:
            return PayrollDeduction.objects.none()
        start_date, end_date = self.get_period_dates()
        return self.employee.payroll_deductions.filter(
            status="active",
            start_date__lte=end_date,
        ).filter(models.Q(end_date__isnull=True) | models.Q(end_date__gte=start_date))

    def payroll_deduction_total(self, category):
        return sum(deduction.amount for deduction in self.active_payroll_deductions().filter(category=category))

    @property
    def loan_deduction(self):
        return self.payroll_deduction_total("loan")

    @property
    def medical_deduction(self):
        return self.payroll_deduction_total("medical")

    @property
    def other_payroll_deductions(self):
        return self.payroll_deduction_total("other")

    @property
    def staff_deductions(self):
        return self.loan_deduction + self.medical_deduction + self.other_payroll_deductions

    @property
    def nssf_employee(self):
        return (self.gross_pay * Decimal("0.05")).quantize(Decimal("0.01"))

    @property
    def nssf_employer(self):
        return (self.gross_pay * Decimal("0.10")).quantize(Decimal("0.01"))

    @property
    def taxable_pay(self):
        return self.gross_pay

    @property
    def paye(self):
        taxable = self.taxable_pay
        if taxable <= Decimal("235000.00"):
            return Decimal("0.00")
        if taxable <= Decimal("335000.00"):
            return ((taxable - Decimal("235000.00")) * Decimal("0.10")).quantize(Decimal("0.01"))
        if taxable <= Decimal("410000.00"):
            return (Decimal("10000.00") + ((taxable - Decimal("335000.00")) * Decimal("0.20"))).quantize(Decimal("0.01"))
        paye = Decimal("25000.00") + ((taxable - Decimal("410000.00")) * Decimal("0.30"))
        if taxable > Decimal("10000000.00"):
            paye += (taxable - Decimal("10000000.00")) * Decimal("0.10")
        return paye.quantize(Decimal("0.01"))

    @property
    def total_deductions(self):
        return self.deductions + self.staff_deductions + self.advance_recovery + self.nssf_employee + self.paye

    @property
    def net_pay(self):
        return self.gross_pay - self.total_deductions

    @property
    def total_nssf(self):
        return self.nssf_employee + self.nssf_employer

    @property
    def total_salary(self):
        return self.net_pay

    def save(self, *args, **kwargs):
        self.update_basic_salary()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"basic_salary", "overtime_pay"}
        super().save(*args, **kwargs)

    def recover_advances(self):
        if not self.employee_id or self.advance_recovery_limit <= 0:
            return Decimal("0.00")

        start_date, end_date = self.get_period_dates()
        remaining_recovery = self.advance_recovery_limit
        recovered_now = Decimal("0.00")

        advances = self.employee.advances.exclude(approval_status="rejected").filter(status="disbursed").order_by("disbursement_date", "created_at")
        for advance in advances:
            if remaining_recovery <= 0:
                break

            current_recovery = AdvanceRecovery.objects.filter(
                advance=advance,
                salary=self,
                period_start_date=start_date,
                period_end_date=end_date,
            ).first()
            current_recovery_amount = current_recovery.amount if current_recovery else Decimal("0.00")
            balance = advance.outstanding_balance + current_recovery_amount
            if balance <= 0:
                advance.status = "recovered"
                advance.save(update_fields=["status", "updated_at"])
                continue

            installment_amount = self.advance_recovery_limit
            if advance.installment_amount != installment_amount:
                advance.installment_amount = installment_amount
                advance.save(update_fields=["installment_amount", "updated_at"])
            recovery_amount = min(balance, installment_amount, remaining_recovery).quantize(Decimal("0.01"))
            ledger_balance = max(advance.amount_requested - installment_amount, Decimal("0.00"))
            _recovery, created = AdvanceRecovery.objects.update_or_create(
                advance=advance,
                salary=self,
                period_start_date=start_date,
                period_end_date=end_date,
                defaults={
                    "employee": self.employee,
                    "amount": recovery_amount,
                    "installment_amount": installment_amount,
                    "balance": ledger_balance,
                },
            )
            if created:
                recovered_now += recovery_amount
            else:
                recovered_now += max(recovery_amount - current_recovery_amount, Decimal("0.00"))
            remaining_recovery -= recovery_amount

            if advance.outstanding_balance <= 0:
                advance.status = "recovered"
                advance.save(update_fields=["status", "updated_at"])

        return recovered_now

    def __str__(self):
        return f"Salary - {self.employee}"

    class Meta:
        db_table = 'salaries'


class Advance(models.Model):
    """Manages salary advances"""
    advance_id = models.AutoField(primary_key=True)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='advances')
    amount_requested = models.DecimalField(max_digits=10, decimal_places=2)
    installment_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, editable=False, validators=[MinValueValidator(0)])
    
    APPROVAL_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    approval_status = models.CharField(max_length=20, choices=APPROVAL_STATUS_CHOICES, default='pending')
    approved_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='advances_approved')
    disbursement_date = models.DateField(blank=True, null=True)
    
    ADVANCE_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('disbursed', 'Disbursed'),
        ('recovered', 'Recovered'),
    ]
    status = models.CharField(max_length=20, choices=ADVANCE_STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def calculate_installment_amount(self):
        if not self.employee_id:
            return Decimal("0.00")
        try:
            gross_pay = self.employee.salary.gross_pay
        except Salary.DoesNotExist:
            gross_pay = Decimal("0.00")
        return (gross_pay * Decimal("0.33")).quantize(Decimal("0.01"))

    def save(self, *args, **kwargs):
        self.installment_amount = self.calculate_installment_amount()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"installment_amount"}
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Advance - {self.employee} ({self.amount_requested})"

    @property
    def amount_recovered(self):
        return sum(recovery.amount for recovery in self.recoveries.all())

    @property
    def outstanding_balance(self):
        balance = self.amount_requested - self.amount_recovered
        return max(balance, Decimal("0.00"))

    @property
    def balance(self):
        balance = self.amount_requested - self.installment_amount
        return max(balance, Decimal("0.00"))

    class Meta:
        db_table = 'advances'


class AdvanceRecovery(models.Model):
    """Records salary deductions used to recover employee advances."""
    recovery_id = models.AutoField(primary_key=True)
    advance = models.ForeignKey(Advance, on_delete=models.CASCADE, related_name="recoveries")
    salary = models.ForeignKey(Salary, on_delete=models.CASCADE, related_name="advance_recoveries")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="advance_recoveries")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    installment_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    period_start_date = models.DateField()
    period_end_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Advance Recovery - {self.employee} ({self.amount})"

    class Meta:
        db_table = "advance_recoveries"
        constraints = [
            models.UniqueConstraint(
                fields=["advance", "salary", "period_start_date", "period_end_date"],
                name="unique_advance_recovery_per_salary_period",
            ),
        ]


class Invoice(models.Model):
    """Manages client invoices"""
    invoice_id = models.AutoField(primary_key=True)
    invoice_number = models.CharField(max_length=20, unique=True, blank=True, null=True, editable=False)
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name='invoices', blank=True, null=True)
    sites = models.ManyToManyField(Site, blank=True, related_name='invoices')
    billable_products = models.ManyToManyField(ContractDeliverable, blank=True, related_name='invoices')
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='invoices')
    invoice_date = models.DateField()
    due_date = models.DateField()
    billing_start_date = models.DateField(blank=True, null=True)
    billing_end_date = models.DateField(blank=True, null=True)
    description = models.TextField(blank=True)
    deployed_guards = models.IntegerField(default=0, editable=False)
    rate_per_guard = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    contract_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    amendment_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amendment_reason = models.TextField(blank=True)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=18, help_text="Uganda VAT rate percentage applied to taxable invoice charges.")
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    
    INVOICE_STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('paid', 'Paid'),
        ('overdue', 'Overdue'),
        ('cancelled', 'Cancelled'),
    ]
    status = models.CharField(max_length=20, choices=INVOICE_STATUS_CHOICES, default='draft')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def effective_billing_dates(self):
        start_date = self.billing_start_date or self.invoice_date
        end_date = self.billing_end_date or self.due_date
        return start_date, end_date

    def apply_contract_defaults(self):
        if not self.contract_id:
            return
        if not self.invoice_date:
            self.invoice_date = self.contract.contract_start_date
        if not self.due_date:
            self.due_date = self.contract.contract_end_date
        if not self.billing_start_date:
            self.billing_start_date = self.contract.contract_start_date
        if not self.billing_end_date:
            self.billing_end_date = self.contract.contract_end_date

    def invoice_sites(self):
        if not self.contract_id:
            return Site.objects.none()
        if self.pk and self.sites.exists():
            return self.sites.filter(contract=self.contract).order_by("site_name")
        return self.contract.sites.all().order_by("site_name")

    def calculate_deployed_guards(self):
        if not self.contract_id:
            return 0
        sites = list(self.invoice_sites())
        if sites:
            return sum(site.number_of_guards for site in sites)
        return self.contract.number_of_guards

    @property
    def invoiced_sites(self):
        if not self.contract_id:
            return "-"
        site_names = [site.site_name for site in self.invoice_sites()]
        return ", ".join(site_names) if site_names else "All contract sites"

    @property
    def invoiced_site_count(self):
        if not self.contract_id:
            return 0
        return self.invoice_sites().count()

    def invoice_billable_products(self):
        if not self.contract_id:
            return ContractDeliverable.objects.none()
        if self.pk and self.billable_products.exists():
            return self.billable_products.filter(contract=self.contract).order_by("item_name", "deliverable_id")
        return self.contract.deliverables.all().order_by("item_name", "deliverable_id")

    def invoice_line_items(self):
        if not self.contract_id:
            return []
        lines = []
        for site in self.invoice_sites():
            guards = site.number_of_guards
            lines.append({
                "category": "Guarding Services",
                "description": f"Guarding services - {site.site_name}",
                "site": site,
                "quantity": guards,
                "unit": "guard(s)",
                "day_guards": site.day_shift_guards,
                "night_guards": site.night_shift_guards,
                "guards": guards,
                "rate": self.rate_per_guard,
                "amount": guards * self.rate_per_guard,
                "taxable": True,
            })
        if not lines:
            lines.append({
                "category": "Guarding Services",
                "description": f"Guarding services under {self.contract}",
                "site": None,
                "quantity": self.contract.number_of_guards,
                "unit": "guard(s)",
                "day_guards": self.contract.day_shift_guards,
                "night_guards": self.contract.night_shift_guards,
                "guards": self.contract.number_of_guards,
                "rate": self.rate_per_guard,
                "amount": self.contract.number_of_guards * self.rate_per_guard,
                "taxable": True,
            })
        for product in self.invoice_billable_products():
            lines.append({
                "category": "Contract Billable Item",
                "description": product.get_item_name_display(),
                "site": None,
                "quantity": product.quantity,
                "unit": "item(s)",
                "day_guards": "-",
                "night_guards": "-",
                "guards": product.quantity,
                "rate": product.unit_price,
                "amount": product.amount,
                "taxable": True,
            })
        if self.pk:
            for item in self.provisional_items.all().order_by("item_name", "item_id"):
                lines.append({
                    "category": "Provisional Item",
                    "description": item.display_name,
                    "site": None,
                    "quantity": item.quantity,
                    "unit": item.unit,
                    "day_guards": "-",
                    "night_guards": "-",
                    "guards": item.quantity,
                    "rate": item.unit_price,
                    "amount": item.amount,
                    "taxable": item.taxable,
                })
        return lines

    def generate_invoice_number(self):
        if self.invoice_id:
            return f"INV{self.invoice_id:03d}"
        latest = Invoice.objects.order_by("-invoice_id").first()
        next_id = (latest.invoice_id + 1) if latest else 1
        return f"INV{next_id:03d}"

    def build_contract_description(self):
        start_date, end_date = self.effective_billing_dates()
        contract_number = self.contract.contract_number or f"Contract #{self.contract_id}"
        site_count = self.invoice_sites().count() if self.pk else self.contract.sites.count()
        site_text = f" covering {site_count} site(s)" if site_count else ""
        return (
            f"Security services under {contract_number} for {self.contract.client.client_name}{site_text} "
            f"from {start_date} to {end_date}"
        )

    def update_from_contract(self):
        if not self.contract_id:
            return
        self.apply_contract_defaults()
        self.client = self.contract.client
        self.rate_per_guard = self.contract.rate_per_guard
        self.deployed_guards = self.calculate_deployed_guards()
        lines = self.invoice_line_items()
        self.contract_amount = sum((line["amount"] for line in lines), Decimal("0.00"))
        taxable_amount = sum((line["amount"] for line in lines if line.get("taxable", True)), Decimal("0.00"))
        self.tax_amount = (taxable_amount * (self.tax_rate / Decimal("100"))).quantize(Decimal("0.01"))
        self.total_amount = self.contract_amount + self.tax_amount
        self.description = self.build_contract_description()

    def clean(self):
        super().clean()
        if self.contract_id:
            self.client = self.contract.client
            self.apply_contract_defaults()
        if self.invoice_date and self.due_date and self.due_date < self.invoice_date:
            raise ValidationError("Due date cannot be earlier than invoice date.")
        if self.billing_start_date and self.billing_end_date and self.billing_end_date < self.billing_start_date:
            raise ValidationError("Billing end date cannot be earlier than billing start date.")

    def save(self, *args, **kwargs):
        self.update_from_contract()
        self.full_clean()
        self.update_from_contract()
        if not self.invoice_number:
            self.invoice_number = self.generate_invoice_number()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"invoice_number", "client", "invoice_date", "due_date", "billing_start_date", "billing_end_date", "deployed_guards", "rate_per_guard", "contract_amount", "tax_amount", "total_amount", "description"}
        super().save(*args, **kwargs)
        if hasattr(self, "payment_record"):
            self.payment_record.save(update_fields=["total_amount", "status", "updated_at"])

    def __str__(self):
        return f"{self.invoice_number or self.generate_invoice_number()} - {self.client}"

    class Meta:
        db_table = 'invoices'

class InvoiceBillableItemPrice(models.Model):
    """Controlled unit prices for provisional invoice items."""
    ITEM_CHOICES = [
        ('gun', 'Gun'),
        ('radio', 'Radio'),
        ('walk_through_detector', 'Walk Through Detector'),
        ('metal_detector', 'Metal Detector'),
        ('vehicle', 'Vehicle'),
        ('dog', 'Dog'),
        ('other', 'Other'),
    ]

    price_id = models.AutoField(primary_key=True)
    item_name = models.CharField(max_length=100, choices=ITEM_CHOICES, unique=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    taxable = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def active_price_for(cls, item_name):
        return cls.objects.filter(item_name=item_name, active=True).first()

    def __str__(self):
        return f"{self.get_item_name_display()} - {self.unit_price}"

    class Meta:
        db_table = 'invoice_billable_item_prices'
        verbose_name = 'Provisional Billable Item Price'
        verbose_name_plural = 'Provisional Billable Item Prices'

class InvoiceBillableItem(models.Model):
    """Provisional billable items added directly to one invoice."""
    ITEM_CHOICES = [
        ('gun', 'Gun'),
        ('radio', 'Radio'),
        ('walk_through_detector', 'Walk Through Detector'),
        ('metal_detector', 'Metal Detector'),
        ('vehicle', 'Vehicle'),
        ('dog', 'Dog'),
        ('other', 'Other'),
    ]

    item_id = models.AutoField(primary_key=True)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='provisional_items')
    item_name = models.CharField(max_length=100, choices=ITEM_CHOICES)
    description = models.CharField(max_length=255, blank=True)
    quantity = models.IntegerField(default=1, validators=[MinValueValidator(1)])
    unit = models.CharField(max_length=30, default='item(s)')
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    taxable = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def display_name(self):
        base = self.get_item_name_display()
        return f"{base} - {self.description}" if self.description else base

    def save(self, *args, **kwargs):
        price_config = InvoiceBillableItemPrice.active_price_for(self.item_name)
        if price_config:
            self.unit_price = price_config.unit_price
            self.taxable = price_config.taxable
        self.amount = self.quantity * self.unit_price
        super().save(*args, **kwargs)
        self.invoice.save(update_fields=["contract_amount", "tax_amount", "total_amount", "description", "updated_at"])

    def delete(self, *args, **kwargs):
        invoice = self.invoice
        super().delete(*args, **kwargs)
        invoice.save(update_fields=["contract_amount", "tax_amount", "total_amount", "description", "updated_at"])

    def __str__(self):
        invoice_number = self.invoice.invoice_number or self.invoice.generate_invoice_number()
        return f"{invoice_number} - {self.display_name}"

    class Meta:
        db_table = 'invoice_billable_items'
class Paymee(models.Model):
    """Invoice receivables ledger synced from invoices and payment receipts."""
    invoice = models.OneToOneField(Invoice, on_delete=models.CASCADE, primary_key=True, related_name='payment_record')
    client = models.ForeignKey(Client, on_delete=models.CASCADE, null=True, blank=True, related_name='payment_records', editable=False)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    due_date = models.DateField(blank=True, null=True, editable=False)
    last_payment_date = models.DateField(blank=True, null=True, editable=False)
    payment_terms = models.CharField(max_length=100, default='Due on receipt')
    currency = models.CharField(max_length=10, default='UGX')

    PAYMENT_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('partial', 'Partially Paid'),
        ('paid', 'Paid'),
        ('overdue', 'Overdue'),
        ('overpaid', 'Overpaid'),
        ('cancelled', 'Cancelled'),
    ]
    status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='pending', editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def balance_amount(self):
        return max(self.total_amount - self.amount_paid, Decimal('0.00'))

    @property
    def overpaid_amount(self):
        return max(self.amount_paid - self.total_amount, Decimal('0.00'))

    @property
    def aging_days(self):
        if not self.due_date or self.status in ('paid', 'overpaid', 'cancelled'):
            return 0
        return max((timezone.localdate() - self.due_date).days, 0)

    def calculate_amount_paid(self):
        if not self.invoice_id:
            return Decimal('0.00')
        return sum((payment.amount for payment in self.invoice.payments.all()), Decimal('0.00'))

    def calculate_last_payment_date(self):
        if not self.invoice_id:
            return None
        latest_payment = self.invoice.payments.order_by('-payment_date', '-payment_id').first()
        return latest_payment.payment_date if latest_payment else None

    def calculate_status(self):
        if self.invoice_id and self.invoice.status == 'cancelled':
            return 'cancelled'
        if self.total_amount <= 0:
            return 'pending'
        if self.amount_paid > self.total_amount:
            return 'overpaid'
        if self.amount_paid >= self.total_amount:
            return 'paid'
        if self.amount_paid > 0:
            return 'partial'
        if self.due_date and self.due_date < timezone.localdate():
            return 'overdue'
        return 'pending'

    def refresh_from_invoice(self):
        if not self.invoice_id:
            return
        self.client = self.invoice.client
        self.total_amount = self.invoice.total_amount
        self.due_date = self.invoice.due_date
        self.amount_paid = self.calculate_amount_paid()
        self.last_payment_date = self.calculate_last_payment_date()
        self.status = self.calculate_status()

    def save(self, *args, **kwargs):
        self.refresh_from_invoice()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"client", "total_amount", "amount_paid", "due_date", "last_payment_date", "status"}
        super().save(*args, **kwargs)

    def __str__(self):
        invoice_number = self.invoice.invoice_number or self.invoice.generate_invoice_number()
        return f"Receivable - {invoice_number} ({self.get_status_display()})"

    class Meta:
        db_table = 'paymees'
        verbose_name = 'Receivable'
        verbose_name_plural = 'Receivables'


class Payment(models.Model):
    """Manages individual payments"""
    payment_id = models.AutoField(primary_key=True)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='payments')
    payment_date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    
    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('check', 'Check'),
        ('bank_transfer', 'Bank Transfer'),
        ('credit_card', 'Credit Card'),
        ('other', 'Other'),
    ]
    payment_method = models.CharField(max_length=50, choices=PAYMENT_METHOD_CHOICES)
    transaction_ref = models.CharField(max_length=100, blank=True, null=True)
    remarks = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def refresh_payment_record(self):
        if not self.invoice_id:
            return
        payment_record, _created = Paymee.objects.get_or_create(invoice=self.invoice)
        payment_record.save(update_fields=["client", "total_amount", "amount_paid", "due_date", "last_payment_date", "status", "updated_at"])

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.refresh_payment_record()

    def delete(self, *args, **kwargs):
        invoice = self.invoice if self.invoice_id else None
        result = super().delete(*args, **kwargs)
        if invoice:
            payment_record, _created = Paymee.objects.get_or_create(invoice=invoice)
            payment_record.save(update_fields=["client", "total_amount", "amount_paid", "due_date", "last_payment_date", "status", "updated_at"])
        return result

    def __str__(self):
        return f"Payment - {self.amount} on {self.payment_date}"

    class Meta:
        db_table = 'payments'


class Supplier(models.Model):
    """Approved supplier master data used by procurement and accounts payable."""
    supplier_id = models.AutoField(primary_key=True)
    supplier_code = models.CharField(max_length=30, unique=True, blank=True, null=True, editable=False)
    supplier_name = models.CharField(max_length=255)
    contact_person = models.CharField(max_length=255, blank=True, default="")
    phone_number = models.CharField(max_length=30, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    address = models.TextField(blank=True, default="")
    tax_identification_number = models.CharField(max_length=80, blank=True, default="")
    bank_name = models.CharField(max_length=120, blank=True, default="")
    bank_account_name = models.CharField(max_length=120, blank=True, default="")
    bank_account_number = models.CharField(max_length=80, blank=True, default="")
    STATUS_CHOICES = [
        ("active", "Active"),
        ("suspended", "Suspended"),
        ("inactive", "Inactive"),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    due_diligence_notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def generate_supplier_code(self):
        return f"SUP-{self.supplier_id:05d}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.supplier_code:
            self.supplier_code = self.generate_supplier_code()
            super().save(update_fields=["supplier_code"])

    def __str__(self):
        return f"{self.supplier_code or 'Supplier'} - {self.supplier_name}"

    class Meta:
        db_table = "suppliers"
        ordering = ["supplier_name"]


class Budget(models.Model):
    """Budget requisition, approval, control, and accountability record."""
    budget_id = models.AutoField(primary_key=True)
    budget_code = models.CharField(max_length=30, unique=True, blank=True, null=True, editable=False)
    budget_title = models.CharField(max_length=255, default="Department Budget")

    DEPARTMENT_CHOICES = [
        ('operations', 'Operations'),
        ('hr', 'Human Resources'),
        ('finance', 'Finance'),
        ('admin', 'Administration'),
    ]
    department = models.CharField(max_length=50, choices=DEPARTMENT_CHOICES)
    CATEGORY_CHOICES = [
        ('payroll', 'Payroll'),
        ('operations', 'Operations'),
        ('training', 'Training'),
        ('equipment', 'Equipment'),
        ('admin', 'Administration'),
        ('other', 'Other'),
    ]
    budget_category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='operations')
    fiscal_year = models.IntegerField(default=timezone.localdate().year)
    year = models.IntegerField(default=timezone.localdate().year)
    period_start = models.DateField(default=timezone.localdate)
    period_end = models.DateField(default=timezone.localdate)
    requested_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    allocated_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    spent_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    low_balance_threshold = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("100000.00"), validators=[MinValueValidator(0)])
    requisition_reason = models.TextField(blank=True, default='')
    requested_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='budget_requisitions_requested')
    verified_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='budget_requisitions_verified')
    approved_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='budget_requisitions_approved')
    WORKFLOW_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('verified', 'Verified'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    verification_status = models.CharField(max_length=20, choices=WORKFLOW_STATUS_CHOICES, default='pending')
    approval_status = models.CharField(max_length=20, choices=WORKFLOW_STATUS_CHOICES, default='pending')
    ACCOUNTABILITY_STATUS_CHOICES = [
        ('not_due', 'Not Due'),
        ('pending_expense', 'Pending Expense Record'),
        ('partially_accounted', 'Partially Accounted'),
        ('accounted', 'Fully Accounted'),
        ('over_spent', 'Over Spent'),
    ]
    accountability_status = models.CharField(max_length=30, choices=ACCOUNTABILITY_STATUS_CHOICES, default='not_due', editable=False)
    approved_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def remaining_amount(self):
        return self.allocated_amount - self.spent_amount

    @property
    def utilization_rate(self):
        if self.allocated_amount <= 0:
            return Decimal("0.00")
        return ((self.spent_amount / self.allocated_amount) * Decimal("100.00")).quantize(Decimal("0.01"))

    @property
    def expense_record_due_at(self):
        if not self.approved_at:
            return None
        return self.approved_at + timedelta(hours=12)

    def generate_budget_code(self):
        return f"BUD-{self.fiscal_year}-{self.budget_id:05d}"

    def update_spent_amount(self):
        expense_model = globals().get("Expense")
        if not expense_model or not self.pk:
            return
        self.spent_amount = sum((expense.amount for expense in expense_model.objects.filter(budget=self).exclude(status="rejected")), Decimal("0.00"))

    def update_accountability_status(self):
        if self.approval_status != 'approved':
            self.accountability_status = 'not_due'
        elif self.spent_amount > self.allocated_amount:
            self.accountability_status = 'over_spent'
        elif self.spent_amount >= self.allocated_amount and self.allocated_amount > 0:
            self.accountability_status = 'accounted'
        elif self.spent_amount > 0:
            self.accountability_status = 'partially_accounted'
        elif self.expense_record_due_at and timezone.now() >= self.expense_record_due_at:
            self.accountability_status = 'pending_expense'
        else:
            self.accountability_status = 'not_due'

    def should_notify_low_balance(self):
        return self.approval_status == 'approved' and self.allocated_amount > 0 and self.remaining_amount <= self.low_balance_threshold

    def create_notification(self, recipient, recipient_group, notification_type, message):
        notification_model = globals().get("BudgetNotification")
        if not notification_model or not recipient:
            return None
        notification, _created = notification_model.objects.get_or_create(
            budget=self,
            recipient=recipient,
            notification_type=notification_type,
            defaults={
                "recipient_group": recipient_group,
                "message": message,
                "status": "sent",
                "delivery_note": "Saved in system notifications.",
                "notified_at": timezone.now(),
            },
        )
        return notification

    def create_audit_notifications(self):
        if self.should_notify_low_balance():
            message = f"Budget {self.budget_code} balance is {self.remaining_amount}. Threshold is {self.low_balance_threshold}."
            for employee in Employee.objects.filter(status="active").filter(models.Q(role="finance_officer") | models.Q(department="finance")).distinct():
                self.create_notification(employee, "Finance", "low_balance", message)

    def clean(self):
        super().clean()
        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise ValidationError("Budget period end cannot be earlier than period start.")
        if self.allocated_amount and self.requested_amount and self.allocated_amount > self.requested_amount:
            raise ValidationError("Allocated amount cannot exceed requested amount.")
        if self.approval_status == 'approved' and not self.approved_by_id:
            raise ValidationError("Approved budgets require an approver.")
        if self.verification_status == 'verified' and not self.verified_by_id:
            raise ValidationError("Verified budgets require a verifier.")

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
        if self.fiscal_year and self.year != self.fiscal_year:
            self.year = self.fiscal_year
            if update_fields is not None:
                update_fields.add("year")
        if self.approval_status == 'approved' and not self.approved_at:
            self.approved_at = timezone.now()
            if update_fields is not None:
                update_fields.add("approved_at")
        self.update_spent_amount()
        if update_fields is not None:
            update_fields.update(["spent_amount"])
            kwargs["update_fields"] = list(update_fields)
        self.full_clean()
        super().save(*args, **kwargs)
        if not self.budget_code:
            self.budget_code = self.generate_budget_code()
            super().save(update_fields=["budget_code"])
        self.create_audit_notifications()

    def __str__(self):
        return f"{self.budget_code or 'Budget'} - {self.budget_title}"

    class Meta:
        db_table = 'budgets'
        unique_together = ('department', 'year')


class Expense(models.Model):
    """Expense requisition and accountability record linked to an approved budget."""
    expense_id = models.AutoField(primary_key=True)
    expense_code = models.CharField(max_length=30, unique=True, blank=True, null=True, editable=False)
    budget = models.ForeignKey(Budget, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses')

    CATEGORY_CHOICES = Budget.CATEGORY_CHOICES
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    requisition_title = models.CharField(max_length=255, default="Expense Requisition")
    requested_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    expense_date = models.DateField()
    vendor_payee = models.CharField(max_length=255, blank=True, default='')
    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('mobile_money', 'Mobile Money'),
        ('bank_transfer', 'Bank Transfer'),
        ('cheque', 'Cheque'),
        ('card', 'Card'),
        ('other', 'Other'),
    ]
    payment_method = models.CharField(max_length=50, choices=PAYMENT_METHOD_CHOICES, blank=True, default='')
    requisition_reason = models.TextField(blank=True, default='')
    description = models.TextField()
    receipt_reference = models.CharField(max_length=100, blank=True, default='')
    accountability_notes = models.TextField(blank=True, default='')
    requested_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='expense_requisitions_requested')
    spent_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses_submitted')
    verified_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses_verified')
    approved_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses_approved')
    WORKFLOW_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('verified', 'Verified'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    verification_status = models.CharField(max_length=20, choices=WORKFLOW_STATUS_CHOICES, default='pending')
    approval_status = models.CharField(max_length=20, choices=WORKFLOW_STATUS_CHOICES, default='pending')
    EXPENSE_STATUS_CHOICES = [
        ('requisition', 'Requisition'),
        ('recorded', 'Recorded'),
        ('verified', 'Verified'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    status = models.CharField(max_length=20, choices=EXPENSE_STATUS_CHOICES, default='requisition')
    ACCOUNTABILITY_STATUS_CHOICES = [
        ('not_due', 'Not Due'),
        ('pending_accountability', 'Pending Accountability'),
        ('submitted', 'Submitted'),
        ('verified', 'Verified'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('overdue', 'Overdue'),
    ]
    accountability_status = models.CharField(max_length=30, choices=ACCOUNTABILITY_STATUS_CHOICES, default='not_due', editable=False)
    approved_at = models.DateTimeField(blank=True, null=True)
    accounted_at = models.DateTimeField(blank=True, null=True)
    recorded_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def accountability_due_at(self):
        if not self.approved_at:
            return None
        return self.approved_at + timedelta(hours=12)

    @property
    def variance_amount(self):
        return self.requested_amount - self.amount

    def generate_expense_code(self):
        return f"EXP-{self.expense_date.year}-{self.expense_id:05d}"

    def has_accountability_evidence(self):
        return bool(self.amount > 0 or self.receipt_reference or self.accountability_notes)

    def update_accountability_status(self):
        if self.approval_status != 'approved':
            self.accountability_status = 'not_due'
        elif self.status == 'rejected':
            self.accountability_status = 'rejected'
        elif self.status == 'approved':
            self.accountability_status = 'approved'
        elif self.status == 'verified':
            self.accountability_status = 'verified'
        elif self.has_accountability_evidence():
            self.accountability_status = 'submitted'
            if not self.accounted_at:
                self.accounted_at = timezone.now()
        elif self.accountability_due_at and timezone.now() >= self.accountability_due_at:
            self.accountability_status = 'overdue'
        else:
            self.accountability_status = 'pending_accountability'

    def should_notify_missing_accountability(self):
        return self.accountability_status == 'overdue'

    def create_accountability_notifications(self):
        notification_model = globals().get("ExpenseNotification")
        if not notification_model or not self.should_notify_missing_accountability():
            return
        message = f"Expense {self.expense_code} for {self.requisition_title} was approved more than 12 hours ago and accountability evidence has not been submitted."
        for employee in Employee.objects.filter(status="active").filter(models.Q(role="hr_officer") | models.Q(department="hr")).distinct():
            notification_model.objects.get_or_create(
                expense=self,
                recipient=employee,
                notification_type="missing_accountability",
                defaults={
                    "recipient_group": "Human Resources",
                    "message": message,
                    "status": "sent",
                    "delivery_note": "Saved in system notifications.",
                    "notified_at": timezone.now(),
                },
            )

    def clean(self):
        super().clean()
        if self.budget_id and self.budget.approval_status != 'approved':
            raise ValidationError("Expenses can only be requisitioned against approved budgets.")
        if self.requested_amount and self.amount and self.amount > self.requested_amount:
            raise ValidationError("Accounted expense amount cannot exceed the requisition amount.")
        if self.verification_status == 'verified' and not self.verified_by_id:
            raise ValidationError("Verified expense requisitions require a verifier.")
        if self.approval_status == 'approved' and not self.approved_by_id:
            raise ValidationError("Approved expense requisitions require an approver.")
        if self.status == 'verified' and not self.verified_by_id:
            raise ValidationError("Verified expense accountability requires a verifier.")
        if self.status == 'approved' and not self.approved_by_id:
            raise ValidationError("Approved expense accountability requires an approver.")

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
        if self.approval_status == 'approved' and not self.approved_at:
            self.approved_at = timezone.now()
            if update_fields is not None:
                update_fields.add("approved_at")
        self.update_accountability_status()
        if update_fields is not None:
            update_fields.update(["accountability_status", "accounted_at"])
            kwargs["update_fields"] = list(update_fields)
        self.full_clean()
        super().save(*args, **kwargs)
        if not self.expense_code:
            self.expense_code = self.generate_expense_code()
            super().save(update_fields=["expense_code"])
        if self.budget_id:
            self.budget.save(update_fields=["spent_amount", "updated_at"])
        self.create_accountability_notifications()

    
    def delete(self, *args, **kwargs):
        budget = self.budget if self.budget_id else None
        result = super().delete(*args, **kwargs)
        if budget:
            budget.save(update_fields=["spent_amount", "updated_at"])
        return result

    def __str__(self):
        return f"{self.expense_code or 'Expense'} - {self.requisition_title}"

    class Meta:
        db_table = 'expenses'


class ProcurementRequisition(models.Model):
    """Internal procurement request that starts the purchasing workflow."""
    requisition_id = models.AutoField(primary_key=True)
    requisition_number = models.CharField(max_length=30, unique=True, blank=True, null=True, editable=False)
    budget = models.ForeignKey(Budget, on_delete=models.SET_NULL, null=True, blank=True, related_name="procurement_requisitions")
    title = models.CharField(max_length=255)
    category = models.CharField(max_length=50, choices=Budget.CATEGORY_CHOICES, default="operations")
    description = models.TextField()
    requested_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="procurement_requisitions_requested")
    approval_assigned_to = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="procurement_requisitions_to_approve")
    viewer = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="procurement_requisitions_to_view")
    preferred_supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True, related_name="preferred_procurement_requisitions")
    department = models.CharField(max_length=50, choices=Budget.DEPARTMENT_CHOICES, default="operations")
    required_date = models.DateField()
    estimated_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    justification = models.TextField(blank=True, default="")
    supplier_contacted_at = models.DateTimeField(blank=True, null=True)
    WORKFLOW_STATUS_CHOICES = [
        ("draft", "Draft"),
        ("submitted", "Submitted"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("supplier_contacted", "Supplier Contacted"),
        ("proforma_received", "Proforma Received"),
        ("converted", "LPO Generated"),
        ("cancelled", "Cancelled"),
    ]
    status = models.CharField(max_length=30, choices=WORKFLOW_STATUS_CHOICES, default="draft")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def approved_amount(self):
        return sum((approval.approved_amount for approval in self.approvals.filter(decision="approved")), Decimal("0.00"))

    def generate_requisition_number(self):
        return f"REQ-{timezone.localdate().year}-{self.requisition_id:05d}"

    @property
    def can_contact_supplier(self):
        return self.status == "approved" and self.preferred_supplier_id and not self.supplier_contacted_at

    def notify(self, recipient, recipient_group, notification_type, message):
        notification_model = globals().get("ProcurementNotification")
        if not notification_model or not recipient or not self.pk:
            return None
        notification, _created = notification_model.objects.get_or_create(
            recipient=recipient,
            notification_type=notification_type,
            related_model=self._meta.model_name,
            related_object_id=self.pk,
            defaults={
                "recipient_group": recipient_group,
                "message": message,
                "status": "sent",
                "notified_at": timezone.now(),
            },
        )
        return notification

    def notify_submission(self):
        if self.status != "submitted" or not self.pk:
            return
        message = f"Procurement requisition {self.requisition_number} requires approval: {self.title}."
        if self.approval_assigned_to_id:
            self.notify(self.approval_assigned_to, "Approver", "requisition_submitted", message)
            return
        approvers = Employee.objects.filter(status="active").filter(
            models.Q(role__in=("finance_officer", "administrator", "manager")) | models.Q(department="finance")
        ).distinct()
        for employee in approvers:
            self.notify(employee, "Approver", "requisition_submitted", message)

    def mark_supplier_contacted(self):
        if not self.preferred_supplier_id:
            raise ValidationError("Choose a preferred supplier before contacting the supplier.")
        if self.status != "approved":
            raise ValidationError("Only approved requisitions can be sent to suppliers.")
        self.supplier_contacted_at = timezone.now()
        self.status = "supplier_contacted"
        self.save(update_fields=["supplier_contacted_at", "status", "updated_at"])
        message = f"Supplier {self.preferred_supplier} has been contacted for requisition {self.requisition_number}."
        self.notify(self.requested_by, "Requester", "supplier_contacted", message)
        self.notify(self.viewer, "Viewer", "supplier_contacted", message)

    def clean(self):
        super().clean()
        if self.budget_id and self.budget.approval_status != "approved":
            raise ValidationError("Procurement requisitions can only use approved budgets.")
        if self.budget_id and self.estimated_amount > self.budget.remaining_amount:
            raise ValidationError("Estimated procurement amount cannot exceed the selected budget balance.")
        if self.status in ("draft", "submitted") and self.required_date and self.required_date < timezone.localdate():
            raise ValidationError("Required date cannot be in the past.")
        if self.status not in ("draft", "cancelled") and not self.requested_by_id:
            raise ValidationError("Submitted procurement requisitions require a requester.")
        if self.status in ("supplier_contacted", "proforma_received", "converted") and not self.preferred_supplier_id:
            raise ValidationError("Supplier workflow stages require a preferred supplier.")

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            kwargs["update_fields"] = list(update_fields)
        self.full_clean()
        super().save(*args, **kwargs)
        if not self.requisition_number:
            self.requisition_number = self.generate_requisition_number()
            super().save(update_fields=["requisition_number"])
        self.notify_submission()

    def __str__(self):
        return f"{self.requisition_number or 'Requisition'} - {self.title}"

    class Meta:
        db_table = "procurement_requisitions"
        ordering = ["-created_at", "-requisition_id"]


class ProcurementApproval(models.Model):
    """Approval decision for a procurement requisition."""
    approval_id = models.AutoField(primary_key=True)
    requisition = models.ForeignKey(ProcurementRequisition, on_delete=models.CASCADE, related_name="approvals")
    approved_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="procurement_approvals")
    DECISION_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("needs_revision", "Needs Revision"),
    ]
    decision = models.CharField(max_length=20, choices=DECISION_CHOICES, default="pending")
    approved_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    comments = models.TextField(blank=True, default="")
    decided_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        if self.decision == "approved" and not self.approved_by_id:
            raise ValidationError("Approved procurement decisions require an approver.")
        if self.decision == "approved" and self.approved_amount <= 0:
            raise ValidationError("Approved procurement amount must be greater than zero.")
        if self.requisition_id and self.approved_amount > self.requisition.estimated_amount:
            raise ValidationError("Approved amount cannot exceed requisition estimate.")

    def save(self, *args, **kwargs):
        if self.decision in ("approved", "rejected", "needs_revision") and not self.decided_at:
            self.decided_at = timezone.now()
        self.full_clean()
        super().save(*args, **kwargs)
        if self.decision == "approved" and self.requisition.status not in ("supplier_contacted", "proforma_received", "converted"):
            self.requisition.status = "approved"
            self.requisition.save(update_fields=["status", "updated_at"])
            message = f"Procurement requisition {self.requisition.requisition_number} has been approved. Contact the supplier to request a proforma invoice."
            self.requisition.notify(self.requisition.requested_by, "Requester", "requisition_approved", message)
            self.requisition.notify(self.requisition.viewer, "Viewer", "requisition_approved", message)
        elif self.decision == "rejected":
            self.requisition.status = "rejected"
            self.requisition.save(update_fields=["status", "updated_at"])
            self.requisition.notify(self.requisition.requested_by, "Requester", "requisition_rejected", f"Procurement requisition {self.requisition.requisition_number} was rejected.")

    def __str__(self):
        return f"{self.requisition} - {self.get_decision_display()}"

    class Meta:
        db_table = "procurement_approvals"
        ordering = ["-created_at"]


class SupplierProformaInvoice(models.Model):
    """Supplier proforma invoice received before an LPO is generated."""
    proforma_id = models.AutoField(primary_key=True)
    proforma_number = models.CharField(max_length=30, unique=True, blank=True, null=True, editable=False)
    supplier_reference = models.CharField(max_length=100, blank=True, default="")
    requisition = models.ForeignKey(ProcurementRequisition, on_delete=models.PROTECT, related_name="proforma_invoices")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="proforma_invoices")
    proforma_date = models.DateField(default=timezone.localdate)
    valid_until = models.DateField(blank=True, null=True)
    subtotal_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    delivery_terms = models.CharField(max_length=255, blank=True, default="")
    PAYMENT_TERMS_CHOICES = [("on_delivery", "On Delivery"), ("net_7", "Net 7"), ("net_15", "Net 15"), ("net_30", "Net 30")]
    payment_terms = models.CharField(max_length=20, choices=PAYMENT_TERMS_CHOICES, default="net_15")
    STATUS_CHOICES = [("received", "Received"), ("accepted", "Accepted"), ("rejected", "Rejected")]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="received")
    purchase_order = models.OneToOneField("PurchaseOrder", on_delete=models.SET_NULL, null=True, blank=True, related_name="proforma_invoice")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def generate_proforma_number(self):
        return f"PROF-{timezone.localdate().year}-{self.proforma_id:05d}"

    def update_totals_from_items(self):
        if not self.pk:
            self.subtotal_amount = Decimal("0.00")
            self.tax_amount = Decimal("0.00")
            self.total_amount = Decimal("0.00")
            return
        active_items = self.items.all()
        self.subtotal_amount = sum((item.line_total for item in active_items), Decimal("0.00"))
        self.tax_amount = sum((item.tax_amount for item in active_items), Decimal("0.00"))
        self.total_amount = self.subtotal_amount + self.tax_amount

    def has_items(self):
        return bool(self.pk and self.items.exists())

    def clean(self):
        super().clean()
        if self.status == "accepted" and self.pk and not self.has_items():
            raise ValidationError("Accepted proforma invoices require at least one item line.")
        if self.requisition_id and self.requisition.status not in ("supplier_contacted", "proforma_received", "converted"):
            raise ValidationError("Proforma invoices require an approved requisition where the supplier has been contacted.")
        if self.requisition_id and self.supplier_id and self.requisition.preferred_supplier_id and self.requisition.preferred_supplier_id != self.supplier_id:
            raise ValidationError("Proforma supplier must match the requisition supplier.")
        if self.valid_until and self.valid_until < self.proforma_date:
            raise ValidationError("Proforma validity cannot end before the proforma date.")
        if self.total_amount and self.requisition_id:
            approved_limit = self.requisition.approved_amount or self.requisition.estimated_amount
            if approved_limit and self.total_amount > approved_limit:
                raise ValidationError("Proforma total cannot exceed the approved requisition amount.")

    def create_purchase_order(self):
        if self.purchase_order_id or self.status != "accepted":
            return self.purchase_order
        purchase_order = PurchaseOrder.objects.create(
            requisition=self.requisition,
            supplier=self.supplier,
            order_date=timezone.localdate(),
            expected_delivery_date=self.requisition.required_date,
            subtotal_amount=self.subtotal_amount,
            tax_amount=self.tax_amount,
            payment_terms=self.payment_terms,
            status="issued",
            prepared_by=self.requisition.approval_assigned_to or self.requisition.requested_by,
            notes=f"Automatically generated from proforma invoice {self.proforma_number}.",
        )
        self.purchase_order = purchase_order
        super().save(update_fields=["purchase_order", "updated_at"])
        message = f"LPO {purchase_order.po_number} was automatically generated from proforma {self.proforma_number}."
        self.requisition.notify(self.requisition.requested_by, "Requester", "lpo_generated", message)
        self.requisition.notify(self.requisition.viewer, "Viewer", "lpo_generated", message)
        return purchase_order

    def save(self, *args, **kwargs):
        self.update_totals_from_items()
        self.full_clean()
        super().save(*args, **kwargs)
        if not self.proforma_number:
            self.proforma_number = self.generate_proforma_number()
            update_fields = ["proforma_number"]
            if not self.supplier_reference:
                self.supplier_reference = self.proforma_number
                update_fields.append("supplier_reference")
            super().save(update_fields=update_fields)
        if self.requisition.status == "supplier_contacted":
            self.requisition.status = "proforma_received"
            self.requisition.save(update_fields=["status", "updated_at"])
        update_fields = kwargs.get("update_fields")
        totals_only_update = update_fields is not None and set(update_fields).issubset({"subtotal_amount", "tax_amount", "total_amount", "updated_at"})
        if self.status == "accepted" and self.has_items() and not totals_only_update:
            self.create_purchase_order()

    def __str__(self):
        return f"Proforma {self.proforma_number} - {self.supplier}"

    class Meta:
        db_table = "supplier_proforma_invoices"
        ordering = ["-proforma_date", "-proforma_id"]
        constraints = [models.UniqueConstraint(fields=["supplier", "supplier_reference"], condition=~models.Q(supplier_reference=""), name="unique_supplier_reference_number")]


class SupplierProformaItemPrice(models.Model):
    """Controlled item catalog for supplier proforma line pricing."""
    item_id = models.AutoField(primary_key=True)
    item_name = models.CharField(max_length=255, unique=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))])
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(0), MaxValueValidator(100)])
    discount_allowed = models.BooleanField(default=False)
    max_discount_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(0), MaxValueValidator(100)])
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def active_price_for(cls, item_id):
        if not item_id:
            return None
        return cls.objects.filter(pk=item_id, active=True).first()

    def __str__(self):
        return self.item_name

    class Meta:
        db_table = "supplier_proforma_item_prices"
        ordering = ["item_name"]


class SupplierProformaInvoiceItem(models.Model):
    """Line item used to calculate supplier proforma totals."""
    item_id = models.AutoField(primary_key=True)
    proforma = models.ForeignKey(SupplierProformaInvoice, on_delete=models.CASCADE, related_name="items")
    catalog_item = models.ForeignKey(SupplierProformaItemPrice, on_delete=models.PROTECT, null=True, blank=True, related_name="proforma_items")
    description = models.CharField(max_length=255, editable=False)
    quantity = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))])
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(0), MaxValueValidator(100)])
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @staticmethod
    def discount_rate_for_quantity(quantity):
        quantity = quantity or Decimal("0.00")
        if Decimal("100.00") <= quantity <= Decimal("250.00"):
            return Decimal("10.00")
        if Decimal("300.00") <= quantity <= Decimal("450.00"):
            return Decimal("20.00")
        if quantity >= Decimal("500.00"):
            return Decimal("30.00")
        return Decimal("0.00")

    def calculated_discount_amount(self):
        if not self.catalog_item_id or not self.catalog_item.discount_allowed:
            return Decimal("0.00")
        gross_amount = (self.quantity or Decimal("0.00")) * (self.unit_price or Decimal("0.00"))
        discount_rate = self.discount_rate_for_quantity(self.quantity)
        return (gross_amount * (discount_rate / Decimal("100"))).quantize(Decimal("0.01"))

    def clean(self):
        super().clean()
        if not self.catalog_item_id:
            raise ValidationError("Choose an item from the catalog.")
        gross_amount = (self.quantity or Decimal("0.00")) * (self.unit_price or Decimal("0.00"))
        if self.discount_amount and self.discount_amount > gross_amount:
            raise ValidationError("Item discount cannot exceed quantity multiplied by unit price.")
        if self.discount_amount and self.catalog_item_id and not self.catalog_item.discount_allowed:
            raise ValidationError("Discount is not allowed for the selected item.")

    def save(self, *args, **kwargs):
        if self.catalog_item_id:
            self.description = self.catalog_item.item_name
            self.unit_price = self.catalog_item.unit_price
            self.tax_rate = self.catalog_item.tax_rate
        gross_amount = (self.quantity or Decimal("0.00")) * (self.unit_price or Decimal("0.00"))
        self.discount_amount = self.calculated_discount_amount()
        self.line_total = (gross_amount - (self.discount_amount or Decimal("0.00"))).quantize(Decimal("0.01"))
        self.tax_amount = (self.line_total * ((self.tax_rate or Decimal("0.00")) / Decimal("100"))).quantize(Decimal("0.01"))
        self.total_amount = self.line_total + self.tax_amount
        self.full_clean()
        super().save(*args, **kwargs)
        self.proforma.save(update_fields=["subtotal_amount", "tax_amount", "total_amount", "updated_at"])

    def delete(self, *args, **kwargs):
        proforma = self.proforma if self.proforma_id else None
        result = super().delete(*args, **kwargs)
        if proforma:
            proforma.save(update_fields=["subtotal_amount", "tax_amount", "total_amount", "updated_at"])
        return result

    def __str__(self):
        return f"{self.description} - {self.proforma}"

    class Meta:
        db_table = "supplier_proforma_invoice_items"
        ordering = ["item_id"]


class PurchaseOrder(models.Model):
    """Supplier purchase order generated from an approved requisition."""
    purchase_order_id = models.AutoField(primary_key=True)
    po_number = models.CharField(max_length=30, unique=True, blank=True, null=True, editable=False)
    requisition = models.ForeignKey(ProcurementRequisition, on_delete=models.PROTECT, related_name="purchase_orders")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    order_date = models.DateField(default=timezone.localdate)
    expected_delivery_date = models.DateField()
    currency = models.CharField(max_length=10, default="UGX")
    subtotal_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    PAYMENT_TERMS_CHOICES = [
        ("on_delivery", "On Delivery"),
        ("net_7", "Net 7"),
        ("net_15", "Net 15"),
        ("net_30", "Net 30"),
    ]
    payment_terms = models.CharField(max_length=20, choices=PAYMENT_TERMS_CHOICES, default="net_15")
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("issued", "Issued"),
        ("partially_received", "Partially Received"),
        ("received", "Received"),
        ("invoiced", "Invoiced"),
        ("closed", "Closed"),
        ("cancelled", "Cancelled"),
    ]
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="draft")
    prepared_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="purchase_orders_prepared")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def generate_po_number(self):
        return f"LPO-{self.order_date.year}-{self.purchase_order_id:05d}"

    def clean(self):
        super().clean()
        if self.requisition_id and self.requisition.status not in ("approved", "supplier_contacted", "proforma_received", "converted"):
            raise ValidationError("Purchase orders require an approved procurement requisition.")
        if self.supplier_id and self.supplier.status != "active":
            raise ValidationError("Purchase orders can only be issued to active suppliers.")
        if self.expected_delivery_date and self.order_date and self.expected_delivery_date < self.order_date:
            raise ValidationError("Expected delivery date cannot be earlier than order date.")
        if self.status in ("issued", "partially_received", "received", "invoiced", "closed") and not self.prepared_by_id:
            raise ValidationError("Issued purchase orders require a preparer.")

    def save(self, *args, **kwargs):
        self.total_amount = self.subtotal_amount + self.tax_amount
        self.full_clean()
        super().save(*args, **kwargs)
        if not self.po_number:
            self.po_number = self.generate_po_number()
            super().save(update_fields=["po_number"])
        if self.requisition.status != "converted":
            self.requisition.status = "converted"
            self.requisition.save(update_fields=["status", "updated_at"])

    def __str__(self):
        return f"{self.po_number or 'LPO'} - {self.supplier}"

    class Meta:
        db_table = "purchase_orders"
        ordering = ["-order_date", "-purchase_order_id"]


class GoodsReceivedNote(models.Model):
    """Goods received confirmation against a purchase order."""
    grn_id = models.AutoField(primary_key=True)
    grn_number = models.CharField(max_length=30, unique=True, blank=True, null=True, editable=False)
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="goods_received_notes")
    received_date = models.DateField(default=timezone.localdate)
    received_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="goods_received")
    delivery_note_number = models.CharField(max_length=100, blank=True, default="")
    quantity_summary = models.CharField(max_length=255)
    condition_notes = models.TextField(blank=True, default="")
    STATUS_CHOICES = [
        ("pending_inspection", "Pending Inspection"),
        ("accepted", "Accepted"),
        ("partially_accepted", "Partially Accepted"),
        ("rejected", "Rejected"),
    ]
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="pending_inspection")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def generate_grn_number(self):
        return f"GRN-{self.received_date.year}-{self.grn_id:05d}"

    def clean(self):
        super().clean()
        if self.purchase_order_id and self.purchase_order.status not in ("issued", "partially_received", "received"):
            raise ValidationError("Goods can only be received against an issued purchase order.")
        if self.status in ("accepted", "partially_accepted") and not self.received_by_id:
            raise ValidationError("Accepted goods received notes require a receiver.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        if not self.grn_number:
            self.grn_number = self.generate_grn_number()
            super().save(update_fields=["grn_number"])
        if self.status == "accepted" and self.purchase_order.status != "received":
            self.purchase_order.status = "received"
            self.purchase_order.save(update_fields=["status", "updated_at"])
        elif self.status == "partially_accepted" and self.purchase_order.status == "issued":
            self.purchase_order.status = "partially_received"
            self.purchase_order.save(update_fields=["status", "updated_at"])
        if self.status in ("accepted", "partially_accepted"):
            requisition = self.purchase_order.requisition
            message = f"Goods received for {self.purchase_order.po_number}. Supplier invoice can now be captured and approved for payment."
            requisition.notify(requisition.approval_assigned_to, "Approver", "goods_received", message)
            requisition.notify(requisition.viewer, "Viewer", "goods_received", message)

    def __str__(self):
        return f"{self.grn_number or 'GRN'} - {self.purchase_order}"

    class Meta:
        db_table = "goods_received_notes"
        ordering = ["-received_date", "-grn_id"]


class SupplierInvoice(models.Model):
    """Supplier invoice matched to purchase order and goods received evidence."""
    supplier_invoice_id = models.AutoField(primary_key=True)
    invoice_number = models.CharField(max_length=100)
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="supplier_invoices")
    goods_received_note = models.ForeignKey(GoodsReceivedNote, on_delete=models.PROTECT, related_name="supplier_invoices")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="supplier_invoices")
    invoice_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField()
    subtotal_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    STATUS_CHOICES = [
        ("received", "Received"),
        ("matched", "Three-Way Matched"),
        ("approved", "Approved for Payment"),
        ("partially_paid", "Partially Paid"),
        ("paid", "Paid"),
        ("disputed", "Disputed"),
        ("cancelled", "Cancelled"),
    ]
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="received")
    approved_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="supplier_invoices_approved")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def balance_amount(self):
        return max(self.total_amount - self.amount_paid, Decimal("0.00"))

    def refresh_payment_totals(self):
        if not self.pk:
            self.amount_paid = Decimal("0.00")
            return
        self.amount_paid = sum((payment.amount for payment in self.payments.filter(payment_status="paid")), Decimal("0.00"))
        if self.total_amount > 0 and self.amount_paid >= self.total_amount:
            self.status = "paid"
        elif self.amount_paid > 0:
            self.status = "partially_paid"

    @property
    def approved_payable_amount(self):
        approved_amount = self.purchase_order.requisition.approved_amount if self.purchase_order_id else Decimal("0.00")
        payable_limit = approved_amount if approved_amount > 0 else self.balance_amount
        return min(self.balance_amount, payable_limit)

    def create_payment_request(self):
        payable_amount = self.approved_payable_amount
        if self.status != "approved" or payable_amount <= 0:
            return None
        payment, created = SupplierPayment.objects.get_or_create(
            supplier_invoice=self,
            payment_status="initiated",
            defaults={
                "payment_date": timezone.localdate(),
                "amount": payable_amount,
                "payment_method": "bank_transfer",
                "approval_status": "pending",
                "remarks": "Automatically initiated from the requisition approved amount.",
            },
        )
        if not created and payment.approval_status == "pending" and payment.amount != payable_amount:
            payment.amount = payable_amount
            payment.remarks = payment.remarks or "Automatically aligned to the requisition approved amount."
            payment.save(update_fields=["amount", "remarks", "updated_at"])
        if created:
            requisition = self.purchase_order.requisition
            message = f"Payment approval is required for supplier invoice {self.invoice_number} ({payable_amount})."
            requisition.notify(requisition.approval_assigned_to or self.approved_by, "Approver", "payment_initiated", message)
            requisition.notify(requisition.viewer, "Viewer", "payment_initiated", message)
        return payment
    def clean(self):
        super().clean()
        if self.purchase_order_id and self.goods_received_note_id and self.goods_received_note.purchase_order_id != self.purchase_order_id:
            raise ValidationError("Supplier invoice GRN must belong to the selected purchase order.")
        if self.purchase_order_id and self.supplier_id and self.purchase_order.supplier_id != self.supplier_id:
            raise ValidationError("Supplier invoice supplier must match the purchase order supplier.")
        if self.goods_received_note_id and self.goods_received_note.status not in ("accepted", "partially_accepted"):
            raise ValidationError("Supplier invoices require accepted goods received evidence.")
        if self.due_date and self.invoice_date and self.due_date < self.invoice_date:
            raise ValidationError("Supplier invoice due date cannot be earlier than invoice date.")
        if self.status in ("approved", "partially_paid", "paid") and not self.approved_by_id:
            raise ValidationError("Supplier invoices approved for payment require an approver.")
        if self.purchase_order_id and self.total_amount and self.total_amount > self.purchase_order.total_amount:
            raise ValidationError("Supplier invoice total cannot exceed the purchase order total.")

    def save(self, *args, **kwargs):
        self.total_amount = self.subtotal_amount + self.tax_amount
        self.refresh_payment_totals()
        self.full_clean()
        super().save(*args, **kwargs)
        if self.status in ("received", "matched", "approved") and self.purchase_order.status == "received":
            self.purchase_order.status = "invoiced"
            self.purchase_order.save(update_fields=["status", "updated_at"])
        self.create_payment_request()

    def __str__(self):
        return f"Supplier Invoice {self.invoice_number} - {self.supplier}"

    class Meta:
        db_table = "supplier_invoices"
        ordering = ["-invoice_date", "-supplier_invoice_id"]
        constraints = [
            models.UniqueConstraint(fields=["supplier", "invoice_number"], name="unique_supplier_invoice_number"),
        ]


class SupplierPayment(models.Model):
    """Payment made against an approved supplier invoice."""
    supplier_payment_id = models.AutoField(primary_key=True)
    supplier_invoice = models.ForeignKey(SupplierInvoice, on_delete=models.PROTECT, related_name="payments")
    payment_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    payment_method = models.CharField(max_length=50, choices=Expense.PAYMENT_METHOD_CHOICES)
    transaction_ref = models.CharField(max_length=100, blank=True, default="")
    APPROVAL_STATUS_CHOICES = [("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")]
    approval_status = models.CharField(max_length=20, choices=APPROVAL_STATUS_CHOICES, default="pending")
    PAYMENT_STATUS_CHOICES = [("initiated", "Initiated"), ("paid", "Paid"), ("rejected", "Rejected")]
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default="initiated")
    approved_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="supplier_payments_approved")
    paid_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="supplier_payments_made")
    paid_at = models.DateTimeField(blank=True, null=True)
    remarks = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def sync_amount_to_requisition_approval(self):
        if not self.supplier_invoice_id:
            return
        previous_status = None
        if self.pk:
            previous_status = SupplierPayment.objects.filter(pk=self.pk).values_list("payment_status", flat=True).first()
        if previous_status == "paid":
            return
        payable_amount = self.supplier_invoice.approved_payable_amount
        if payable_amount > 0:
            self.amount = payable_amount
    def clean(self):
        super().clean()
        self.sync_amount_to_requisition_approval()
        if self.supplier_invoice_id and self.supplier_invoice.status not in ("approved", "partially_paid", "paid"):
            raise ValidationError("Supplier payments require an invoice approved for payment.")
        if self.supplier_invoice_id:
            existing_amount = Decimal("0.00")
            if self.pk:
                existing_amount = SupplierPayment.objects.filter(pk=self.pk).values_list("amount", flat=True).first() or Decimal("0.00")
            available_balance = self.supplier_invoice.balance_amount + existing_amount
            if self.payment_status != "paid" and self.amount > available_balance:
                raise ValidationError("Supplier payment cannot exceed the invoice balance.")
        if self.approval_status == "approved" and not self.approved_by_id:
            raise ValidationError("Approved supplier payments require an approver.")
        if self.approval_status == "approved" and not self.paid_by_id:
            raise ValidationError("Approved supplier payments require the employee paying the supplier.")

    def save(self, *args, **kwargs):
        if self.approval_status == "approved":
            self.payment_status = "paid"
            if not self.paid_at:
                self.paid_at = timezone.now()
        elif self.approval_status == "rejected":
            self.payment_status = "rejected"
        self.full_clean()
        super().save(*args, **kwargs)
        self.supplier_invoice.save(update_fields=["amount_paid", "status", "updated_at"])
        if self.payment_status == "paid":
            requisition = self.supplier_invoice.purchase_order.requisition
            message = f"Supplier payment of {self.amount} for invoice {self.supplier_invoice.invoice_number} has been approved and paid."
            requisition.notify(requisition.viewer, "Viewer", "payment_paid", message)
            requisition.notify(requisition.requested_by, "Requester", "payment_paid", message)

    def delete(self, *args, **kwargs):
        supplier_invoice = self.supplier_invoice if self.supplier_invoice_id else None
        result = super().delete(*args, **kwargs)
        if supplier_invoice:
            supplier_invoice.save(update_fields=["amount_paid", "status", "updated_at"])
        return result

    def __str__(self):
        return f"Supplier Payment - {self.amount} on {self.payment_date}"

    class Meta:
        db_table = "supplier_payments"
        ordering = ["-payment_date", "-supplier_payment_id"]


class ProcurementNotification(models.Model):
    """Procurement workflow notification for requesters, viewers, and approvers."""
    notification_id = models.AutoField(primary_key=True)
    recipient = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="procurement_notifications")
    recipient_group = models.CharField(max_length=50)
    NOTIFICATION_TYPE_CHOICES = [
        ("requisition_submitted", "Requisition Submitted"),
        ("requisition_approved", "Requisition Approved"),
        ("requisition_rejected", "Requisition Rejected"),
        ("supplier_contacted", "Supplier Contacted"),
        ("lpo_generated", "LPO Generated"),
        ("goods_received", "Goods Received"),
        ("payment_initiated", "Payment Initiated"),
        ("payment_paid", "Payment Paid"),
    ]
    notification_type = models.CharField(max_length=40, choices=NOTIFICATION_TYPE_CHOICES)
    related_model = models.CharField(max_length=80, blank=True, default="")
    related_object_id = models.PositiveIntegerField(blank=True, null=True)
    message = models.TextField()
    STATUS_CHOICES = [("pending", "Pending"), ("sent", "Sent"), ("read", "Read"), ("failed", "Failed")]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    notified_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.get_notification_type_display()} - {self.recipient}"

    class Meta:
        db_table = "procurement_notifications"
        ordering = ["-notified_at", "-notification_id"]
        constraints = [models.UniqueConstraint(fields=["recipient", "notification_type", "related_model", "related_object_id"], name="unique_procurement_notification")]


class ExpenseNotification(models.Model):
    """Notification for overdue expense accountability."""
    notification_id = models.AutoField(primary_key=True)
    expense = models.ForeignKey(Expense, on_delete=models.CASCADE, related_name='notifications')
    recipient = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='expense_notifications')
    recipient_group = models.CharField(max_length=50)
    NOTIFICATION_TYPE_CHOICES = [
        ('missing_accountability', 'Missing Expense Accountability'),
    ]
    notification_type = models.CharField(max_length=30, choices=NOTIFICATION_TYPE_CHOICES)
    message = models.TextField()
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    delivery_note = models.CharField(max_length=255, blank=True, default='')
    notified_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.get_notification_type_display()} - {self.recipient}"

    class Meta:
        db_table = 'expense_notifications'
        constraints = [
            models.UniqueConstraint(
                fields=['expense', 'recipient', 'notification_type'],
                name='unique_expense_notification_per_recipient',
            ),
        ]


class BudgetNotification(models.Model):
    """Audit notification for budget thresholds and accountability breaches."""
    notification_id = models.AutoField(primary_key=True)
    budget = models.ForeignKey(Budget, on_delete=models.CASCADE, related_name='notifications')
    recipient = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='budget_notifications')
    recipient_group = models.CharField(max_length=50)
    NOTIFICATION_TYPE_CHOICES = [
        ('low_balance', 'Low Balance'),
        ('missing_expense', 'Missing Expense Accountability'),
    ]
    notification_type = models.CharField(max_length=30, choices=NOTIFICATION_TYPE_CHOICES)
    message = models.TextField()
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    delivery_note = models.CharField(max_length=255, blank=True, default='')
    notified_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.get_notification_type_display()} - {self.recipient}"

    class Meta:
        db_table = 'budget_notifications'
        constraints = [
            models.UniqueConstraint(
                fields=['budget', 'recipient', 'notification_type'],
                name='unique_budget_notification_per_recipient',
            ),
        ]







