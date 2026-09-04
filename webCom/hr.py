from datetime import timedelta
import mimetypes

from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from .access import require_model_access, require_view_access
from .finance import sync_salary_table
from .forms_hr import EmployeeDeploymentTransferForm, LeaveReviewForm
from .models import DisciplinaryNotification, Document, Employee, JobApplication, Leave, LeaveNotification, Training
from .views import render_page

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


def job_application_resume(request, pk):
    require_model_access(request, "job-applications")
    application = get_object_or_404(JobApplication, pk=pk)
    if not application.resume:
        raise Http404("This application has no resume attached.")
    filename = application.resume.name.rsplit("/", 1)[-1]
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(
        application.resume.open("rb"),
        as_attachment=request.GET.get("download") == "1",
        filename=filename,
        content_type=content_type,
    )

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


def leave_notifications(request):
    require_view_access(request, "leave_notifications")
    notifications = LeaveNotification.objects.select_related(
        "leave",
        "leave__employee",
        "recipient",
    ).order_by("-notified_at", "-notification_id")
    context = {
        "title": "Leave Notifications",
        "notifications": notifications,
    }
    return render_page(request, "webCom/leave_notifications.html", context, "leaves")


def leave_notification_action(request, notification_id, action):
    require_view_access(request, "leave_notification_action")
    notification = get_object_or_404(
        LeaveNotification.objects.select_related("leave", "leave__employee", "recipient"),
        pk=notification_id,
    )
    leave = notification.leave
    actor = notification.recipient

    if request.method != "POST":
        return redirect("webcom:leave_notifications")

    now = timezone.now()
    if action == "verify":
        if not notification.can_verify:
            messages.error(request, "This leave request cannot be verified from this notification.")
            return redirect("webcom:leave_notifications")
        leave.operations_verification_status = "verified"
        leave.verified_by = actor
        leave.operations_feedback = leave.operations_feedback or "Verified from leave notification."
        leave.operations_verified_at = now
        leave.save(update_fields=["operations_verification_status", "verified_by", "operations_feedback", "operations_verified_at", "updated_at"])
        leave.notify(leave.employee, "Requester", "leave_verified", f"Your leave request from {leave.start_date} to {leave.end_date} has been verified.")
        messages.success(request, "Leave request verified.")
    elif action == "approve":
        if not notification.can_approve:
            messages.error(request, "This leave request must be verified before approval.")
            return redirect("webcom:leave_notifications")
        leave.approval_status = "approved"
        leave.approved_by = actor
        leave.feedback = leave.feedback or "Approved from leave notification."
        leave.hr_decided_at = now
        leave.save(update_fields=["approval_status", "approved_by", "feedback", "hr_decided_at", "updated_at"])
        leave.notify(leave.employee, "Requester", "leave_approved", f"Your leave request from {leave.start_date} to {leave.end_date} has been approved.")
        deliver_leave_feedback(leave)
        messages.success(request, "Leave request approved.")
    elif action == "reject":
        if not notification.can_reject:
            messages.error(request, "This leave request cannot be rejected from this notification.")
            return redirect("webcom:leave_notifications")
        if notification.recipient_group == "Verifier" and leave.operations_verification_status == "pending":
            leave.operations_verification_status = "rejected"
            leave.verified_by = actor
            leave.operations_feedback = leave.operations_feedback or "Rejected from leave notification."
            leave.operations_verified_at = now
        leave.approval_status = "rejected"
        leave.approved_by = actor
        leave.feedback = leave.feedback or "Rejected from leave notification."
        leave.hr_decided_at = now
        leave.save(update_fields=["operations_verification_status", "verified_by", "operations_feedback", "operations_verified_at", "approval_status", "approved_by", "feedback", "hr_decided_at", "updated_at"])
        leave.notify(leave.employee, "Requester", "leave_rejected", f"Your leave request from {leave.start_date} to {leave.end_date} has been rejected.")
        deliver_leave_feedback(leave)
        messages.success(request, "Leave request rejected.")
    else:
        messages.error(request, "Unknown leave decision.")
        return redirect("webcom:leave_notifications")

    notification.status = "actioned"
    notification.decision = action
    notification.decided_at = now
    notification.save(update_fields=["status", "decision", "decided_at", "updated_at"])
    return redirect("webcom:leave_notifications")


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
