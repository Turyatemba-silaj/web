from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.utils import timezone
from django.core.validators import MaxValueValidator, MinValueValidator

class Role(models.Model):
    """Manages employee roles"""
    role_id = models.AutoField(primary_key=True)
    role_name = models.CharField(max_length=100)
    
    DEPARTMENT_CHOICES = [
        ('operations', 'Operations'),
        ('hr', 'Human Resources'),
        ('finance', 'Finance'),
        ('admin', 'Administration'),
    ]
    department = models.CharField(max_length=50, choices=DEPARTMENT_CHOICES)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.role_name

    class Meta:
        db_table = 'roles'


class Position(models.Model):
    """Manages employee positions"""
    position_id = models.AutoField(primary_key=True)
    position_title = models.CharField(max_length=100)
    
    DEPARTMENT_CHOICES = [
        ('operations', 'Operations'),
        ('hr', 'Human Resources'),
        ('finance', 'Finance'),
        ('admin', 'Administration'),
    ]
    department = models.CharField(max_length=50, choices=DEPARTMENT_CHOICES)
    grade_level = models.CharField(max_length=50)
    salary_range_min = models.DecimalField(max_digits=10, decimal_places=2)
    salary_range_max = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.position_title

    class Meta:
        db_table = 'positions'


class Employee(models.Model):
    """Manages all employees"""
    employee_id = models.AutoField(primary_key=True)
    employee_number = models.CharField("Employee Number", max_length=20, unique=True, blank=True, editable=False)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    
    GENDER_CHOICES = [
        ('M', 'Male'),
        ('F', 'Female'),
        ('O', 'Other'),
    ]
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    phone_number = models.CharField(max_length=20)
    email = models.EmailField()
    address = models.TextField()
    national_id = models.CharField(max_length=50, unique=True)
    nssf_number = models.CharField("NSSF Number", max_length=50, unique=True, blank=True, null=True)
    ROLE_CHOICES = [
        ('guard', 'Guard'),
        ('supervisor', 'Supervisor'),
        ('manager', 'Manager'),
        ('operations_officer', 'Operations Officer'),
        ('hr_officer', 'HR Officer'),
        ('finance_officer', 'Finance Officer'),
        ('administrator', 'Administrator'),
    ]
    POSITION_CHOICES = [
        ('security_guard', 'Security Guard'),
        ('site_supervisor', 'Site Supervisor'),
        ('regional_supervisor', 'Regional Supervisor'),
        ('operations_officer', 'Operations Officer'),
        ('hr_officer', 'HR Officer'),
        ('finance_officer', 'Finance Officer'),
        ('administrator', 'Administrator'),
    ]
    DEPARTMENT_CHOICES = [
        ('operations', 'Operations'),
        ('hr', 'Human Resources'),
        ('finance', 'Finance'),
        ('admin', 'Administration'),
    ]
    QUALIFICATION_CHOICES = [
        ('primary', 'Primary'),
        ('o_level', 'O Level'),
        ('a_level', 'A Level'),
        ('certificate', 'Certificate'),
        ('diploma', 'Diploma'),
        ('degree', 'Degree'),
        ('masters', 'Masters'),
        ('other', 'Other'),
    ]
    ARMED_STATUS_CHOICES = [
        ('armed', 'Armed'),
        ('unarmed', 'Unarmed'),
    ]
    AUTHORITY_LEVEL_CHOICES = [
        ('site', 'Site Level'),
        ('regional', 'Regional Level'),
        ('national', 'National Level'),
    ]
    role = models.CharField(max_length=50, choices=ROLE_CHOICES, default='guard')
    position = models.CharField(max_length=50, choices=POSITION_CHOICES, default='security_guard')
    department = models.CharField(max_length=50, choices=DEPARTMENT_CHOICES, default='operations')
    salary_scale = models.DecimalField("Daily Rate", max_digits=10, decimal_places=2, default=0, editable=False)
    qualification = models.CharField('Academic Qualification', max_length=50, choices=QUALIFICATION_CHOICES, blank=True, default='')
    armed_status = models.CharField(max_length=20, choices=ARMED_STATUS_CHOICES, blank=True, default='')
    authority_level = models.CharField(max_length=50, choices=AUTHORITY_LEVEL_CHOICES, blank=True, default='')
    hire_date = models.DateField()
    
    EMPLOYEE_STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('on_leave', 'On Leave'),
        ('terminated', 'Terminated'),
    ]
    status = models.CharField(max_length=20, choices=EMPLOYEE_STATUS_CHOICES, default='active')
    is_reliever = models.BooleanField('Reliever', default=False, help_text="Relievers may be assigned to more than one site.")
    PAYOUT_METHOD_CHOICES = [
        ('bank_transfer', 'Bank Transfer'),
        ('mobile_money', 'Mobile Money'),
    ]
    payout_method = models.CharField(max_length=30, choices=PAYOUT_METHOD_CHOICES, default='mobile_money')
    bank_name = models.CharField(max_length=120, blank=True, default='')
    bank_account_name = models.CharField(max_length=120, blank=True, default='')
    bank_account_number = models.CharField(max_length=80, blank=True, default='')
    mobile_money_provider = models.CharField(max_length=80, blank=True, default='')
    mobile_money_number = models.CharField(max_length=30, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def set_employee_number(self):
        prefix = self.employee_number_prefix
        if self.employee_number and self.employee_number.startswith(prefix):
            return
        existing_numbers = Employee.objects.filter(
            employee_number__startswith=prefix
        )
        if self.pk:
            existing_numbers = existing_numbers.exclude(pk=self.pk)
        existing_numbers = existing_numbers.values_list("employee_number", flat=True)
        highest_number = 0
        for employee_number in existing_numbers:
            suffix = employee_number.removeprefix(prefix)
            if suffix.isdigit():
                highest_number = max(highest_number, int(suffix))
        self.employee_number = f"{prefix}{highest_number + 1:03d}"

    @property
    def employee_number_prefix(self):
        if self.role == 'guard':
            return self.seniority_code
        if self.role == 'supervisor':
            return "SUP "
        return "ADM "

    def months_worked(self):
        if not self.hire_date:
            return 0
        today = timezone.localdate()
        months = (today.year - self.hire_date.year) * 12 + today.month - self.hire_date.month
        if today.day < self.hire_date.day:
            months -= 1
        return max(months, 0)

    @property
    def seniority_code(self):
        months = self.months_worked()
        if months < 6:
            return "TE"
        if months < 60:
            return "PE"
        if months < 108:
            return "P"
        return "SENIOR"

    @property
    def seniority_level(self):
        labels = {
            "TE": "TE (Temporary Employee)",
            "PE": "PE (Permanent Employee)",
            "P": "P (Pioneer)",
            "SENIOR": "Senior Employee",
        }
        return labels[self.seniority_code]

    @property
    def daily_rate(self):
        return self.calculate_daily_rate()

    @property
    def current_deployment_area(self):
        today = timezone.localdate()
        area = self.deployment_areas.filter(
            status='active',
            start_date__lte=today,
        ).filter(
            models.Q(end_date__isnull=True) | models.Q(end_date__gte=today)
        ).select_related('region').order_by('-start_date', '-deployment_area_id').first()
        return area.region if area else "-"

    @property
    def can_transfer(self):
        return self.role in ('guard', 'supervisor')

    def calculate_daily_rate(self):
        if self.role == "guard":
            guard_rates = {
                "TE": Decimal("9000.00"),
                "PE": Decimal("10500.00"),
                "P": Decimal("12000.00"),
                "SENIOR": Decimal("20000.00"),
            }
            return guard_rates[self.seniority_code]
        fixed_staff_rates = {
            "supervisor": Decimal("30000.00"),
            "manager": Decimal("30000.00"),
            "operations_officer": Decimal("30000.00"),
            "hr_officer": Decimal("30000.00"),
            "finance_officer": Decimal("30000.00"),
            "administrator": Decimal("30000.00"),
        }
        return fixed_staff_rates.get(self.role, Decimal("0.00"))

    @property
    def uses_fixed_monthly_salary(self):
        return self.role in {
            "supervisor",
            "manager",
            "operations_officer",
            "hr_officer",
            "finance_officer",
            "administrator",
        }

    @property
    def fixed_monthly_salary(self):
        if not self.uses_fixed_monthly_salary:
            return Decimal("0.00")
        return (self.daily_rate * Decimal("26.00")).quantize(Decimal("0.01"))
    def set_salary_profile(self):
        if self.role == 'guard':
            self.department = 'operations'
            if not self.position:
                self.position = 'security_guard'
        elif self.role == 'supervisor':
            self.department = 'operations'
            if not self.position or self.position == 'security_guard':
                self.position = 'site_supervisor'
        elif self.role == 'hr_officer':
            self.department = 'hr'
        elif self.role == 'finance_officer':
            self.department = 'finance'
        elif self.role in ('manager', 'operations_officer'):
            self.department = 'operations'
        elif self.role == 'administrator':
            self.department = 'admin'

        self.salary_scale = self.calculate_daily_rate()

    def save(self, *args, **kwargs):
        self.set_employee_number()
        self.set_salary_profile()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"employee_number", "salary_scale"}
        super().save(*args, **kwargs)
        salary, _created = Salary.objects.get_or_create(
            employee=self,
            defaults={"basic_salary": Decimal("0.00")},
        )
        salary.update_basic_salary()
        salary.save(update_fields=["basic_salary", "updated_at"])

    @property
    def employee_number_name(self):
        if self.employee_number:
            return f"{self.employee_number} - {self.first_name} {self.last_name}"
        return f"{self.first_name} {self.last_name}"

    def __str__(self):
        return self.employee_number_name

    class Meta:
        db_table = 'employees'




