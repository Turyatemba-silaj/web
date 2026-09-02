import csv
import io
from datetime import date, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.forms import modelformset_factory
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from .forms_finance import ExpenseForm, InvoiceForm
from .forms_hr import DisciplinaryActionForm, PerformanceEvaluationForm
from .forms_operations import SiteForm
from .middleware import RequestAuditMiddleware
from .models import Advance, Attendance, AuditLog, Budget, BudgetNotification, Client, CompanyEvent, Contract, ContractDeliverable, Deployment, DeploymentArea, Disciplinary_Action, DisciplinaryNotification, Employee, Expense, ExpenseNotification, Invoice, InvoiceBillableItem, JobApplication, JobPosting, Leave, LeaveNotification, Paymee, PayrollDeduction, Payment, Performance_Evaluation, GoodsReceivedNote, ProcurementApproval, ProcurementRequisition, PurchaseOrder, Region, SupplierInvoice, SupplierPayment, SupplierProformaInvoice, SupplierProformaItemPrice, Shift, Site, Supplier, WebsiteAdvertisement, WebsiteResource, AssociatedLink
from .hr import notify_disciplinary_employee
from .operations import build_monthly_roster_matrix, parse_scheduled_period, rotating_scheduled_guards, write_monthly_roster_csv

def login_test_staff(client, username="staff"):
    user, _created = get_user_model().objects.get_or_create(username=username)
    user.set_password("test-pass")
    user.is_staff = True
    user.save()
    client.force_login(user)
    return user

class GuardRotationTests(SimpleTestCase):
    def test_schedules_required_guards_and_leaves_relief_guard_off(self):
        guards = list(range(1, 8))

        scheduled = rotating_scheduled_guards(
            guards,
            required_count=6,
            work_date=date(2026, 7, 20),
            cycle_start=date(2026, 7, 20),
        )

        self.assertEqual(scheduled, [1, 2, 3, 4, 5, 6])

    def test_rotates_off_guard_each_day(self):
        guards = list(range(1, 8))

        scheduled = rotating_scheduled_guards(
            guards,
            required_count=6,
            work_date=date(2026, 7, 21),
            cycle_start=date(2026, 7, 20),
        )

        self.assertEqual(scheduled, [2, 3, 4, 5, 6, 7])

class DutyRosterMatrixTests(TestCase):
    def test_parses_sample_period_with_extra_range(self):
        rows = [["Scheduled Period: 2023-02-26 to 2023-03-25", "2023.03.26 to", "2023.04.25"]]

        period_start, period_end = parse_scheduled_period(rows)

        self.assertEqual(period_start, date(2023, 2, 26))
        self.assertEqual(period_end, date(2023, 3, 25))

    def test_monthly_matrix_rotates_one_relief_guard_off(self):
        region = Region.objects.get(region_name="Central Three")
        site = Site.objects.create(
            region=region,
            site_name="BRAC Kasambya",
            site_address="Kasambya",
            day_shift_guards=3,
            night_shift_guards=3,
        )
        for index in range(7):
            guard = Employee.objects.create(
                first_name=f"Guard{index}",
                last_name="Test",
                date_of_birth=date(1990, 1, 1),
                gender="M",
                phone_number=f"07000000{index}",
                email=f"guard{index}@example.com",
                address="Kampala",
                national_id=f"NIN{index}",
                hire_date=date(2022, 1, 1),
                role="guard",
                status="active",
            )
            DeploymentArea.objects.create(employee=guard, region=region, start_date=date(2023, 2, 1), status="active")
            site.guards.add(guard)

        dates, rows = build_monthly_roster_matrix(site, date(2023, 2, 26), date(2023, 2, 27))

        self.assertEqual(len(dates), 2)
        for work_date in dates:
            markers = [row["markers"][work_date] for row in rows]
            self.assertEqual(markers.count("D"), 3)
            self.assertEqual(markers.count("N"), 3)
            self.assertEqual(markers.count("O"), 1)

    def test_export_layout_matches_monthly_roster_format(self):
        region = Region.objects.get(region_name="Central Three")
        site = Site.objects.create(
            region=region,
            site_name="BRAC Kasambya",
            site_address="Kasambya",
            day_shift_guards=1,
            night_shift_guards=0,
        )
        guard = Employee.objects.create(
            first_name="Saimon",
            last_name="Baluku",
            date_of_birth=date(1990, 1, 1),
            gender="M",
            phone_number="0700000000",
            email="saimon@example.com",
            address="Kampala",
            national_id="NIN-LAYOUT",
            hire_date=date(2022, 1, 1),
            role="guard",
            status="active",
        )
        DeploymentArea.objects.create(employee=guard, region=region, start_date=date(2023, 2, 1), status="active")
        site.guards.add(guard)
        output = io.StringIO()

        write_monthly_roster_csv(output, site, date(2023, 2, 26), date(2023, 3, 25))

        rows = list(csv.reader(io.StringIO(output.getvalue())))
        self.assertIn("Site Roster for BRAC Kasambya", rows[0][0])
        self.assertEqual(rows[1], [])
        self.assertEqual(rows[2][0], "Deployment Area: Central Three")
        self.assertEqual(rows[4][0], "Scheduled Period: 2023-02-26 to 2023-03-25")
        self.assertEqual(rows[4][2], "2023.03.26 to")
        self.assertEqual(rows[4][3], "2023.04.25")
        self.assertEqual(rows[6][:6], ["Pers No", "Grade", "Name", "Contact", "Worked days", "Su/26"])
        self.assertEqual(rows[7][2], "Saimon Baluku")
        self.assertEqual(rows[7][4], "")

    def test_export_ignores_deployment_area_guards_not_assigned_to_site(self):
        region = Region.objects.get(region_name="Central Three")
        site = Site.objects.create(
            region=region,
            site_name="BRAC Unassigned",
            site_address="Kasambya",
            day_shift_guards=1,
            night_shift_guards=0,
        )
        guard = Employee.objects.create(
            first_name="AreaOnly",
            last_name="Guard",
            date_of_birth=date(1990, 1, 1),
            gender="M",
            phone_number="0700000099",
            email="areaonly@example.com",
            address="Kampala",
            national_id="NIN-AREA-ONLY",
            hire_date=date(2022, 1, 1),
            role="guard",
            status="active",
        )
        DeploymentArea.objects.create(employee=guard, region=region, start_date=date(2023, 2, 1), status="active")

        _dates, rows = build_monthly_roster_matrix(site, date(2023, 2, 26), date(2023, 2, 26))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Shortage Guard")
class AttendanceRosterTests(TestCase):
    def test_attendance_uses_site_assigned_guards_without_deployment_area(self):
        region = Region.objects.create(region_name="Attendance Region")
        site = Site.objects.create(
            region=region,
            site_name="Attendance Site",
            site_address="Kampala",
            day_shift_guards=1,
            night_shift_guards=0,
        )
        guard = Employee.objects.create(
            first_name="Site",
            last_name="Guard",
            date_of_birth=date(1990, 1, 1),
            gender="M",
            phone_number="0711111111",
            email="siteguard@example.com",
            address="Kampala",
            national_id="NIN-SITE-GUARD",
            hire_date=date(2022, 1, 1),
            role="guard",
            status="active",
        )
        site.guards.add(guard)
        shift = Shift.objects.create(start_time=time(7, 0), end_time=time(18, 0), hours_per_shift=0)
        Deployment.objects.create(
            site=site,
            shift=shift,
            shift_coverage="day",
            start_date=date(2026, 7, 28),
            end_date=date(2026, 7, 28),
            status="active",
        )

        login_test_staff(self.client)
        response = self.client.get(f"/attendance/?site={site.pk}&mark_date=2026-07-28")

        self.assertContains(response, "Site Guard")
        self.assertContains(response, 'class="roster-data-row"')

    def test_site_assigned_guard_can_save_attendance_without_deployment_area_warning(self):
        region = Region.objects.create(region_name="Attendance Save Region")
        site = Site.objects.create(
            region=region,
            site_name="Attendance Save Site",
            site_address="Kampala",
            day_shift_guards=1,
            night_shift_guards=0,
        )
        guard = Employee.objects.create(
            first_name="Save",
            last_name="Guard",
            date_of_birth=date(1990, 1, 1),
            gender="M",
            phone_number="0722222222",
            email="saveguard@example.com",
            address="Kampala",
            national_id="NIN-SAVE-GUARD",
            hire_date=date(2022, 1, 1),
            role="guard",
            status="active",
        )
        site.guards.add(guard)
        shift = Shift.objects.create(start_time=time(7, 0), end_time=time(18, 0), hours_per_shift=0)
        deployment = Deployment.objects.create(
            site=site,
            shift=shift,
            shift_coverage="day",
            start_date=date(2026, 7, 28),
            end_date=date(2026, 7, 28),
            status="active",
        )

        Attendance.objects.create(
            deployment=deployment,
            site=site,
            shift=shift,
            scheduled_guard=guard,
            attended_guard=guard,
            employee=guard,
            present=True,
            date=date(2026, 7, 28),
            time_in=shift.start_time,
            time_out=shift.end_time,
        )

        self.assertEqual(Attendance.objects.count(), 1)
