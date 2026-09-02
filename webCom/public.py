from django.contrib import messages
from django.db import DatabaseError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms_public import PublicJobApplicationForm
from .models import AssociatedLink, Client, CompanyEvent, Employee, JobApplication, JobPosting, Region, Site, WebsiteAdvertisement, WebsiteResource

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
