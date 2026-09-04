from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.utils import timezone
from django.core.validators import MaxValueValidator, MinValueValidator

# ==================== GOVERNANCE AND AUDIT ====================

class AuditLog(models.Model):
    """Immutable request audit trail for security-sensitive staff activity."""
    audit_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="webcom_audit_logs")
    username = models.CharField(max_length=150, blank=True, default="")
    action = models.CharField(max_length=20)
    path = models.CharField(max_length=500)
    method = models.CharField(max_length=10)
    status_code = models.PositiveIntegerField(default=0)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Audit log records are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit log records cannot be deleted.")

    def __str__(self):
        actor = self.username or "anonymous"
        return f"{self.action} {self.path} by {actor}"

    class Meta:
        db_table = "audit_logs"
        ordering = ["-created_at", "-audit_id"]

class UserPasswordProfile(models.Model):
    """Tracks password age and forced reset state without storing plain-text passwords."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="password_profile")
    password_changed_at = models.DateTimeField(default=timezone.now)
    force_password_change = models.BooleanField(default=False)
    last_admin_reset_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    EXPIRY_DAYS = 90

    @property
    def expires_at(self):
        return self.password_changed_at + timedelta(days=self.EXPIRY_DAYS)

    @property
    def days_until_expiry(self):
        remaining = self.expires_at - timezone.now()
        return max(remaining.days, 0)

    @property
    def is_expired(self):
        return self.force_password_change or timezone.now() >= self.expires_at

    def mark_changed(self, *, admin_reset=False, force_change=False):
        now = timezone.now()
        self.password_changed_at = now
        self.force_password_change = force_change
        if admin_reset:
            self.last_admin_reset_at = now
        self.save(update_fields=["password_changed_at", "force_password_change", "last_admin_reset_at", "updated_at"])

    def __str__(self):
        return f"Password policy - {self.user}"

    class Meta:
        db_table = "user_password_profiles"

# ==================== PUBLIC WEBSITE ====================

class WebsiteAdvertisement(models.Model):
    advert_id = models.AutoField(primary_key=True)
    title = models.CharField(max_length=160)
    placement = models.CharField(max_length=80, default="Homepage")
    message = models.TextField()
    call_to_action = models.CharField(max_length=80, blank=True, default="")
    target_url = models.CharField(max_length=255, blank=True, default="")
    starts_on = models.DateField(default=timezone.localdate)
    ends_on = models.DateField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_current(self):
        today = timezone.localdate()
        return self.is_active and self.starts_on <= today and (self.ends_on is None or self.ends_on >= today)

    def __str__(self):
        return self.title

    class Meta:
        db_table = 'website_advertisements'
        ordering = ['-starts_on', 'title']


class CompanyEvent(models.Model):
    event_id = models.AutoField(primary_key=True)
    title = models.CharField(max_length=180)
    event_date = models.DateField()
    location = models.CharField(max_length=160, blank=True, default="")
    summary = models.TextField()
    is_public = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

    class Meta:
        db_table = 'company_events'
        ordering = ['-event_date', 'title']


class WebsiteResource(models.Model):
    resource_id = models.AutoField(primary_key=True)
    title = models.CharField(max_length=180)
    resource_type = models.CharField(max_length=80, default="Guide")
    summary = models.TextField()
    url = models.CharField(max_length=255, blank=True, default="")
    is_public = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

    class Meta:
        db_table = 'website_resources'
        ordering = ['resource_type', 'title']


class AssociatedLink(models.Model):
    link_id = models.AutoField(primary_key=True)
    title = models.CharField(max_length=180)
    category = models.CharField(max_length=80, default="Partner")
    url = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    is_public = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

    class Meta:
        db_table = 'associated_links'
        ordering = ['category', 'title']


class JobPosting(models.Model):
    job_id = models.AutoField(primary_key=True)
    title = models.CharField(max_length=180)
    department = models.CharField(max_length=80, default="Operations")
    location = models.CharField(max_length=160, default="Kampala")
    employment_type = models.CharField(max_length=80, default="Full-time")
    summary = models.TextField()
    requirements = models.TextField(blank=True, default="")
    deadline = models.DateField(blank=True, null=True)
    is_online = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    posted_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_open(self):
        return self.is_active and (self.deadline is None or self.deadline >= timezone.localdate())

    def __str__(self):
        return self.title

    class Meta:
        db_table = 'job_postings'
        ordering = ['-posted_at', 'title']


class JobApplication(models.Model):
    application_id = models.AutoField(primary_key=True)
    job = models.ForeignKey(JobPosting, on_delete=models.CASCADE, related_name='applications')
    APPLICATION_MODE_CHOICES = [
        ('online', 'Online'),
        ('physical', 'Physical'),
    ]
    application_mode = models.CharField(max_length=20, choices=APPLICATION_MODE_CHOICES, default='online')
    applicant_name = models.CharField(max_length=180)
    phone_number = models.CharField(max_length=30)
    email = models.EmailField(blank=True, default="")
    address = models.CharField(max_length=255, blank=True, default="")
    qualification = models.CharField(max_length=180, blank=True, default="")
    experience_summary = models.TextField(blank=True, default="")
    cover_note = models.TextField(blank=True, default="")
    resume = models.FileField(upload_to='job_applications/', blank=True, null=True)
    received_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='received_job_applications')
    STATUS_CHOICES = [
        ('received', 'Received'),
        ('shortlisted', 'Shortlisted'),
        ('interview', 'Interview'),
        ('hired', 'Hired'),
        ('declined', 'Declined'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='received')
    submitted_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.applicant_name} - {self.job}"

    class Meta:
        db_table = 'job_applications'
        ordering = ['-submitted_at']


class JobApplicationNotification(models.Model):
    notification_id = models.AutoField(primary_key=True)
    application = models.ForeignKey(JobApplication, on_delete=models.CASCADE, related_name='notifications')
    recipient = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='job_application_notifications')
    recipient_group = models.CharField(max_length=50, default='Human Resource')
    notification_type = models.CharField(max_length=40, default='new_application')
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
        return f"Application notification - {self.application}"

    class Meta:
        db_table = 'job_application_notifications'
        ordering = ['-notified_at', '-notification_id']
        constraints = [
            models.UniqueConstraint(
                fields=['application', 'recipient', 'notification_type'],
                name='unique_job_application_notification',
            ),
        ]