class InvoiceContractSyncTests(TestCase):
    def test_invoice_picks_contract_information(self):
        client = Client.objects.create(
            client_name="Equity Bank",
            contact_person="Jane Doe",
            phone_number="0700000000",
            email="client@example.com",
            address="Kampala Road",
        )
        contract = Contract.objects.create(
            client=client,
            contract_start_date=date(2023, 2, 26),
            contract_end_date=date(2023, 3, 25),
            day_shift_guards=5,
            night_shift_guards=2,
            rate_per_guard=Decimal("150000.00"),
            contract_status="active",
        )

        invoice = Invoice.objects.create(contract=contract, client=client)

        self.assertEqual(invoice.client, client)
        self.assertEqual(invoice.invoice_date, contract.contract_start_date)
        self.assertEqual(invoice.due_date, contract.contract_end_date)
        self.assertEqual(invoice.billing_start_date, contract.contract_start_date)
        self.assertEqual(invoice.billing_end_date, contract.contract_end_date)
        self.assertEqual(invoice.deployed_guards, 7)
        self.assertEqual(invoice.rate_per_guard, contract.rate_per_guard)
        self.assertEqual(invoice.contract_amount, contract.number_of_guards * contract.rate_per_guard)
        self.assertEqual(invoice.tax_amount, Decimal("189000.00"))
        self.assertEqual(invoice.total_amount, Decimal("1239000.00"))
        self.assertIn(contract.contract_number, invoice.description)

    def test_contract_updates_refresh_draft_invoice(self):
        client = Client.objects.create(
            client_name="Equity Bank",
            contact_person="Jane Doe",
            phone_number="0700000000",
            email="client@example.com",
            address="Kampala Road",
        )
        contract = Contract.objects.create(
            client=client,
            contract_start_date=date(2023, 2, 26),
            contract_end_date=date(2023, 3, 25),
            day_shift_guards=5,
            night_shift_guards=2,
            rate_per_guard=Decimal("150000.00"),
            contract_status="active",
        )
        invoice = Invoice.objects.create(contract=contract, client=client, status="draft")

        contract.day_shift_guards = 6
        contract.rate_per_guard = Decimal("175000.00")
        contract.save()
        invoice.refresh_from_db()

        self.assertEqual(invoice.deployed_guards, 8)
        self.assertEqual(invoice.rate_per_guard, contract.rate_per_guard)
        self.assertEqual(invoice.contract_amount, contract.number_of_guards * contract.rate_per_guard)

    def test_invoice_numbering_and_billable_products_share_one_invoice(self):
        client = Client.objects.create(
            client_name="Equity Bank",
            contact_person="Jane Doe",
            phone_number="0700000000",
            email="client-products@example.com",
            address="Kampala Road",
        )
        contract = Contract.objects.create(
            client=client,
            contract_start_date=date(2023, 2, 26),
            contract_end_date=date(2023, 3, 25),
            day_shift_guards=2,
            night_shift_guards=1,
            rate_per_guard=Decimal("150000.00"),
            contract_status="active",
        )
        gun = ContractDeliverable.objects.create(
            contract=contract,
            item_name="gun",
            quantity=2,
            unit_price=Decimal("50000.00"),
        )
        dog = ContractDeliverable.objects.create(
            contract=contract,
            item_name="dog",
            quantity=1,
            unit_price=Decimal("80000.00"),
        )

        invoice = Invoice.objects.create(contract=contract, client=client)
        second_invoice = Invoice.objects.create(contract=contract, client=client)
        lines = invoice.invoice_line_items()

        self.assertEqual(invoice.invoice_number, "INV001")
        self.assertEqual(second_invoice.invoice_number, "INV002")
        self.assertEqual(invoice.contract_amount, Decimal("630000.00"))
        self.assertEqual(invoice.tax_amount, Decimal("113400.00"))
        self.assertEqual(invoice.total_amount, Decimal("743400.00"))
        self.assertEqual([line["category"] for line in lines], ["Guarding Services", "Contract Billable Item", "Contract Billable Item"])
        self.assertIn(gun.amount, [line["amount"] for line in lines])
        self.assertIn(dog.amount, [line["amount"] for line in lines])

    def test_invoice_form_rejects_products_outside_selected_contract(self):
        client = Client.objects.create(
            client_name="Equity Bank",
            contact_person="Jane Doe",
            phone_number="0700000000",
            email="client-invalid-products@example.com",
            address="Kampala Road",
        )
        other_client = Client.objects.create(
            client_name="Centenary Bank",
            contact_person="John Doe",
            phone_number="0711111111",
            email="other-products@example.com",
            address="Entebbe Road",
        )
        contract = Contract.objects.create(
            client=client,
            contract_start_date=date(2023, 2, 26),
            contract_end_date=date(2023, 3, 25),
            day_shift_guards=1,
            night_shift_guards=1,
            rate_per_guard=Decimal("150000.00"),
            contract_status="active",
        )
        other_contract = Contract.objects.create(
            client=other_client,
            contract_start_date=date(2023, 2, 26),
            contract_end_date=date(2023, 3, 25),
            day_shift_guards=1,
            night_shift_guards=0,
            rate_per_guard=Decimal("120000.00"),
            contract_status="active",
        )
        detector = ContractDeliverable.objects.create(
            contract=other_contract,
            item_name="walk_through_detector",
            quantity=1,
            unit_price=Decimal("250000.00"),
        )

        form = InvoiceForm(data={
            "contract": str(contract.pk),
            "billable_products": [str(detector.pk)],
            "invoice_date": "2026-07-01",
            "due_date": "2026-07-31",
            "billing_start_date": "2026-07-01",
            "billing_end_date": "2026-07-31",
            "amendment_amount": "0",
            "amendment_reason": "",
            "status": "draft",
        })

        self.assertFalse(form.is_valid())
        self.assertIn("billable_products", form.errors)
    def test_invoice_can_bill_selected_sites_under_one_contract(self):
        client = Client.objects.create(
            client_name="Equity Bank",
            contact_person="Jane Doe",
            phone_number="0700000000",
            email="client-sites@example.com",
            address="Kampala Road",
        )
        contract = Contract.objects.create(
            client=client,
            contract_start_date=date(2023, 2, 26),
            contract_end_date=date(2023, 3, 25),
            day_shift_guards=5,
            night_shift_guards=2,
            rate_per_guard=Decimal("150000.00"),
            contract_status="active",
        )
        site_one = Site.objects.create(
            client=client,
            contract=contract,
            site_name="Equity Head Office",
            site_address="Kampala Road",
            day_shift_guards=2,
            night_shift_guards=1,
        )
        site_two = Site.objects.create(
            client=client,
            contract=contract,
            site_name="Equity Branch",
            site_address="Jinja Road",
            day_shift_guards=3,
            night_shift_guards=1,
        )
        invoice = Invoice.objects.create(contract=contract, client=client)
        invoice.sites.set([site_one, site_two])
        invoice.save()

        self.assertEqual(invoice.deployed_guards, 7)
        self.assertEqual(invoice.contract_amount, Decimal("1050000.00"))
        self.assertEqual(len(invoice.invoice_line_items()), 2)

    def test_invoice_add_view_accepts_provisional_billable_items_with_vat(self):
        client = Client.objects.create(
            client_name="Equity Bank",
            contact_person="Jane Doe",
            phone_number="0700000000",
            email="client-provisional@example.com",
            address="Kampala Road",
        )
        contract = Contract.objects.create(
            client=client,
            contract_start_date=date(2023, 2, 26),
            contract_end_date=date(2023, 3, 25),
            day_shift_guards=1,
            night_shift_guards=1,
            rate_per_guard=Decimal("150000.00"),
            contract_status="active",
        )

        login_test_staff(self.client)
        response = self.client.post("/invoices/add/", data={
            "contract": str(contract.pk),
            "invoice_date": "2026-07-01",
            "due_date": "2026-07-31",
            "billing_start_date": "2026-07-01",
            "billing_end_date": "2026-07-31",
            "amendment_amount": "0",
            "amendment_reason": "",
            "tax_rate": "18",
            "status": "draft",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-item_name": "vehicle",
            "items-0-description": "Patrol vehicle hire",
            "items-0-quantity": "2",
            "items-0-unit": "vehicle(s)",
            "items-0-unit_price": "100000",
            "items-0-taxable": "on",
        })

        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get()
        item = InvoiceBillableItem.objects.get(invoice=invoice)
        self.assertEqual(item.amount, Decimal("200000.00"))
        self.assertEqual(invoice.contract_amount, Decimal("500000.00"))
        self.assertEqual(invoice.tax_amount, Decimal("90000.00"))
        self.assertEqual(invoice.total_amount, Decimal("590000.00"))
    def test_invoice_add_view_can_split_contract_sites_into_separate_invoices(self):
        client = Client.objects.create(
            client_name="Equity Bank",
            contact_person="Jane Doe",
            phone_number="0700000000",
            email="client-split@example.com",
            address="Kampala Road",
        )
        contract = Contract.objects.create(
            client=client,
            contract_start_date=date(2023, 2, 26),
            contract_end_date=date(2023, 3, 25),
            day_shift_guards=5,
            night_shift_guards=2,
            rate_per_guard=Decimal("150000.00"),
            contract_status="active",
        )
        region = Region.objects.get(region_name="Central Region")
        site_one = Site.objects.create(client=client, contract=contract, region=region, site_name="Head Office", site_address="A", day_shift_guards=2, night_shift_guards=1)
        site_two = Site.objects.create(client=client, contract=contract, region=region, site_name="Branch", site_address="B", day_shift_guards=3, night_shift_guards=1)

        login_test_staff(self.client)
        response = self.client.post("/invoices/add/", data={
            "invoice_mode": "split_sites",
            "contract": str(contract.pk),
            "invoice_date": "2026-07-01",
            "due_date": "2026-07-31",
            "billing_start_date": "2026-07-01",
            "billing_end_date": "2026-07-31",
            "amendment_amount": "0",
            "amendment_reason": "",
            "tax_rate": "18",
            "status": "draft",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-item_name": "dog",
            "items-0-quantity": "1",
            "items-0-unit_price": "100000",
            "items-0-taxable": "on",
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/invoices/")
        invoices = list(Invoice.objects.order_by("invoice_number"))
        self.assertEqual([invoice.invoice_number for invoice in invoices], ["INV001", "INV002"])
        self.assertEqual(InvoiceBillableItem.objects.count(), 0)
        self.assertEqual([invoice.sites.count() for invoice in invoices], [1, 1])
        totals_by_site = {invoice.sites.get().site_name: invoice for invoice in invoices}
        self.assertEqual(totals_by_site[site_one.site_name].contract_amount, Decimal("450000.00"))
        self.assertEqual(totals_by_site[site_one.site_name].total_amount, Decimal("531000.00"))
        self.assertEqual(totals_by_site[site_two.site_name].contract_amount, Decimal("600000.00"))
        self.assertEqual(totals_by_site[site_two.site_name].total_amount, Decimal("708000.00"))
    def test_invoice_formset_create_does_not_require_save_m2m_method(self):
        client = Client.objects.create(
            client_name="Equity Bank",
            contact_person="Jane Doe",
            phone_number="0700000000",
            email="client-formset@example.com",
            address="Kampala Road",
        )
        contract = Contract.objects.create(
            client=client,
            contract_start_date=date(2023, 2, 26),
            contract_end_date=date(2023, 3, 25),
            day_shift_guards=2,
            night_shift_guards=1,
            rate_per_guard=Decimal("150000.00"),
            contract_status="active",
        )
        InvoiceFormSet = modelformset_factory(
            Invoice,
            form=InvoiceForm,
            fields=("contract", "sites", "invoice_date", "due_date", "billing_start_date", "billing_end_date", "amendment_amount", "amendment_reason", "status"),
            extra=1,
            can_delete=False,
        )
        formset = InvoiceFormSet(
            data={
                "records-TOTAL_FORMS": "1",
                "records-INITIAL_FORMS": "0",
                "records-MIN_NUM_FORMS": "0",
                "records-MAX_NUM_FORMS": "1000",
                "records-0-contract": str(contract.pk),
                "records-0-invoice_date": "2026-07-01",
                "records-0-due_date": "2026-07-31",
                "records-0-billing_start_date": "2026-07-01",
                "records-0-billing_end_date": "2026-07-31",
                "records-0-amendment_amount": "0",
                "records-0-amendment_reason": "",
                "records-0-status": "draft",
            },
            queryset=Invoice.objects.none(),
            prefix="records",
        )

        self.assertTrue(formset.is_valid(), formset.errors)
        invoices = formset.save()

        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices[0].client, client)
        self.assertEqual(invoices[0].deployed_guards, 3)

    def test_invoice_add_view_creates_one_invoice_for_multiple_sites(self):
        client = Client.objects.create(
            client_name="Equity Bank",
            contact_person="Jane Doe",
            phone_number="0700000000",
            email="client-view@example.com",
            address="Kampala Road",
        )
        contract = Contract.objects.create(
            client=client,
            contract_start_date=date(2023, 2, 26),
            contract_end_date=date(2023, 3, 25),
            day_shift_guards=5,
            night_shift_guards=2,
            rate_per_guard=Decimal("150000.00"),
            contract_status="active",
        )
        region = Region.objects.get(region_name="Central Region")
        site_one = Site.objects.create(client=client, contract=contract, region=region, site_name="Head Office", site_address="A", day_shift_guards=2, night_shift_guards=1)
        site_two = Site.objects.create(client=client, contract=contract, region=region, site_name="Branch", site_address="B", day_shift_guards=3, night_shift_guards=1)

        login_test_staff(self.client)
        response = self.client.post("/invoices/add/", data={
            "contract": str(contract.pk),
            "sites": [str(site_one.pk), str(site_two.pk)],
            "invoice_date": "2026-07-01",
            "due_date": "2026-07-31",
            "billing_start_date": "2026-07-01",
            "billing_end_date": "2026-07-31",
            "amendment_amount": "0",
            "amendment_reason": "",
            "status": "draft",
        })

        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.sites.count(), 2)
        self.assertEqual(invoice.invoiced_site_count, 2)
        self.assertEqual(invoice.deployed_guards, 7)