class Guard(models.Model):
    """Manages guard-specific information"""
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, primary_key=True, related_name='guard_info')
    
    QUALIFICATION_CHOICES = [
        ('basic', 'Basic Security'),
        ('advanced', 'Advanced Security'),
        ('specialized', 'Specialized Training'),
    ]
    qualification = models.CharField(max_length=50, choices=QUALIFICATION_CHOICES)
    
    ARMED_STATUS_CHOICES = [
        ('armed', 'Armed'),
        ('unarmed', 'Unarmed'),
    ]
    armed_status = models.CharField(max_length=20, choices=ARMED_STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Guard - {self.employee.first_name} {self.employee.last_name}"

    class Meta:
        db_table = 'guards'


class Supervisor(models.Model):
    """Manages supervisor-specific information"""
    supervisor_id = models.AutoField(primary_key=True)
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name='supervisor_info')
    
    AUTHORITY_LEVEL_CHOICES = [
        ('site', 'Site Level'),
        ('regional', 'Regional Level'),
        ('national', 'National Level'),
    ]
    authority_level = models.CharField(max_length=50, choices=AUTHORITY_LEVEL_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Supervisor - {self.employee.first_name} {self.employee.last_name}"

    class Meta:
        db_table = 'supervisors'


class Training(models.Model):
    """Manages employee training records"""
    training_id = models.AutoField(primary_key=True)
    TRAINING_TYPE_CHOICES = [
        ('basic', 'Basic'),
        ('refresher', 'Refresher'),
        ('promotional_course', 'Promotional Course'),
        ('management_training', 'Management Training'),
    ]
    TRAINING_NAME_CHOICES = [
        ('guarding_training', 'Guarding Training'),
        ('supervision_course', 'Supervision Course'),
        ('managers_course', 'Managers Course'),
        ('iso_training', 'ISO Training'),
    ]
    training_type = models.CharField(max_length=50, choices=TRAINING_TYPE_CHOICES, default='refresher')
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, blank=True, null=True, related_name='trainings')
    recruit = models.CharField(max_length=255, blank=True)
    training_name = models.CharField(max_length=50, choices=TRAINING_NAME_CHOICES, default='guarding_training')
    provider = models.CharField(max_length=255, default="TURYANS SECURITY COMPANY (U) LIMITED")
    start_date = models.DateField()
    end_date = models.DateField("Date of Completion")
    certificate_no = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def trainee(self):
        if self.training_type == 'basic':
            return self.recruit or '-'
        return self.employee or '-'

    @property
    def is_completed(self):
        return self.end_date <= timezone.localdate()

    def ensure_certificate_number(self):
        if not self.certificate_no:
            self.certificate_no = f"TRN{self.training_id:05d}"
            self.save(update_fields=["certificate_no", "updated_at"])
        return self.certificate_no

    def __str__(self):
        return f"{self.get_training_name_display()} - {self.trainee}"

    class Meta:
        db_table = 'training'



