from django import forms

from .forms_base import DATE_WIDGET, StyledModelForm
from .models import AssociatedLink, CompanyEvent, JobApplication, JobPosting, WebsiteAdvertisement, WebsiteResource


class WebsiteAdvertisementForm(StyledModelForm):
    class Meta:
        model = WebsiteAdvertisement
        fields = "__all__"


class CompanyEventForm(StyledModelForm):
    class Meta:
        model = CompanyEvent
        fields = "__all__"
        widgets = {"event_date": DATE_WIDGET}


class WebsiteResourceForm(StyledModelForm):
    class Meta:
        model = WebsiteResource
        fields = "__all__"


class AssociatedLinkForm(StyledModelForm):
    class Meta:
        model = AssociatedLink
        fields = "__all__"


class JobPostingForm(StyledModelForm):
    class Meta:
        model = JobPosting
        fields = "__all__"
        widgets = {"deadline": DATE_WIDGET}


class PublicJobApplicationForm(StyledModelForm):
    class Meta:
        model = JobApplication
        fields = ["applicant_name", "phone_number", "email", "address", "qualification", "experience_summary", "cover_note", "resume"]
        widgets = {"experience_summary": forms.Textarea(attrs={"rows": 4}), "cover_note": forms.Textarea(attrs={"rows": 4})}


class JobApplicationForm(StyledModelForm):
    class Meta:
        model = JobApplication
        fields = "__all__"
        widgets = {"experience_summary": forms.Textarea(attrs={"rows": 3}), "cover_note": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "submitted_at" in self.fields:
            self.fields["submitted_at"].required = False