class LeaveReviewWorkflowTests(TestCase):
    def make_employee(self, first_name, role, department, email):
        return Employee.objects.create(
            first_name=first_name,
            last_name="Tester",
            date_of_birth=date(1990, 1, 1),
            gender="M",
            phone_number=f"07{Employee.objects.count():08d}",
            email=email,
            address="Kampala",
            national_id=f"NIN-{first_name}",
            hire_date=date(2022, 1, 1),
            role=role,
            department=department,
            status="active",
        )

    def test_operations_verified_leave_can_be_approved_by_hr_with_feedback(self):
        employee = self.make_employee("Applicant", "guard", "operations", "applicant@example.com")
        operations_manager = self.make_employee("Ops", "manager", "operations", "ops@example.com")
        hr_manager = self.make_employee("Hr", "manager", "hr", "hr@example.com")
        leave = Leave.objects.create(
            employee=employee,
            leave_type="annual",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 5),
            reason="Family appointment",
        )

        leave.process_review(
            operations_manager=operations_manager,
            operations_status="verified",
            operations_feedback="Deployment cover is available.",
            hr_manager=hr_manager,
            hr_decision="approved",
            feedback="Approved. Report back on 2026-08-06.",
        )

        leave.refresh_from_db()
        self.assertEqual(leave.operations_verification_status, "verified")
        self.assertEqual(leave.approval_status, "approved")
        self.assertEqual(leave.verified_by, operations_manager)
        self.assertEqual(leave.approved_by, hr_manager)
        self.assertIn("Report back", leave.feedback)
        self.assertIsNotNone(leave.operations_verified_at)
        self.assertIsNotNone(leave.hr_decided_at)

    def test_operations_rejection_rejects_leave_and_records_employee_feedback(self):
        employee = self.make_employee("Rejected", "guard", "operations", "rejected@example.com")
        operations_manager = self.make_employee("OpsReject", "manager", "operations", "opsreject@example.com")
        leave = Leave.objects.create(
            employee=employee,
            leave_type="medical",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 2),
            reason="Clinic visit",
        )

        leave.process_review(
            operations_manager=operations_manager,
            operations_status="rejected",
            operations_feedback="Staffing level is below minimum.",
        )

        leave.refresh_from_db()
        self.assertEqual(leave.operations_verification_status, "rejected")
        self.assertEqual(leave.approval_status, "rejected")
        self.assertEqual(leave.feedback, "Staffing level is below minimum.")

    def test_leave_submission_creates_actionable_notifications(self):
        employee = self.make_employee("NoticeApplicant", "guard", "operations", "notice-applicant@example.com")
        supervisor = self.make_employee("NoticeSupervisor", "supervisor", "operations", "notice-supervisor@example.com")
        hod = self.make_employee("NoticeHod", "manager", "operations", "notice-hod@example.com")
        stand_in = self.make_employee("NoticeStandIn", "guard", "operations", "notice-standin@example.com")
        leave = Leave.objects.create(
            employee=employee,
            leave_type="annual",
            start_date=date(2026, 9, 20),
            end_date=date(2026, 9, 22),
            reason="Family appointment",
            address_while_away="Kampala",
            emergency_contact="0700000000",
            supervisor=supervisor,
            hod=hod,
            stand_in_coworker=stand_in,
            application_status="submitted",
        )

        leave.notify_submission()

        self.assertTrue(LeaveNotification.objects.filter(leave=leave, recipient=supervisor, recipient_group="Verifier").exists())
        self.assertTrue(LeaveNotification.objects.filter(leave=leave, recipient=hod, recipient_group="Approver").exists())
        self.assertTrue(LeaveNotification.objects.filter(leave=leave, recipient=stand_in, recipient_group="Stand-In").exists())

        login_test_staff(self.client, "leave-notification-staff")
        verifier_notification = leave.notifications.get(recipient=supervisor, recipient_group="Verifier")
        response = self.client.post(reverse("webcom:leave_notification_action", args=[verifier_notification.pk, "verify"]))
        self.assertEqual(response.status_code, 302)
        leave.refresh_from_db()
        self.assertEqual(leave.operations_verification_status, "verified")
        self.assertEqual(leave.verified_by, supervisor)

        approver_notification = leave.notifications.get(recipient=hod, recipient_group="Approver")
        response = self.client.post(reverse("webcom:leave_notification_action", args=[approver_notification.pk, "approve"]))
        self.assertEqual(response.status_code, 302)
        leave.refresh_from_db()
        self.assertEqual(leave.approval_status, "approved")
        self.assertEqual(leave.approved_by, hod)
        self.assertTrue(LeaveNotification.objects.filter(leave=leave, recipient=employee, notification_type="leave_approved").exists())

class EmployeeNumberTests(TestCase):
    def make_employee(self, first_name, role, department, national_id):
        return Employee.objects.create(
            first_name=first_name,
            last_name="Numbering",
            date_of_birth=date(1995, 1, 1),
            gender="M",
            phone_number=f"0788{Employee.objects.count():06d}",
            email=f"{first_name.lower()}@example.com",
            address="Kampala",
            national_id=national_id,
            hire_date=date(2026, 7, 1),
            role=role,
            department=department,
            status="active",
        )

    def test_non_security_staff_use_incrementing_adm_numbers(self):
        manager = self.make_employee("AdminOne", "manager", "admin", "NIN-ADM-001")
        hr_officer = self.make_employee("AdminTwo", "hr_officer", "hr", "NIN-ADM-002")

        self.assertEqual(manager.employee_number, "ADM 001")
        self.assertEqual(hr_officer.employee_number, "ADM 002")

    def test_security_guards_keep_seniority_employee_numbers(self):
        guard = self.make_employee("GuardNumber", "guard", "operations", "NIN-GUARD-001")

        self.assertEqual(guard.employee_number, "TE001")

    def test_supervisors_use_incrementing_sup_numbers(self):
        supervisor_one = self.make_employee("SupervisorOne", "supervisor", "operations", "NIN-SUP-001")
        supervisor_two = self.make_employee("SupervisorTwo", "supervisor", "operations", "NIN-SUP-002")

        self.assertEqual(supervisor_one.employee_number, "SUP 001")
        self.assertEqual(supervisor_two.employee_number, "SUP 002")

class SiteDeploymentAreaTests(TestCase):
    def test_site_infers_central_region_from_kampala_location(self):
        site = Site.objects.create(
            site_name="Equity Bank Kampala Road",
            site_address="Kampala Road",
            day_shift_guards=1,
            night_shift_guards=1,
        )

        self.assertEqual(site.region.region_name, "Central Region")

    def test_site_infers_western_region_from_mbarara_location(self):
        site = Site.objects.create(
            site_name="Equity Bank Mbarara",
            site_address="Mbarara town",
            day_shift_guards=1,
            night_shift_guards=1,
        )

        self.assertEqual(site.region.region_name, "Western Region")

    def make_employee(self, first_name, is_reliever=False):
        return Employee.objects.create(
            first_name=first_name,
            last_name="Assignment",
            date_of_birth=date(1995, 1, 1),
            gender="M",
            phone_number=f"0700{Employee.objects.count():06d}",
            email=f"{first_name.lower()}@example.com",
            address="Kampala",
            national_id=f"NIN-SITE-{first_name}",
            hire_date=date(2026, 1, 1),
            role="guard",
            department="operations",
            status="active",
            is_reliever=is_reliever,
        )

    def make_contract(self):
        client = Client.objects.create(
            client_name="Assignment Client",
            contact_person="Ops",
            phone_number="0700000000",
            email="ops@example.com",
            address="Kampala",
        )
        return Contract.objects.create(
            client=client,
            contract_start_date=date(2026, 1, 1),
            contract_end_date=date(2026, 12, 31),
            day_shift_guards=4,
            night_shift_guards=0,
            rate_per_guard=Decimal("1000.00"),
        )

    def test_site_form_uses_deployment_area_and_assigned_guards(self):
        form = SiteForm()

        self.assertIn("region", form.fields)
        self.assertEqual(form.fields["region"].label, "Deployment Area")
        self.assertIn("guards", form.fields)


    def test_site_form_guard_choices_are_limited_to_selected_deployment_area(self):
        selected_region = Region.objects.create(region_name="Selected Guard Area")
        other_region = Region.objects.create(region_name="Other Guard Area")
        selected_guard = self.make_employee("SelectedAreaGuard")
        other_guard = self.make_employee("OtherAreaGuard")
        DeploymentArea.objects.create(employee=selected_guard, region=selected_region, start_date=timezone.localdate(), status="active")
        DeploymentArea.objects.create(employee=other_guard, region=other_region, start_date=timezone.localdate(), status="active")

        form = SiteForm(data={"region": selected_region.pk})

        self.assertIn(selected_guard, form.fields["guards"].queryset)
        self.assertNotIn(other_guard, form.fields["guards"].queryset)

    def test_site_form_rejects_guard_outside_selected_deployment_area(self):
        selected_region = Region.objects.create(region_name="Reject Selected Area")
        other_region = Region.objects.create(region_name="Reject Other Area")
        contract = self.make_contract()
        guard = self.make_employee("WrongAreaGuard")
        DeploymentArea.objects.create(employee=guard, region=other_region, start_date=timezone.localdate(), status="active")

        form = SiteForm(data={
            "region": selected_region.pk,
            "client": contract.client.pk,
            "contract": contract.pk,
            "site_name": "Wrong Area Site",
            "site_address": "Kampala",
            "day_shift_guards": 1,
            "night_shift_guards": 0,
            "guards": [guard.pk],
        })

        self.assertFalse(form.is_valid())
        self.assertIn("Select a valid choice", str(form.errors["guards"]))

    def test_site_form_blocks_non_reliever_assigned_to_another_site(self):
        region = Region.objects.create(region_name="Assignment Region")
        contract = self.make_contract()
        guard = self.make_employee("NormalGuard")
        DeploymentArea.objects.create(employee=guard, region=region, start_date=timezone.localdate(), status="active")
        existing_site = Site.objects.create(
            client=contract.client,
            contract=contract,
            region=region,
            site_name="Existing Site",
            site_address="Kampala",
            day_shift_guards=1,
            night_shift_guards=0,
        )
        existing_site.guards.add(guard)

        form = SiteForm(data={
            "region": region.pk,
            "client": contract.client.pk,
            "contract": contract.pk,
            "site_name": "New Site",
            "site_address": "Kampala",
            "day_shift_guards": 1,
            "night_shift_guards": 0,
            "guards": [guard.pk],
        })

        self.assertFalse(form.is_valid())
        self.assertIn("Only relievers may be assigned to more than one site", str(form.errors["guards"]))

    def test_reliever_can_be_assigned_to_multiple_sites(self):
        region = Region.objects.create(region_name="Reliever Region")
        contract = self.make_contract()
        guard = self.make_employee("RelieverGuard", is_reliever=True)
        DeploymentArea.objects.create(employee=guard, region=region, start_date=timezone.localdate(), status="active")
        existing_site = Site.objects.create(
            client=contract.client,
            contract=contract,
            region=region,
            site_name="Existing Reliever Site",
            site_address="Kampala",
            day_shift_guards=1,
            night_shift_guards=0,
        )
        existing_site.guards.add(guard)

        form = SiteForm(data={
            "region": region.pk,
            "client": contract.client.pk,
            "contract": contract.pk,
            "site_name": "New Reliever Site",
            "site_address": "Kampala",
            "day_shift_guards": 1,
            "night_shift_guards": 0,
            "guards": [guard.pk],
        })

        self.assertTrue(form.is_valid(), form.errors)

    def test_direct_site_assignment_blocks_non_reliever_on_second_site(self):
        region = Region.objects.create(region_name="Direct Assignment Region")
        contract = self.make_contract()
        guard = self.make_employee("DirectGuard")
        site_one = Site.objects.create(
            client=contract.client,
            contract=contract,
            region=region,
            site_name="Direct Site One",
            site_address="Kampala",
            day_shift_guards=1,
            night_shift_guards=0,
        )
        site_two = Site.objects.create(
            client=contract.client,
            contract=contract,
            region=region,
            site_name="Direct Site Two",
            site_address="Kampala",
            day_shift_guards=1,
            night_shift_guards=0,
        )
        site_one.guards.add(guard)

        with self.assertRaises(ValidationError):
            site_two.guards.add(guard)