class Leave(models.Model):
    """Manages employee leave requests"""
    leave_id = models.AutoField(primary_key=True)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='leaves')
    
    LEAVE_TYPE_CHOICES = [
        ('annual', 'Annual Leave'),
        ('medical', 'Medical Leave'),
        ('unpaid', 'Unpaid Leave'),
        ('maternity', 'Maternity Leave'),
        ('paternity', 'Paternity Leave'),
    ]
    leave_type = models.CharField(max_length=50, choices=LEAVE_TYPE_CHOICES)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    address_while_away = models.CharField(max_length=255, blank=True, default='')
    emergency_contact = models.CharField(max_length=120, blank=True, default='')
    hr_verifier = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='hr_verified_leave_applications')
    supervisor = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='supervised_leave_applications')
    hod = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='hod_leave_applications')
    stand_in_coworker = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='stand_in_leave_applications')
    APPLICATION_STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
    ]
    application_status = models.CharField(max_length=20, choices=APPLICATION_STATUS_CHOICES, default='submitted')
    
    APPROVAL_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    OPERATIONS_VERIFICATION_CHOICES = [
        ('pending', 'Pending'),
        ('verified', 'Verified'),
        ('rejected', 'Rejected'),
    ]
    operations_verification_status = models.CharField(max_length=20, choices=OPERATIONS_VERIFICATION_CHOICES, default='pending')
    verified_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='operations_verified_leaves')
    operations_feedback = models.TextField(blank=True, default='')
    operations_verified_at = models.DateTimeField(blank=True, null=True)
    approval_status = models.CharField(max_length=20, choices=APPROVAL_STATUS_CHOICES, default='pending')
    approved_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_leaves')
    feedback = models.TextField(blank=True, default='')
    hr_decided_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def process_review(
        self,
        *,
        operations_manager,
        operations_status,
        operations_feedback='',
        hr_manager=None,
        hr_decision='',
        feedback='',
    ):
        if operations_status not in dict(self.OPERATIONS_VERIFICATION_CHOICES):
            raise ValidationError("Choose a valid operations verification status.")
        if operations_status == 'pending':
            raise ValidationError("Operations manager must verify or reject the leave request.")
        if operations_status == 'verified' and not hr_decision:
            raise ValidationError("Human Resource must approve or reject a verified leave request.")
        if hr_decision and hr_decision not in dict(self.APPROVAL_STATUS_CHOICES):
            raise ValidationError("Choose a valid human resource decision.")
        if hr_decision == 'pending':
            raise ValidationError("Human Resource must approve or reject the leave request.")

        now = timezone.now()
        self.operations_verification_status = operations_status
        self.verified_by = operations_manager
        self.operations_feedback = operations_feedback or ''
        self.operations_verified_at = now

        if operations_status == 'rejected':
            self.approval_status = 'rejected'
            self.feedback = feedback or self.operations_feedback
            self.hr_decided_at = now
        elif hr_decision:
            self.approval_status = hr_decision
            self.approved_by = hr_manager
            self.feedback = feedback or ''
            self.hr_decided_at = now

        self.save()
        return self

    def notify(self, recipient, recipient_group, notification_type, message):
        notification_model = globals().get("LeaveNotification")
        if not notification_model or not recipient or not self.pk:
            return None
        notification, _created = notification_model.objects.get_or_create(
            leave=self,
            recipient=recipient,
            recipient_group=recipient_group,
            notification_type=notification_type,
            defaults={
                "message": message,
                "status": "pending",
                "notified_at": timezone.now(),
            },
        )
        return notification

    def notify_submission(self):
        if self.application_status != "submitted" or not self.pk:
            return
        message = f"{self.employee} submitted {self.get_leave_type_display()} from {self.start_date} to {self.end_date}."
        self.notify(self.supervisor, "Verifier", "verification_requested", message)
        self.notify(self.hod, "Approver", "approval_requested", message)
        self.notify(self.hr_verifier, "Approver", "approval_requested", message)
        self.notify(self.stand_in_coworker, "Stand-In", "coverage_notice", f"{self.employee} listed you as stand-in coworker for leave from {self.start_date} to {self.end_date}.")

    def __str__(self):
        return f"{self.employee} - {self.leave_type}"

    class Meta:
        db_table = 'leaves'


