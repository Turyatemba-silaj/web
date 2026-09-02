from datetime import time

from django import forms

from .models import Shift


DATE_WIDGET = forms.DateInput(attrs={"type": "date", "class": "form-control"})
TIME_WIDGET = forms.TimeInput(attrs={"type": "time", "class": "form-control"})
DATETIME_WIDGET = forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"})

DEFAULT_SHIFT_TIMES = {
    "day": (time(7, 0), time(18, 0)),
    "night": (time(18, 0), time(7, 0)),
}


def get_default_shift(shift_type):
    start_time, end_time = DEFAULT_SHIFT_TIMES[shift_type]
    shift, _ = Shift.objects.get_or_create(
        start_time=start_time,
        end_time=end_time,
        defaults={"hours_per_shift": 0, "shift_type": shift_type},
    )
    return shift


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        required_only = kwargs.pop("required_only", False)
        super().__init__(*args, **kwargs)
        if required_only:
            for field_name in list(self.fields):
                if not self.fields[field_name].required:
                    self.fields.pop(field_name)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(widget, forms.CheckboxSelectMultiple):
                continue
            else:
                widget.attrs.setdefault("class", "form-control")


class DateRangeValidationMixin:
    start_date_field = "start_date"
    end_date_field = "end_date"

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get(self.start_date_field)
        end_date = cleaned_data.get(self.end_date_field)
        if start_date and end_date and end_date < start_date:
            self.add_error(self.end_date_field, "End date cannot be earlier than start date.")
        return cleaned_data