class DisciplinaryActionWorkflowTests(TestCase):
    def make_employee(self, first_name, role="guard", department="operations"):
        return Employee.objects.create(
            first_name=first_name,
            last_name="Discipline",
            date_of_birth=date(1990, 1, 1),
            gender="M",
            phone_number=f"0777{Employee.objects.count():06d}",
            email=f"{first_name.lower()}@example.com",
            address="Kampala",
            national_id=f"NIN-DISC-{first_name}",
            hire_date=date(2023, 1, 1),
            role=role,
            department=department,
            status="active",
        )

    def test_disciplinary_action_tracks_steps_to_conclusion(self):
        employee = self.make_employee("Offender")
        manager = self.make_employee("Handler", role="manager", department="hr")

        action = Disciplinary_Action.objects.create(
            employee=employee,
            offence_committed="Absconding from duty post",
            offence_date=date(2026, 7, 1),
            action_date=date(2026, 7, 2),
            reported_by=manager,
            description="Employee left the assigned site before shift handover.",
            investigation_notes="Supervisor statement and attendance record reviewed.",
            hearing_date=date(2026, 7, 3),
            hearing_notes="Employee admitted leaving early without permission.",
            steps_taken="Written warning issued and redeployment briefing completed.",
            outcome="written_warning",
            conclusion="Case closed after warning and counselling.",
            concluded_on=date(2026, 7, 4),
            handled_by=manager,
            approval_status="approved",
        )

        self.assertEqual(action.status, "concluded")
        self.assertIn("Offence committed: Absconding from duty post", action.reason)
        self.assertIn("Steps taken: Written warning issued", action.reason)
        self.assertIn("Conclusion: Case closed", action.reason)


    def test_disciplinary_add_view_returns_form_error_when_steps_are_missing(self):
        employee = self.make_employee("ViewNoSteps")

        login_test_staff(self.client)
        response = self.client.post("/disciplinary-actions/add/", data={
            "records-TOTAL_FORMS": "1",
            "records-INITIAL_FORMS": "0",
            "records-MIN_NUM_FORMS": "0",
            "records-MAX_NUM_FORMS": "1000",
            "records-0-employee": str(employee.pk),
            "records-0-offence_committed": "Late reporting",
            "records-0-offence_date": "2026-07-01",
            "records-0-action_date": "2026-07-02",
            "records-0-description": "Reported late for duty.",
            "records-0-investigation_notes": "Attendance reviewed.",
            "records-0-hearing_date": "",
            "records-0-hearing_notes": "",
            "records-0-steps_taken": "",
            "records-0-outcome": "written_warning",
            "records-0-conclusion": "",
            "records-0-concluded_on": "",
            "records-0-handled_by": "",
            "records-0-reported_by": "",
            "records-0-approval_status": "approved",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "State the disciplinary steps taken")
        self.assertFalse(Disciplinary_Action.objects.exists())


    def test_disciplinary_feedback_notification_is_created_for_employee(self):
        employee = self.make_employee("NotifyVictim")
        action = Disciplinary_Action.objects.create(
            employee=employee,
            offence_committed="Sleeping on duty",
            offence_date=date(2026, 7, 1),
            action_date=date(2026, 7, 2),
            steps_taken="Written warning issued.",
            outcome="written_warning",
            conclusion="Employee warned and retained.",
            concluded_on=date(2026, 7, 3),
        )

        notification = notify_disciplinary_employee(action)

        self.assertIsNotNone(notification)
        self.assertEqual(notification.recipient, employee)
        self.assertIn("Sleeping on duty", notification.message)
        self.assertIn("Written warning issued", notification.message)
        self.assertEqual(DisciplinaryNotification.objects.count(), 1)

    def test_disciplinary_add_view_creates_feedback_notification(self):
        employee = self.make_employee("ViewNotify")

        login_test_staff(self.client)
        response = self.client.post("/disciplinary-actions/add/", data={
            "records-TOTAL_FORMS": "1",
            "records-INITIAL_FORMS": "0",
            "records-MIN_NUM_FORMS": "0",
            "records-MAX_NUM_FORMS": "1000",
            "records-0-employee": str(employee.pk),
            "records-0-offence_committed": "Uniform misconduct",
            "records-0-offence_date": "2026-07-01",
            "records-0-action_date": "2026-07-02",
            "records-0-description": "Uniform not worn correctly.",
            "records-0-investigation_notes": "Supervisor report reviewed.",
            "records-0-hearing_date": "",
            "records-0-hearing_notes": "",
            "records-0-steps_taken": "Verbal warning issued.",
            "records-0-outcome": "verbal_warning",
            "records-0-conclusion": "Employee corrected the issue.",
            "records-0-concluded_on": "2026-07-03",
            "records-0-handled_by": "",
            "records-0-reported_by": "",
            "records-0-approval_status": "approved",
        })

        self.assertEqual(response.status_code, 302)
        notification = DisciplinaryNotification.objects.get()
        self.assertEqual(notification.recipient, employee)
        self.assertIn("Uniform misconduct", notification.message)

    def test_disciplinary_form_requires_steps_before_outcome(self):
        employee = self.make_employee("NoSteps")
        form = DisciplinaryActionForm(data={
            "employee": str(employee.pk),
            "offence_committed": "Late reporting",
            "offence_date": "2026-07-01",
            "action_date": "2026-07-02",
            "description": "Reported late for duty.",
            "investigation_notes": "Attendance reviewed.",
            "hearing_date": "",
            "hearing_notes": "",
            "steps_taken": "",
            "outcome": "verbal_warning",
            "conclusion": "",
            "concluded_on": "",
            "handled_by": "",
            "reported_by": "",
            "approval_status": "pending",
        })

        self.assertFalse(form.is_valid())
        self.assertIn("steps_taken", form.errors)





class PerformanceEvaluationStandardTests(TestCase):
    def make_employee(self, first_name, role="guard", department="operations"):
        return Employee.objects.create(
            first_name=first_name,
            last_name="Performance",
            date_of_birth=date(1990, 1, 1),
            gender="M",
            phone_number=f"0766{Employee.objects.count():06d}",
            email=f"{first_name.lower()}@example.com",
            address="Kampala",
            national_id=f"NIN-PERF-{first_name}",
            hire_date=date(2023, 1, 1),
            role=role,
            department=department,
            status="active",
        )

    def test_evaluated_by_dropdown_only_shows_supervisors_and_admin_staff_numbers(self):
        guard = self.make_employee("EvaluatorGuard", role="guard", department="operations")
        supervisor = self.make_employee("EvaluatorSupervisor", role="supervisor", department="operations")
        manager = self.make_employee("EvaluatorManager", role="manager", department="admin")
        form = PerformanceEvaluationForm()

        evaluator_ids = set(form.fields["evaluated_by"].queryset.values_list("pk", flat=True))
        self.assertNotIn(guard.pk, evaluator_ids)
        self.assertIn(supervisor.pk, evaluator_ids)
        self.assertIn(manager.pk, evaluator_ids)
        self.assertEqual(form.fields["evaluated_by"].label_from_instance(supervisor), supervisor.employee_number_name)


    def test_evaluation_calculates_overall_score_and_rating(self):
        employee = self.make_employee("Rated")
        reviewer = self.make_employee("Reviewer", role="manager", department="hr")

        evaluation = Performance_Evaluation.objects.create(
            employee=employee,
            date=date(2026, 7, 20),
            review_period_start=date(2026, 1, 1),
            review_period_end=date(2026, 6, 30),
            evaluated_by=reviewer,
            job_knowledge=5,
            quality_of_work=4,
            productivity=4,
            reliability_attendance=5,
            communication=4,
            teamwork=4,
            discipline_compliance=5,
            customer_service=4,
            initiative_problem_solving=4,
            safety_security_awareness=5,
            strengths="Reliable and disciplined.",
            areas_for_improvement="More radio procedure practice.",
            goals="Prepare for supervisor relief duties.",
        )

        self.assertEqual(evaluation.overall_score, Decimal("4.40"))
        self.assertEqual(evaluation.rating, "4")
        self.assertIn("Overall score: 4.40", evaluation.comments)
        self.assertIn("Strengths: Reliable", evaluation.comments)

    def test_evaluation_form_rejects_invalid_review_period(self):
        employee = self.make_employee("BadPeriod")
        form = PerformanceEvaluationForm(data={
            "employee": str(employee.pk),
            "date": "2026-07-20",
            "review_period_start": "2026-06-30",
            "review_period_end": "2026-01-01",
            "evaluated_by": "",
            "job_knowledge": "3",
            "quality_of_work": "3",
            "productivity": "3",
            "reliability_attendance": "3",
            "communication": "3",
            "teamwork": "3",
            "discipline_compliance": "3",
            "customer_service": "3",
            "initiative_problem_solving": "3",
            "safety_security_awareness": "3",
            "strengths": "",
            "areas_for_improvement": "",
            "goals": "",
            "training_recommendations": "",
            "supervisor_comments": "",
            "employee_comments": "",
            "status": "reviewed",
        })

        self.assertFalse(form.is_valid())
        self.assertIn("review_period_end", form.errors)

class ReceivablesLedgerTests(TestCase):
    def make_invoice(self):
        client = Client.objects.create(
            client_name="Equity Bank",
            contact_person="Jane Doe",
            phone_number="0700000000",
            email="receivables@example.com",
            address="Kampala Road",
        )
        contract = Contract.objects.create(
            client=client,
            contract_start_date=date(2026, 7, 1),
            contract_end_date=date(2026, 7, 31),
            day_shift_guards=2,
            night_shift_guards=1,
            rate_per_guard=Decimal("100000.00"),
            contract_status="active",
        )
        return Invoice.objects.create(
            contract=contract,
            client=client,
            invoice_date=date(2026, 7, 1),
            due_date=date(2026, 7, 31),
            status="sent",
        )

    def test_payment_dynamically_creates_receivable(self):
        invoice = self.make_invoice()

        Payment.objects.create(
            invoice=invoice,
            payment_date=date(2026, 7, 10),
            amount=Decimal("100000.00"),
            payment_method="bank_transfer",
        )

        receivable = Paymee.objects.get(invoice=invoice)
        self.assertEqual(receivable.client, invoice.client)
        self.assertEqual(receivable.total_amount, invoice.total_amount)
        self.assertEqual(receivable.amount_paid, Decimal("100000.00"))
        self.assertEqual(receivable.balance_amount, invoice.total_amount - Decimal("100000.00"))
        self.assertEqual(receivable.last_payment_date, date(2026, 7, 10))
        self.assertEqual(receivable.status, "partial")

    def test_payment_receipt_updates_receivable_status(self):
        invoice = self.make_invoice()
        receivable = Paymee.objects.create(invoice=invoice)

        Payment.objects.create(
            invoice=invoice,
            payment_date=date(2026, 7, 10),
            amount=Decimal("100000.00"),
            payment_method="bank_transfer",
        )
        receivable.refresh_from_db()
        self.assertEqual(receivable.amount_paid, Decimal("100000.00"))
        self.assertEqual(receivable.balance_amount, invoice.total_amount - Decimal("100000.00"))
        self.assertEqual(receivable.last_payment_date, date(2026, 7, 10))
        self.assertEqual(receivable.status, "partial")

        Payment.objects.create(
            invoice=invoice,
            payment_date=date(2026, 7, 12),
            amount=invoice.total_amount - Decimal("100000.00"),
            payment_method="cash",
        )
        receivable.refresh_from_db()
        self.assertEqual(receivable.amount_paid, invoice.total_amount)
        self.assertEqual(receivable.balance_amount, Decimal("0.00"))
        self.assertEqual(receivable.status, "paid")

    def test_receivables_list_syncs_from_existing_payments_and_is_read_only(self):
        invoice = self.make_invoice()
        Payment.objects.create(
            invoice=invoice,
            payment_date=date(2026, 7, 10),
            amount=Decimal("100000.00"),
            payment_method="bank_transfer",
        )
        Paymee.objects.filter(invoice=invoice).delete()

        login_test_staff(self.client)
        response = self.client.get("/paymees/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Paymee.objects.filter(invoice=invoice).exists())
        self.assertContains(response, invoice.invoice_number)
        self.assertNotContains(response, ">Add</a>")
        self.assertNotContains(response, ">Edit</a>")
        self.assertNotContains(response, ">Delete</a>")

    def test_receivable_create_redirects_to_payment_captured_ledger(self):
        login_test_staff(self.client)
        response = self.client.get("/paymees/add/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/paymees/")

class PaymentDocumentTests(TestCase):
    def make_payment(self):
        client = Client.objects.create(
            client_name="Equity Bank",
            contact_person="Jane Doe",
            phone_number="0700000000",
            email="payment-docs@example.com",
            address="Kampala Road",
        )
        contract = Contract.objects.create(
            client=client,
            contract_start_date=date(2026, 7, 1),
            contract_end_date=date(2026, 7, 31),
            day_shift_guards=2,
            night_shift_guards=1,
            rate_per_guard=Decimal("100000.00"),
            contract_status="active",
        )
        invoice = Invoice.objects.create(
            contract=contract,
            client=client,
            invoice_date=date(2026, 7, 1),
            due_date=date(2026, 7, 31),
            status="sent",
        )
        return Payment.objects.create(
            invoice=invoice,
            payment_date=date(2026, 7, 10),
            amount=Decimal("100000.00"),
            payment_method="bank_transfer",
            transaction_ref="BNK-001",
        )

    def test_payment_receipt_document_renders(self):
        payment = self.make_payment()

        login_test_staff(self.client)
        response = self.client.get(f"/payments/{payment.pk}/receipt/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RECEIPT")
        self.assertContains(response, "RCT-")
        self.assertContains(response, "Amount Received")
        self.assertContains(response, "BNK-001")

    def test_payment_reconciliation_document_renders(self):
        payment = self.make_payment()

        login_test_staff(self.client)
        response = self.client.get(f"/payments/{payment.pk}/reconciliation/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RECONCILIATION")
        self.assertContains(response, "Payment Ledger")
        self.assertContains(response, "Balance After Current Payment")

    def test_payment_list_has_receipt_and_reconciliation_buttons(self):
        self.make_payment()

        login_test_staff(self.client)
        response = self.client.get("/payments/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Receipt")
        self.assertContains(response, "Reconciliation")


class BudgetAccountabilityTests(TestCase):
    def make_employee(self, role, index, department=None):
        defaults = {
            "finance_officer": "finance",
            "hr_officer": "hr",
            "administrator": "admin",
            "manager": "operations",
        }
        return Employee.objects.create(
            first_name=f"{role}{index}",
            last_name="User",
            date_of_birth=date(1990, 1, 1),
            gender="M",
            phone_number=f"0777000{index:03d}",
            email=f"{role}{index}@example.com",
            address="Kampala",
            national_id=f"NIN-{role}-{index}",
            hire_date=date(2022, 1, 1),
            role=role,
            department=department or defaults.get(role, "operations"),
            status="active",
        )

    def test_expense_reduces_budget_and_notifies_finance_at_threshold(self):
        finance = self.make_employee("finance_officer", 1)
        requester = self.make_employee("manager", 2)
        budget = Budget.objects.create(
            budget_title="Operations Fuel",
            department="operations",
            budget_category="operations",
            fiscal_year=2026,
            requested_amount=Decimal("200000.00"),
            allocated_amount=Decimal("200000.00"),
            requested_by=requester,
            verified_by=finance,
            approved_by=finance,
            verification_status="verified",
            approval_status="approved",
        )

        Expense.objects.create(
            budget=budget,
            requisition_title="Fuel for patrol vehicles",
            category="operations",
            requested_amount=Decimal("100000.00"),
            amount=Decimal("100000.00"),
            expense_date=date(2026, 7, 20),
            description="Fuel accountability with receipt.",
            spent_by=requester,
        )
        budget.refresh_from_db()

        self.assertEqual(budget.spent_amount, Decimal("100000.00"))
        self.assertEqual(budget.remaining_amount, Decimal("100000.00"))
        self.assertTrue(BudgetNotification.objects.filter(
            budget=budget,
            recipient=finance,
            recipient_group="Finance",
            notification_type="low_balance",
            status="sent",
        ).exists())

    def test_overdue_expense_accountability_notifies_hr_for_action(self):
        hr = self.make_employee("hr_officer", 3)
        approver = self.make_employee("manager", 4)
        budget = Budget.objects.create(
            budget_title="Training Materials",
            department="hr",
            budget_category="training",
            fiscal_year=2026,
            requested_amount=Decimal("500000.00"),
            allocated_amount=Decimal("500000.00"),
            verified_by=approver,
            approved_by=approver,
            verification_status="verified",
            approval_status="approved",
        )
        expense = Expense.objects.create(
            budget=budget,
            requisition_title="Training handouts",
            category="training",
            requested_amount=Decimal("250000.00"),
            amount=Decimal("0.00"),
            expense_date=date(2026, 7, 20),
            description="Materials approved for training.",
            requested_by=approver,
            verified_by=approver,
            approved_by=approver,
            verification_status="verified",
            approval_status="approved",
        )
        expense.approved_at = timezone.now() - timedelta(hours=13)
        expense.save()

        self.assertEqual(expense.accountability_status, "overdue")
        self.assertTrue(ExpenseNotification.objects.filter(
            expense=expense,
            recipient=hr,
            recipient_group="Human Resources",
            notification_type="missing_accountability",
            status="sent",
        ).exists())

    def test_budget_report_replaces_budget_alert_page(self):
        finance = self.make_employee("finance_officer", 5)
        requester = self.make_employee("manager", 6)
        budget = Budget.objects.create(
            budget_title="Operations Fuel",
            department="operations",
            budget_category="operations",
            fiscal_year=2026,
            requested_amount=Decimal("200000.00"),
            allocated_amount=Decimal("200000.00"),
            requested_by=requester,
            verified_by=finance,
            approved_by=finance,
            verification_status="verified",
            approval_status="approved",
        )
        Expense.objects.create(
            budget=budget,
            requisition_title="Fuel for patrol vehicles",
            category="operations",
            requested_amount=Decimal("100000.00"),
            amount=Decimal("100000.00"),
            expense_date=date(2026, 7, 20),
            description="Fuel accountability with receipt.",
            spent_by=requester,
        )

        login_test_staff(self.client)
        report_response = self.client.get("/budgets/report/")
        list_response = self.client.get("/budgets/")

        self.assertEqual(report_response.status_code, 200)
        self.assertContains(report_response, "Budget Report")
        self.assertContains(report_response, "Operations Fuel")
        self.assertContains(report_response, "Remaining")
        self.assertNotContains(report_response, "Budget Accountability Notifications")
        self.assertContains(list_response, "Budget Report")
        self.assertNotContains(list_response, "Budget Alerts")

    def test_budget_edit_page_renders_without_expense_budget_autofill(self):
        finance = self.make_employee("finance_officer", 13)
        budget = Budget.objects.create(
            budget_title="Operations Fuel",
            department="operations",
            budget_category="operations",
            fiscal_year=2026,
            requested_amount=Decimal("300000.00"),
            allocated_amount=Decimal("250000.00"),
            verified_by=finance,
            approved_by=finance,
            verification_status="verified",
            approval_status="approved",
        )

        login_test_staff(self.client)
        response = self.client.get(f"/budgets/{budget.pk}/edit/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edit Budgets")
        self.assertContains(response, "Operations Fuel")

    def test_expense_form_only_picks_approved_budgets_from_budget_table(self):
        finance = self.make_employee("finance_officer", 9)
        approved_budget = Budget.objects.create(
            budget_title="Approved Fuel",
            department="operations",
            budget_category="operations",
            fiscal_year=2026,
            requested_amount=Decimal("300000.00"),
            allocated_amount=Decimal("250000.00"),
            verified_by=finance,
            approved_by=finance,
            verification_status="verified",
            approval_status="approved",
        )
        pending_budget = Budget.objects.create(
            budget_title="Pending Fuel",
            department="finance",
            budget_category="operations",
            fiscal_year=2026,
            requested_amount=Decimal("300000.00"),
            allocated_amount=Decimal("0.00"),
            approval_status="pending",
        )

        form = ExpenseForm()
        budget_ids = set(form.fields["budget"].queryset.values_list("pk", flat=True))
        approved_label = form.fields["budget"].label_from_instance(approved_budget)

        self.assertIn(approved_budget.pk, budget_ids)
        self.assertNotIn(pending_budget.pk, budget_ids)
        self.assertIn("Approved Fuel", approved_label)
        self.assertIn("250,000.00 - 250,000.00", approved_label)
        self.assertNotIn("Allocated", approved_label)
        self.assertNotIn("Remaining", approved_label)

    def test_expense_form_uses_active_verifier_and_approver_dropdowns(self):
        budget_verifier = self.make_employee("finance_officer", 10)
        budget_approver = self.make_employee("manager", 11)
        selected_user = self.make_employee("manager", 12)
        budget = Budget.objects.create(
            budget_title="Approved Fuel",
            department="operations",
            budget_category="operations",
            fiscal_year=2026,
            requested_amount=Decimal("300000.00"),
            allocated_amount=Decimal("250000.00"),
            verified_by=budget_verifier,
            approved_by=budget_approver,
            verification_status="verified",
            approval_status="approved",
        )
        form = ExpenseForm(data={
            "budget": str(budget.pk),
            "requisition_title": "Fuel for patrol vehicles",
            "category": "operations",
            "requested_amount": "100000.00",
            "expense_date": "2026-07-20",
            "vendor_payee": "Fuel Station",
            "payment_method": "cash",
            "requisition_reason": "Patrol vehicle fuel.",
            "requested_by": str(selected_user.pk),
            "verified_by": str(selected_user.pk),
            "approved_by": str(selected_user.pk),
            "verification_status": "verified",
            "approval_status": "approved",
            "amount": "0.00",
            "description": "Fuel requisition.",
            "receipt_reference": "",
            "accountability_notes": "",
            "spent_by": "",
            "status": "requisition",
        })

        self.assertFalse(form.fields["verified_by"].disabled)
        self.assertFalse(form.fields["approved_by"].disabled)
        self.assertTrue(form.is_valid(), form.errors)
        expense = form.save()
        self.assertEqual(expense.verified_by, selected_user)
        self.assertEqual(expense.approved_by, selected_user)

    def test_expense_accountability_report_uses_budget_and_expense_data(self):
        finance = self.make_employee("finance_officer", 7)
        requester = self.make_employee("manager", 8)
        budget = Budget.objects.create(
            budget_title="Operations Fuel",
            department="operations",
            budget_category="operations",
            fiscal_year=2026,
            requested_amount=Decimal("300000.00"),
            allocated_amount=Decimal("300000.00"),
            requested_by=requester,
            verified_by=finance,
            approved_by=finance,
            verification_status="verified",
            approval_status="approved",
        )
        Expense.objects.create(
            budget=budget,
            requisition_title="Fuel for patrol vehicles",
            category="operations",
            requested_amount=Decimal("120000.00"),
            amount=Decimal("100000.00"),
            expense_date=date(2026, 7, 20),
            description="Fuel accountability with receipt.",
            receipt_reference="RCT-001",
            spent_by=requester,
            approval_status="approved",
            approved_by=finance,
        )

        login_test_staff(self.client)
        report_response = self.client.get("/expenses/report/")
        list_response = self.client.get("/expenses/")

        self.assertEqual(report_response.status_code, 200)
        self.assertContains(report_response, "Expense Accountability Report")
        self.assertContains(report_response, "Operations Fuel")
        self.assertContains(report_response, "Fuel for patrol vehicles")
        self.assertContains(report_response, "RCT-001")
        self.assertContains(report_response, "Budget Remaining")
        self.assertNotContains(report_response, "Expense Accountability Notifications")
        self.assertContains(list_response, "Accountability Report")
        self.assertNotContains(list_response, "Accountability Alerts")


class PublicWebsiteTests(TestCase):
    def test_public_home_replaces_root_and_dashboard_moves(self):
        WebsiteAdvertisement.objects.create(title="Recruitment Drive", message="New guard roles open.", call_to_action="Apply")
        CompanyEvent.objects.create(title="Community Safety Day", event_date=date(2026, 8, 1), location="Kampala", summary="Client and community safety briefing.")
        WebsiteResource.objects.create(title="Client Safety Guide", resource_type="Guide", summary="Preparedness notes.")
        AssociatedLink.objects.create(title="Uganda Police", category="Authority", url="https://www.upf.go.ug", description="Security authority.")
        JobPosting.objects.create(title="Security Guard", department="Operations", location="Kampala", employment_type="Full-time", summary="Guard client premises.")

        home_response = self.client.get("/")
        dashboard_response = self.client.get("/dashboard/")

        self.assertEqual(home_response.status_code, 200)
        self.assertContains(home_response, "Turyans Security Company")
        self.assertNotContains(home_response, "Recruitment Drive")
        self.assertNotContains(home_response, "New guard roles open")
        self.assertContains(home_response, "Community Safety Day")
        self.assertContains(home_response, "Security Guard")
        self.assertEqual(dashboard_response.status_code, 302)
        self.assertIn("/staff/login/", dashboard_response.url)

        login_test_staff(self.client)
        dashboard_response = self.client.get("/dashboard/")
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, "Dashboard")

    def test_careers_accepts_online_application(self):
        job = JobPosting.objects.create(
            title="Security Guard",
            department="Operations",
            location="Kampala",
            employment_type="Full-time",
            summary="Guard client premises.",
            requirements="National ID and security discipline.",
        )

        response = self.client.post(f"/careers/{job.pk}/", data={
            "applicant_name": "Jane Applicant",
            "phone_number": "0700000001",
            "email": "jane@example.com",
            "address": "Kampala",
            "qualification": "S.4",
            "experience_summary": "Two years guarding experience.",
            "cover_note": "Ready to work.",
        })

        self.assertEqual(response.status_code, 302)
        application = JobApplication.objects.get()
        self.assertEqual(application.job, job)
        self.assertEqual(application.application_mode, "online")
        self.assertEqual(application.applicant_name, "Jane Applicant")

    def test_physical_application_can_be_recorded_from_dashboard_form(self):
        receiver = Employee.objects.create(
            first_name="HR",
            last_name="Receiver",
            date_of_birth=date(1990, 1, 1),
            gender="F",
            phone_number="0700000002",
            email="hr@example.com",
            address="Kampala",
            national_id="NIN-HR-001",
            hire_date=date(2024, 1, 1),
            role="hr_officer",
            department="hr",
            status="active",
        )
        job = JobPosting.objects.create(title="Supervisor", department="Operations", location="Mukono", employment_type="Full-time", summary="Supervise guards.")

        login_test_staff(self.client)
        response = self.client.post("/job-applications/add/", data={
            "records-TOTAL_FORMS": "1",
            "records-INITIAL_FORMS": "0",
            "records-MIN_NUM_FORMS": "0",
            "records-MAX_NUM_FORMS": "1000",
            "records-0-job": str(job.pk),
            "records-0-application_mode": "physical",
            "records-0-applicant_name": "Physical Applicant",
            "records-0-phone_number": "0700000003",
            "records-0-email": "",
            "records-0-address": "Kampala",
            "records-0-qualification": "S.6",
            "records-0-experience_summary": "Delivered documents to office.",
            "records-0-cover_note": "Physical application received.",
            "records-0-received_by": str(receiver.pk),
            "records-0-status": "received",
        })

        self.assertEqual(response.status_code, 302)
        application = JobApplication.objects.get()
        self.assertEqual(application.application_mode, "physical")
        self.assertEqual(application.received_by, receiver)



class RoleAccessControlTests(TestCase):
    def make_role_user(self, username, group_name, superuser=False):
        user = get_user_model().objects.create_user(username=username, password="test-pass", is_staff=True, is_superuser=superuser)
        group, _created = Group.objects.get_or_create(name=group_name)
        user.groups.add(group)
        self.client.force_login(user)
        return user

    def test_supervisor_can_only_open_attendance(self):
        self.make_role_user("supervisor-role", "Supervisor")

        attendance_response = self.client.get("/attendance/")
        employees_response = self.client.get("/employees/")

        self.assertEqual(attendance_response.status_code, 200)
        self.assertEqual(employees_response.status_code, 403)
        self.assertContains(attendance_response, "Attendance", status_code=200)
        self.assertNotContains(attendance_response, "Employees")

    def test_hr_can_open_employees_and_payroll_but_not_finance_payments(self):
        self.make_role_user("hr-role", "Human Resources")

        self.assertEqual(self.client.get("/employees/").status_code, 200)
        self.assertEqual(self.client.get("/payroll/").status_code, 200)
        self.assertEqual(self.client.get("/payments/").status_code, 403)

    def test_finance_officer_can_open_finance_but_not_employees(self):
        self.make_role_user("finance-role", "Finance Officer")

        self.assertEqual(self.client.get("/payments/").status_code, 200)
        self.assertEqual(self.client.get("/employees/").status_code, 403)

    def test_head_of_finance_has_full_access(self):
        self.make_role_user("head-finance-role", "Head of Finance", superuser=True)

        self.assertEqual(self.client.get("/employees/").status_code, 200)
        self.assertEqual(self.client.get("/payments/").status_code, 200)

class AccessControlAndAuditTests(TestCase):
    def test_non_staff_user_cannot_open_staff_dashboard(self):
        user = get_user_model().objects.create_user(username="regular", password="test-pass")
        self.client.force_login(user)

        response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/staff/login/", response.url)

    def test_staff_write_request_is_audited(self):
        user = get_user_model().objects.create_user(username="auditor", password="test-pass", is_staff=True)
        request = RequestFactory().post("/clients/add/", HTTP_USER_AGENT="unit-test", REMOTE_ADDR="127.0.0.1")
        request.user = user
        middleware = RequestAuditMiddleware(lambda request: HttpResponse(status=302))

        response = middleware(request)

        self.assertEqual(response.status_code, 302)
        audit_log = AuditLog.objects.get()
        self.assertEqual(audit_log.user, user)
        self.assertEqual(audit_log.username, "auditor")
        self.assertEqual(audit_log.action, "create")
        self.assertEqual(audit_log.path, "/clients/add/")
        self.assertEqual(audit_log.method, "POST")
        self.assertEqual(audit_log.status_code, 302)

    def test_audit_logs_are_immutable(self):
        user = get_user_model().objects.create_user(username="immutable", password="test-pass", is_staff=True)
        audit_log = AuditLog.objects.create(
            user=user,
            username="immutable",
            action="create",
            path="/clients/add/",
            method="POST",
            status_code=302,
        )
        audit_log.status_code = 200

        with self.assertRaisesMessage(Exception, "Audit log records are immutable"):
            audit_log.save()

class PayrollProcessingTests(TestCase):
    def make_employee(self, first_name, role):
        return Employee.objects.create(
            first_name=first_name,
            last_name="Payroll",
            date_of_birth=date(1990, 1, 1),
            gender="M",
            phone_number=f"0799{Employee.objects.count():06d}",
            email=f"{first_name.lower()}@example.com",
            address="Kampala",
            national_id=f"NIN-PAY-{first_name}",
            hire_date=date(2024, 1, 1),
            role=role,
            status="active",
        )

    def test_supervisor_salary_is_fixed_not_attendance_based(self):
        supervisor = self.make_employee("SupervisorFixed", "supervisor")
        salary = supervisor.salary
        salary.update_basic_salary()

        self.assertTrue(supervisor.uses_fixed_monthly_salary)
        self.assertEqual(supervisor.fixed_monthly_salary, Decimal("780000.00"))
        self.assertEqual(salary.basic_salary, Decimal("780000.00"))
        self.assertEqual(salary.overtime_pay, Decimal("0.00"))

    def test_administrator_salary_is_fixed_not_attendance_based(self):
        administrator = self.make_employee("AdminFixed", "administrator")
        salary = administrator.salary
        salary.update_basic_salary()

        self.assertTrue(administrator.uses_fixed_monthly_salary)
        self.assertEqual(administrator.department, "admin")
        self.assertEqual(salary.basic_salary, Decimal("780000.00"))

    def test_loan_medical_and_salary_advance_reduce_net_pay(self):
        employee = self.make_employee("DeductionStaff", "administrator")
        salary = employee.salary
        PayrollDeduction.objects.create(employee=employee, category="loan", description="Staff loan", amount=Decimal("50000.00"), start_date=timezone.localdate())
        PayrollDeduction.objects.create(employee=employee, category="medical", description="Medical bill", amount=Decimal("25000.00"), start_date=timezone.localdate())
        Advance.objects.create(
            employee=employee,
            amount_requested=Decimal("100000.00"),
            approval_status="approved",
            disbursement_date=timezone.localdate(),
            status="disbursed",
        )
        salary.update_basic_salary()
        salary.save(update_fields=["basic_salary", "overtime_pay", "updated_at"])
        salary.recover_advances()

        self.assertEqual(salary.loan_deduction, Decimal("50000.00"))
        self.assertEqual(salary.medical_deduction, Decimal("25000.00"))
        self.assertEqual(salary.advance_recovery, Decimal("100000.00"))
        self.assertEqual(salary.total_deductions, salary.deductions + salary.staff_deductions + salary.advance_recovery + salary.nssf_employee + salary.paye)
        self.assertEqual(salary.net_pay, salary.gross_pay - salary.total_deductions)













class PurchaseOrderLpoReportTests(TestCase):
    def test_lpo_report_renders_supplier_purchase_order(self):
        preparer = Employee.objects.create(
            first_name="Procurement",
            last_name="Officer",
            date_of_birth=date(1990, 1, 1),
            gender="F",
            phone_number="0788000001",
            email="procurement@example.com",
            address="Kampala",
            national_id="NIN-LPO-PREP",
            hire_date=date(2024, 1, 1),
            role="finance_officer",
            department="finance",
            status="active",
        )
        supplier = Supplier.objects.create(
            supplier_name="Office Supplies Ltd",
            contact_person="Jane Supplier",
            phone_number="0700000001",
            email="supplier@example.com",
            address="Kampala Road",
            tax_identification_number="TIN123",
        )
        requisition = ProcurementRequisition.objects.create(
            title="Office stationery",
            category="admin",
            description="Procure stationery for operations office",
            requested_by=preparer,
            approval_assigned_to=preparer,
            preferred_supplier=supplier,
            department="admin",
            required_date=date(2026, 8, 15),
            estimated_amount=Decimal("150000.00"),
            status="approved",
        )
        purchase_order = PurchaseOrder.objects.create(
            requisition=requisition,
            supplier=supplier,
            order_date=date(2026, 7, 29),
            expected_delivery_date=date(2026, 8, 15),
            subtotal_amount=Decimal("100000.00"),
            tax_amount=Decimal("18000.00"),
            payment_terms="net_15",
            status="issued",
            prepared_by=preparer,
            notes="Deliver to head office.",
        )

        login_test_staff(self.client)
        response = self.client.get(f"/purchase-orders/{purchase_order.pk}/lpo/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "LOCAL PURCHASE ORDER")
        self.assertContains(response, purchase_order.po_number)
        self.assertContains(response, "Office Supplies Ltd")
        self.assertContains(response, "Print LPO")

class ProcurementApiTests(TestCase):
    def setUp(self):
        self.preparer = Employee.objects.create(
            first_name="Api",
            last_name="Officer",
            date_of_birth=date(1990, 1, 1),
            gender="F",
            phone_number="0788000099",
            email="api-officer@example.com",
            address="Kampala",
            national_id="NIN-API-PREP",
            hire_date=date(2024, 1, 1),
            role="finance_officer",
            department="finance",
            status="active",
        )
        self.supplier = Supplier.objects.create(
            supplier_name="Api Supplies Ltd",
            contact_person="Api Contact",
            phone_number="0700000099",
            email="api-supplier@example.com",
            address="Industrial Area",
            tax_identification_number="TIN-API",
        )
        self.requisition = ProcurementRequisition.objects.create(
            title="API stationery",
            category="admin",
            description="Procure stationery through API test",
            requested_by=self.preparer,
            approval_assigned_to=self.preparer,
            preferred_supplier=self.supplier,
            department="admin",
            required_date=date(2026, 8, 20),
            estimated_amount=Decimal("200000.00"),
            status="approved",
        )
        self.purchase_order = PurchaseOrder.objects.create(
            requisition=self.requisition,
            supplier=self.supplier,
            order_date=date(2026, 7, 29),
            expected_delivery_date=date(2026, 8, 20),
            subtotal_amount=Decimal("120000.00"),
            tax_amount=Decimal("21600.00"),
            payment_terms="net_15",
            status="issued",
            prepared_by=self.preparer,
        )
        login_test_staff(self.client)

    def test_procurement_api_index_returns_modules(self):
        response = self.client.get("/api/procurement/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["api"], "procurement")
        self.assertEqual(payload["version"], "1.1")
        self.assertIn("purchase-orders", {module["name"] for module in payload["modules"]})

    def test_procurement_api_list_supports_search_fields_and_pagination(self):
        response = self.client.get("/api/procurement/suppliers/?q=Api&page_size=1&fields=supplier_name,status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["pagination"]["page_size"], 1)
        self.assertEqual(payload["pagination"]["count"], 1)
        self.assertEqual(payload["results"][0]["attributes"]["supplier_name"], "Api Supplies Ltd")
        self.assertIn("status", payload["results"][0]["attributes"])

    def test_purchase_order_api_detail_includes_lpo_report_link(self):
        response = self.client.get(f"/api/procurement/purchase-orders/{self.purchase_order.pk}/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["result"]["id"], self.purchase_order.pk)
        self.assertIn("lpo_report", payload["result"]["links"])
        self.assertIn(f"/purchase-orders/{self.purchase_order.pk}/lpo/", payload["result"]["links"]["lpo_report"])

class FinancePayoutApiTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(
            first_name="Payout",
            last_name="Officer",
            date_of_birth=date(1990, 1, 1),
            gender="F",
            phone_number="0777000001",
            email="payout@example.com",
            address="Kampala",
            national_id="NIN-PAYOUT-EMP",
            hire_date=date(2024, 1, 1),
            role="finance_officer",
            department="finance",
            status="active",
            payout_method="mobile_money",
            mobile_money_provider="MTN Mobile Money",
            mobile_money_number="0777000001",
        )
        self.salary = self.employee.salary
        self.salary.allowances = Decimal("50000.00")
        self.salary.save()
        login_test_staff(self.client)

    def test_payout_api_index_lists_batches(self):
        response = self.client.get("/api/payouts/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["api"], "finance-payouts")
        self.assertIn("salaries", {batch["name"] for batch in payload["batches"]})
        self.assertIn("client-payments", {batch["name"] for batch in payload["batches"]})

    def test_salary_payout_api_returns_mobile_money_ready_record(self):
        response = self.client.get("/api/payouts/salaries/?method=mobile_money&ready=true")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreaterEqual(payload["pagination"]["count"], 1)
        record = next(item for item in payload["results"] if item["id"] == self.salary.pk)
        self.assertEqual(record["channel"], "mobile_money")
        self.assertTrue(record["ready_for_export"])
        self.assertEqual(record["mobile_money"]["provider"], "MTN Mobile Money")
        self.assertEqual(record["mobile_money"]["number"], "0777000001")

    def test_client_payment_api_returns_inbound_bank_reference(self):
        client = Client.objects.create(
            client_name="Bank Client Ltd",
            contact_person="Client Contact",
            phone_number="0312000000",
            email="client@example.com",
            address="Kampala",
        )
        invoice = Invoice.objects.create(
            client=client,
            invoice_date=date(2026, 7, 1),
            due_date=date(2026, 7, 31),
            total_amount=Decimal("250000.00"),
            status="sent",
        )
        payment = Payment.objects.create(
            invoice=invoice,
            payment_date=date(2026, 7, 29),
            amount=Decimal("250000.00"),
            payment_method="bank_transfer",
            transaction_ref="BANK-REF-001",
        )

        response = self.client.get("/api/payouts/client-payments/?method=bank_transfer&ready=true")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        record = next(item for item in payload["results"] if item["id"] == payment.pk)
        self.assertEqual(record["direction"], "inbound")
        self.assertEqual(record["reference"], "BANK-REF-001")
        self.assertTrue(record["ready_for_export"])

class ProcurementDashboardTests(TestCase):
    def test_procurement_dashboard_uses_common_workflow(self):
        login_test_staff(self.client)

        response = self.client.get("/procurement/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "New Request")
        self.assertContains(response, "Record Quote")
        self.assertContains(response, "Create LPO")
        self.assertContains(response, "To Approve")
        self.assertContains(response, "Registers")

class ProcurementApprovalPrefillTests(TestCase):
    def test_dashboard_approve_prefills_pending_requisition(self):
        approver = Employee.objects.create(
            first_name="Approval",
            last_name="Officer",
            date_of_birth=date(1990, 1, 1),
            gender="F",
            phone_number="0788555001",
            email="approval@example.com",
            address="Kampala",
            national_id="NIN-APPROVAL-PREFILL",
            hire_date=date(2024, 1, 1),
            role="finance_officer",
            department="finance",
            status="active",
        )
        supplier = Supplier.objects.create(supplier_name="Prefill Supplier", phone_number="0700555001")
        requisition = ProcurementRequisition.objects.create(
            title="Prefill uniforms",
            category="admin",
            description="Procure uniforms for deployment staff",
            requested_by=approver,
            approval_assigned_to=approver,
            preferred_supplier=supplier,
            department="admin",
            required_date=date(2026, 8, 20),
            estimated_amount=Decimal("300000.00"),
            status="submitted",
        )
        login_test_staff(self.client)

        dashboard_response = self.client.get("/procurement/")
        approval_response = self.client.get(f"/procurement-approvals/add/?requisition={requisition.pk}")

        self.assertContains(dashboard_response, f"/procurement-approvals/add/?requisition={requisition.pk}")
        self.assertContains(approval_response, "Prefill uniforms")
        self.assertContains(approval_response, "300,000.00")
        self.assertContains(approval_response, f'value="{requisition.pk}" selected')





class SupplierPaymentApprovedAmountTests(TestCase):
    def make_procurement_chain(self):
        approver = Employee.objects.create(
            first_name="Pay",
            last_name="Approver",
            date_of_birth=date(1990, 1, 1),
            gender="F",
            phone_number="0788666001",
            email="pay-approver@example.com",
            address="Kampala",
            national_id="NIN-PAY-APPROVER",
            hire_date=date(2024, 1, 1),
            role="finance_officer",
            department="finance",
            status="active",
        )
        supplier = Supplier.objects.create(supplier_name="Approved Amount Supplier", phone_number="0700666001")
        requisition = ProcurementRequisition.objects.create(
            title="Approved amount purchase",
            category="admin",
            description="Procurement approved below invoice amount",
            requested_by=approver,
            approval_assigned_to=approver,
            preferred_supplier=supplier,
            department="admin",
            required_date=date(2026, 8, 20),
            estimated_amount=Decimal("500000.00"),
            status="submitted",
        )
        ProcurementApproval.objects.create(
            requisition=requisition,
            approved_by=approver,
            decision="approved",
            approved_amount=Decimal("300000.00"),
            comments="Approve partial amount.",
        )
        purchase_order = PurchaseOrder.objects.create(
            requisition=requisition,
            supplier=supplier,
            order_date=date(2026, 7, 29),
            expected_delivery_date=date(2026, 8, 20),
            subtotal_amount=Decimal("500000.00"),
            tax_amount=Decimal("0.00"),
            payment_terms="net_15",
            status="issued",
            prepared_by=approver,
        )
        grn = GoodsReceivedNote.objects.create(
            purchase_order=purchase_order,
            received_date=date(2026, 8, 1),
            received_by=approver,
            quantity_summary="All items received",
            status="accepted",
        )
        return approver, supplier, requisition, purchase_order, grn

    def test_approved_supplier_invoice_creates_payment_for_requisition_approved_amount(self):
        approver, supplier, _requisition, purchase_order, grn = self.make_procurement_chain()

        invoice = SupplierInvoice.objects.create(
            invoice_number="SUP-APPROVED-001",
            purchase_order=purchase_order,
            goods_received_note=grn,
            supplier=supplier,
            invoice_date=date(2026, 8, 2),
            due_date=date(2026, 8, 20),
            subtotal_amount=Decimal("500000.00"),
            tax_amount=Decimal("0.00"),
            status="approved",
            approved_by=approver,
        )

        payment = invoice.payments.get()
        self.assertEqual(invoice.approved_payable_amount, Decimal("300000.00"))
        self.assertEqual(payment.amount, Decimal("300000.00"))

    def test_pending_payment_syncs_to_requisition_approved_amount_when_paid(self):
        approver, supplier, _requisition, purchase_order, grn = self.make_procurement_chain()
        invoice = SupplierInvoice.objects.create(
            invoice_number="SUP-APPROVED-002",
            purchase_order=purchase_order,
            goods_received_note=grn,
            supplier=supplier,
            invoice_date=date(2026, 8, 2),
            due_date=date(2026, 8, 20),
            subtotal_amount=Decimal("500000.00"),
            tax_amount=Decimal("0.00"),
            status="approved",
            approved_by=approver,
        )
        payment = invoice.payments.get()
        SupplierPayment.objects.filter(pk=payment.pk).update(amount=Decimal("500000.00"))
        payment.refresh_from_db()
        payment.approval_status = "approved"
        payment.approved_by = approver
        payment.paid_by = approver
        payment.save()

        payment.refresh_from_db()
        self.assertEqual(payment.amount, Decimal("300000.00"))
        self.assertEqual(payment.payment_status, "paid")


class SupplierProformaValidationTests(TestCase):
    def test_over_approved_amount_returns_form_error_without_crashing(self):
        approver = Employee.objects.create(
            first_name="Proforma",
            last_name="Approver",
            date_of_birth=date(1990, 1, 1),
            gender="F",
            phone_number="0788777001",
            email="proforma-approver@example.com",
            address="Kampala",
            national_id="NIN-PROFORMA-APPROVER",
            hire_date=date(2024, 1, 1),
            role="finance_officer",
            department="finance",
            status="active",
        )
        supplier = Supplier.objects.create(supplier_name="Over Limit Supplier", phone_number="0700777001")
        requisition = ProcurementRequisition.objects.create(
            title="Limited purchase",
            category="admin",
            description="Supplier quote should not exceed approved amount",
            requested_by=approver,
            approval_assigned_to=approver,
            preferred_supplier=supplier,
            department="admin",
            required_date=date(2026, 8, 20),
            estimated_amount=Decimal("500000.00"),
            status="submitted",
        )
        ProcurementApproval.objects.create(
            requisition=requisition,
            approved_by=approver,
            decision="approved",
            approved_amount=Decimal("300000.00"),
        )
        requisition.mark_supplier_contacted()
        item = SupplierProformaItemPrice.objects.create(
            item_name="Uniform Set",
            unit_price=Decimal("120000.00"),
            tax_rate=Decimal("18.00"),
            discount_allowed=False,
            active=True,
        )
        login_test_staff(self.client)

        response = self.client.post("/supplier-proformas/add/?popup=1", {
            "requisition": str(requisition.pk),
            "supplier": str(supplier.pk),
            "proforma_date": "2026-07-29",
            "valid_until": "2026-07-31",
            "supplier_reference": "",
            "status": "accepted",
            "proforma_items-TOTAL_FORMS": "1",
            "proforma_items-INITIAL_FORMS": "0",
            "proforma_items-MIN_NUM_FORMS": "0",
            "proforma_items-MAX_NUM_FORMS": "1000",
            "proforma_items-0-item_id": "",
            "proforma_items-0-proforma": "",
            "proforma_items-0-catalog_item": str(item.pk),
            "proforma_items-0-quantity": "4",
            "proforma_items-0-unit_price": "120000.00",
            "proforma_items-0-discount_amount": "0.00",
            "proforma_items-0-tax_rate": "18.00",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "exceeds the approved requisition amount")
        self.assertEqual(SupplierProformaInvoice.objects.count(), 0)
        self.assertEqual(PurchaseOrder.objects.count(), 0)


class PasswordManagementTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="system-admin",
            email="admin@example.com",
            password="Admin-Password-2026!",
        )
        self.staff_user = User.objects.create_user(
            username="staff-user",
            email="staff@example.com",
            password="Old-Password-2026!",
            is_staff=True,
            is_active=False,
        )

    def test_superuser_can_open_password_management(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("webcom:password_management"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Password Management")
        self.assertContains(response, "staff-user")

    def test_superuser_can_activate_and_reset_password(self):
        self.client.force_login(self.admin)

        activate_response = self.client.post(
            reverse("webcom:password_management_action", args=[self.staff_user.pk]),
            {"action": "activate"},
        )
        self.assertRedirects(activate_response, reverse("webcom:password_management"))
        self.staff_user.refresh_from_db()
        self.assertTrue(self.staff_user.is_active)

        reset_response = self.client.post(
            reverse("webcom:password_management_action", args=[self.staff_user.pk]),
            {
                "action": "reset_password",
                "new_password": "New-Password-2026!",
                "confirm_password": "New-Password-2026!",
            },
        )
        self.assertRedirects(reset_response, reverse("webcom:password_management"))
        self.staff_user.refresh_from_db()
        self.assertTrue(self.staff_user.check_password("New-Password-2026!"))

    def test_bulk_deactivates_users_linked_to_terminated_staff(self):
        self.staff_user.is_active = True
        self.staff_user.save(update_fields=["is_active"])
        Employee.objects.create(
            first_name="Terminated",
            last_name="Staff",
            date_of_birth=date(1990, 1, 1),
            gender="M",
            phone_number="0700000000",
            email="staff@example.com",
            address="Kampala",
            national_id="NAT-TERM-001",
            role="guard",
            position="security_guard",
            department="operations",
            hire_date=date(2020, 1, 1),
            status="terminated",
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("webcom:password_management"),
            {"action": "deactivate_terminated"},
        )

        self.assertRedirects(response, reverse("webcom:password_management"))
        self.staff_user.refresh_from_db()
        self.assertFalse(self.staff_user.is_active)

    def test_separated_employee_save_deactivates_linked_user(self):
        self.staff_user.is_active = True
        self.staff_user.save(update_fields=["is_active"])
        employee = Employee.objects.create(
            first_name="Separated",
            last_name="Staff",
            date_of_birth=date(1990, 1, 1),
            gender="M",
            phone_number="0700000001",
            email="staff@example.com",
            address="Kampala",
            national_id="NAT-TERM-002",
            role="hr_officer",
            position="hr_officer",
            department="hr",
            hire_date=date(2020, 1, 1),
            status="active",
        )

        employee.status = "resigned"
        employee.save(update_fields=["status"])

        self.staff_user.refresh_from_db()
        self.assertFalse(self.staff_user.is_active)

    def test_active_employee_save_creates_grouped_user_with_default_password(self):
        employee = Employee.objects.create(
            first_name="Finance",
            last_name="Access",
            date_of_birth=date(1990, 1, 1),
            gender="F",
            phone_number="0700000002",
            email="finance.access@example.com",
            address="Kampala",
            national_id="NAT-ACTIVE-001",
            role="finance_officer",
            position="finance_officer",
            department="finance",
            hire_date=date(2020, 1, 1),
            status="active",
        )

        user = get_user_model().objects.get(username=employee.employee_number)
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertEqual(user.email, employee.email)
        self.assertTrue(user.check_password("ChangeMe123!"))
        self.assertTrue(user.groups.filter(name="Finance Officer").exists())

    def test_guard_employee_does_not_receive_unrestricted_staff_login(self):
        employee = Employee.objects.create(
            first_name="Guard",
            last_name="NoLogin",
            date_of_birth=date(1990, 1, 1),
            gender="M",
            phone_number="0700000003",
            email="guard.nologin@example.com",
            address="Kampala",
            national_id="NAT-GUARD-NOLOGIN",
            role="guard",
            position="security_guard",
            department="operations",
            hire_date=date(2020, 1, 1),
            status="active",
        )

        self.assertFalse(get_user_model().objects.filter(username=employee.employee_number).exists())
