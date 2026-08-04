from django.conf import settings
from django.db import DatabaseError, OperationalError, ProgrammingError
from django.http import HttpResponse

from .models import AuditLog

class VercelConfigurationMiddleware:
    """Shows deployment configuration errors before views touch the DB."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        errors = getattr(settings, "VERCEL_CONFIGURATION_ERRORS", [])
        if errors:
            body = "Deployment configuration required:\n\n" + "\n".join(f"- {error}" for error in errors)
            return HttpResponse(body, status=503, content_type="text/plain; charset=utf-8")
        try:
            return self.get_response(request)
        except DatabaseError:
            if getattr(settings, "IS_VERCEL_RUNTIME", False):
                body = (
                    "Database is not ready for this deployment.\n\n"
                    "- Confirm DATABASE_URL points to a hosted PostgreSQL database.\n"
                    "- Confirm Vercel build logs show migrations completed successfully.\n"
                    "- Visit /system/health/ after redeploying to verify database access.\n"
                )
                return HttpResponse(body, status=503, content_type="text/plain; charset=utf-8")
            raise

class RequestAuditMiddleware:
    """Records authenticated staff write activity for compliance traceability."""

    WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
    IGNORED_PREFIXES = ("/static/", "/media/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if self.should_audit(request):
            self.write_audit_log(request, response)
        return response

    def should_audit(self, request):
        if not getattr(settings, "AUDIT_LOG_ENABLED", True):
            return False
        if request.method not in self.WRITE_METHODS:
            return False
        if any(request.path.startswith(prefix) for prefix in self.IGNORED_PREFIXES):
            return False
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated)

    def write_audit_log(self, request, response):
        user = request.user
        ip_address = self.client_ip(request)
        try:
            AuditLog.objects.create(
                user=user,
                username=user.get_username(),
                action=self.classify_action(request),
                path=request.path[:500],
                method=request.method,
                status_code=getattr(response, "status_code", 0) or 0,
                ip_address=ip_address,
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
            )
        except (OperationalError, ProgrammingError):
            pass

    @staticmethod
    def classify_action(request):
        if request.method == "POST" and request.path.endswith("/delete/"):
            return "delete"
        if request.method == "POST" and request.path.endswith("/add/"):
            return "create"
        if request.method == "POST" and request.path.endswith("/edit/"):
            return "update"
        if request.method == "DELETE":
            return "delete"
        return request.method.lower()

    @staticmethod
    def client_ip(request):
        forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip() or None
        return request.META.get("REMOTE_ADDR") or None