class LeaveNotification(models.Model):
    """Actionable leave workflow notification for verifiers and approvers."""
    notification_id = models.AutoField(primary_key=True)
    leave = models.ForeignKey(Leave, on_delete=models.CASCADE, related_name='notifications')
    recipient = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='leave_notifications')
    recipient_group = models.CharField(max_length=50)
    NOTIFICATION_TYPE_CHOICES = [
        ('verification_requested', 'Verification Requested'),
        ('approval_requested', 'Approval Requested'),
        ('coverage_notice', 'Coverage Notice'),
        ('leave_verified', 'Leave Verified'),
        ('leave_approved', 'Leave Approved'),
        ('leave_rejected', 'Leave Rejected'),
    ]
    notification_type = models.CharField(max_length=40, choices=NOTIFICATION_TYPE_CHOICES)
    message = models.TextField()
    STATUS_CHOICES = [('pending', 'Pending'), ('read', 'Read'), ('actioned', 'Actioned')]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    decision = models.CharField(max_length=20, blank=True, default='')
    decided_at = models.DateTimeField(blank=True, null=True)
    notified_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def can_verify(self):
        return self.recipient_group == 'Verifier' and self.leave.operations_verification_status == 'pending'

    @property
    def can_approve(self):
        return self.recipient_group == 'Approver' and self.leave.operations_verification_status == 'verified' and self.leave.approval_status == 'pending'

    @property
    def can_reject(self):
        return self.recipient_group in ('Verifier', 'Approver') and self.leave.approval_status == 'pending'

    def __str__(self):
        return f"{self.get_notification_type_display()} - {self.recipient}"

    class Meta:
        db_table = 'leave_notifications'
        ordering = ['-notified_at', '-notification_id']
        constraints = [
            models.UniqueConstraint(fields=['leave', 'recipient', 'recipient_group', 'notification_type'], name='unique_leave_notification')
        ]


class Disciplinary_Action(models.Model):
    """Tracks a standard disciplinary case from offence to conclusion."""
    action_id = models.AutoField(primary_key=True)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='disciplinary_actions')
    offence_committed = models.CharField(max_length=255, default='Unspecified offence')
    offence_date = models.DateField(default=timezone.now)
    reported_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='disciplinary_reports_made')
    action_date = models.DateField(default=timezone.now)
    description = models.TextField(blank=True, default='')
    investigation_notes = models.TextField(blank=True, default='')
    hearing_date = models.DateField(blank=True, null=True)
    hearing_notes = models.TextField(blank=True, default='')
    steps_taken = models.TextField(blank=True, default='')
    conclusion = models.TextField(blank=True, default='')
    concluded_on = models.DateField(blank=True, null=True)
    handled_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='disciplinary_cases_handled')
    OUTCOME_CHOICES = [
        ('pending', 'Pending'),
        ('verbal_warning', 'Verbal Warning'),
        ('written_warning', 'Written Warning'),
        ('suspension', 'Suspension'),
        ('termination', 'Termination'),
        ('cleared', 'Cleared'),
        ('other', 'Other'),
    ]
    outcome = models.CharField(max_length=30, choices=OUTCOME_CHOICES, default='pending')
    STATUS_CHOICES = [
        ('reported', 'Reported'),
        ('investigating', 'Under Investigation'),
        ('hearing', 'Hearing Scheduled'),
        ('action_taken', 'Action Taken'),
        ('concluded', 'Concluded'),
    ]
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='reported', editable=False)
    reason = models.TextField(blank=True, default='')
    APPROVAL_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    approval_status = models.CharField(max_length=20, choices=APPROVAL_STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def update_status(self):
        if self.conclusion or self.concluded_on:
            self.status = 'concluded'
        elif self.steps_taken or self.outcome != 'pending':
            self.status = 'action_taken'
        elif self.hearing_date or self.hearing_notes:
            self.status = 'hearing'
        elif self.investigation_notes:
            self.status = 'investigating'
        else:
            self.status = 'reported'

    def build_reason_summary(self):
        parts = [f"Offence committed: {self.offence_committed}"]
        if self.description:
            parts.append(f"Description: {self.description}")
        if self.investigation_notes:
            parts.append(f"Investigation: {self.investigation_notes}")
        if self.hearing_notes:
            parts.append(f"Hearing: {self.hearing_notes}")
        if self.steps_taken:
            parts.append(f"Steps taken: {self.steps_taken}")
        if self.conclusion:
            parts.append(f"Conclusion: {self.conclusion}")
        if self.outcome != 'pending':
            parts.append(f"Outcome: {self.get_outcome_display()}")
        return "\n".join(parts)

    def clean(self):
        super().clean()
        if self.offence_date and self.action_date and self.action_date < self.offence_date:
            raise ValidationError("Action date cannot be earlier than the offence date.")
        if self.hearing_date and self.offence_date and self.hearing_date < self.offence_date:
            raise ValidationError("Hearing date cannot be earlier than the offence date.")
        if self.concluded_on and self.offence_date and self.concluded_on < self.offence_date:
            raise ValidationError("Conclusion date cannot be earlier than the offence date.")
        if self.concluded_on and not self.conclusion:
            raise ValidationError("Enter the conclusion before closing the disciplinary action.")

    def save(self, *args, **kwargs):
        self.update_status()
        if not self.reason:
            self.reason = self.build_reason_summary()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"status", "reason"}
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee} - {self.offence_committed}"

    class Meta:
        db_table = 'disciplinary_actions'


class DisciplinaryNotification(models.Model):
    """Stores automated feedback messages sent to employees after disciplinary action."""
    notification_id = models.AutoField(primary_key=True)
    disciplinary_action = models.ForeignKey(Disciplinary_Action, on_delete=models.CASCADE, related_name='notifications')
    recipient = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='disciplinary_notifications')
    message = models.TextField()
    notified_at = models.DateTimeField(default=timezone.now)
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    delivery_note = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Disciplinary feedback for {self.recipient}"

    class Meta:
        db_table = 'disciplinary_notifications'
        constraints = [
            models.UniqueConstraint(
                fields=['disciplinary_action', 'recipient'],
                name='unique_disciplinary_feedback_notification',
            ),
        ]


class Performance_Evaluation(models.Model):
    """Standard employee appraisal with scored real-world performance factors."""
    eval_id = models.AutoField(primary_key=True)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='evaluations')
    date = models.DateField(default=timezone.localdate)
    review_period_start = models.DateField(default=timezone.localdate)
    review_period_end = models.DateField(default=timezone.localdate)

    SCORE_VALIDATORS = [MinValueValidator(1), MaxValueValidator(5)]
    SCORE_CHOICES = [
        (1, '1 - Poor'),
        (2, '2 - Below Expectations'),
        (3, '3 - Meets Expectations'),
        (4, '4 - Exceeds Expectations'),
        (5, '5 - Outstanding'),
    ]
    job_knowledge = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, default=3, validators=SCORE_VALIDATORS)
    quality_of_work = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, default=3, validators=SCORE_VALIDATORS)
    productivity = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, default=3, validators=SCORE_VALIDATORS)
    reliability_attendance = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, default=3, validators=SCORE_VALIDATORS)
    communication = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, default=3, validators=SCORE_VALIDATORS)
    teamwork = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, default=3, validators=SCORE_VALIDATORS)
    discipline_compliance = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, default=3, validators=SCORE_VALIDATORS)
    customer_service = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, default=3, validators=SCORE_VALIDATORS)
    initiative_problem_solving = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, default=3, validators=SCORE_VALIDATORS)
    safety_security_awareness = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, default=3, validators=SCORE_VALIDATORS)
    overall_score = models.DecimalField(max_digits=4, decimal_places=2, default=0, editable=False)

    RATING_CHOICES = [
        ('1', 'Poor'),
        ('2', 'Below Average'),
        ('3', 'Average'),
        ('4', 'Good'),
        ('5', 'Excellent'),
    ]
    rating = models.CharField(max_length=1, choices=RATING_CHOICES, default='3', editable=False)
    strengths = models.TextField(blank=True, default='')
    areas_for_improvement = models.TextField(blank=True, default='')
    goals = models.TextField(blank=True, default='')
    training_recommendations = models.TextField(blank=True, default='')
    supervisor_comments = models.TextField(blank=True, default='')
    employee_comments = models.TextField(blank=True, default='')
    comments = models.TextField(blank=True, default='')
    evaluated_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='evaluations_given')
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('reviewed', 'Reviewed'),
        ('acknowledged', 'Acknowledged'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='reviewed')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    PERFORMANCE_SCORE_FIELDS = (
        'job_knowledge',
        'quality_of_work',
        'productivity',
        'reliability_attendance',
        'communication',
        'teamwork',
        'discipline_compliance',
        'customer_service',
        'initiative_problem_solving',
        'safety_security_awareness',
    )

    def calculate_overall_score(self):
        scores = [Decimal(str(getattr(self, field) or 0)) for field in self.PERFORMANCE_SCORE_FIELDS]
        if not scores:
            return Decimal('0.00')
        return (sum(scores) / Decimal(len(scores))).quantize(Decimal('0.01'))

    def rating_from_score(self):
        score = self.overall_score
        if score < Decimal('1.50'):
            return '1'
        if score < Decimal('2.50'):
            return '2'
        if score < Decimal('3.50'):
            return '3'
        if score < Decimal('4.50'):
            return '4'
        return '5'

    def build_comments_summary(self):
        parts = [f"Overall score: {self.overall_score} ({self.get_rating_display()})"]
        if self.strengths:
            parts.append(f"Strengths: {self.strengths}")
        if self.areas_for_improvement:
            parts.append(f"Areas for improvement: {self.areas_for_improvement}")
        if self.goals:
            parts.append(f"Goals: {self.goals}")
        if self.training_recommendations:
            parts.append(f"Training recommendations: {self.training_recommendations}")
        if self.supervisor_comments:
            parts.append(f"Supervisor comments: {self.supervisor_comments}")
        if self.employee_comments:
            parts.append(f"Employee comments: {self.employee_comments}")
        return "\n".join(parts)

    def clean(self):
        super().clean()
        if self.review_period_start and self.review_period_end and self.review_period_end < self.review_period_start:
            raise ValidationError("Review period end cannot be earlier than review period start.")

    def save(self, *args, **kwargs):
        self.overall_score = self.calculate_overall_score()
        self.rating = self.rating_from_score()
        self.comments = self.build_comments_summary()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"overall_score", "rating", "comments"}
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Evaluation - {self.employee} ({self.date})"

    class Meta:
        db_table = 'performance_evaluations'


class Document(models.Model):
    """Manages employee documents"""
    doc_id = models.AutoField(primary_key=True)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='documents')
    
    DOC_TYPE_CHOICES = [
        ('license', 'License'),
        ('certificate', 'Certificate'),
        ('passport', 'Passport'),
        ('contract', 'Contract'),
        ('other', 'Other'),
    ]
    doc_type = models.CharField(max_length=50, choices=DOC_TYPE_CHOICES)
    file_path = models.FileField(upload_to='employee_documents/')
    expiry_date = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.doc_type} - {self.employee}"

    class Meta:
        db_table = 'documents'



