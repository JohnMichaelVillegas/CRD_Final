import os
from datetime import timedelta
from io import BytesIO
from openpyxl import load_workbook
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
import requests
import secrets
from django.contrib.auth.hashers import make_password, check_password

from .models import (
    Student, ArchivedStudent, Offense, CaseRecord, ArchivedRecord,
    AuditLog, AccumulationRule, UserProfile, SystemSettings,
    CaseSanction, CaseDiaryEntry, CaseAttachment,
)

def validate_case_uploads(uploaded_files):
    max_size = 4 * 1024 * 1024

    for uploaded_file in uploaded_files:

        if uploaded_file.size > max_size:
            return (
                f"{uploaded_file.name} exceeds the 4 MB limit. "
                "Please upload a smaller PDF file."
            )

        if not uploaded_file.name.lower().endswith(".pdf"):
            return (
                "Invalid file format. Only PDF files are allowed. "
                "Please upload a PDF file."
            )

        uploaded_file.seek(0)
        file_header = uploaded_file.read(1024)
        uploaded_file.seek(0)

        if b"%PDF-" not in file_header:
            return (
                "Invalid file. Please upload a valid PDF document."
            )

    return None

def is_main_admin(user):
    return user.is_authenticated and user.is_active and user.is_superuser


def staff_has_permission(user, permission):
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    try:
        profile = user.userprofile
    except UserProfile.DoesNotExist:
        return False
    if profile.role != "Staff":
        return False
    permissions = profile.permissions or []
    if permission == "case_view" and "case_record" in permissions:
        return True
    return permission in permissions


def staff_has_any_case_permission(user):
    return any(staff_has_permission(user, p) for p in (
        "case_view",
        "case_full",
        "case_create",
    ))


def account_is_inactive(request):
    if request.user.is_authenticated and not request.user.is_active:
        logout(request)
        messages.error(
            request,
            "Your account is inactive. Please contact an administrator."
        )
        return True
    return False


def get_profile(user):
    try:
        return user.userprofile
    except UserProfile.DoesNotExist:
        return None

def create_case_diary_entry(case, user, note):
    CaseDiaryEntry.objects.create(
        case=case,
        entry_date=timezone.now(),
        note=note,
        created_by=user,
    )

# =========================================================
# CASE NUMBER SORTING
# =========================================================

def case_number_sort_key(case):
    try:
        parts = case.case_number.split("-")

        if len(parts) == 3:
            school_year = int(parts[0])
            term = int(parts[1])
            sequence = int(parts[2][:-1])

            return (school_year, sequence, term)

        # Keep older case numbers sortable.
        return (0, 0, int(parts[0].replace("CASE", "")))

    except (ValueError, IndexError):
        return (0, 0, 0)

def generate_case_number(school_year, term, campus):
    """
    Generate a case number in the format:
    YYZZ-TERM-SEQ-CAMPUS

    The sequence continues across all school years,
    terms, and campuses.
    """

    try:
        years = school_year.strip().split("-")

        if len(years) != 2:
            raise ValueError

        start_year = int(years[0])
        end_year = int(years[1])

        if end_year != start_year + 1:
            raise ValueError

        school_year_code = (
            f"{start_year % 100:02d}"
            f"{end_year % 100:02d}"
        )

    except (ValueError, AttributeError):
        raise ValueError(
            "Enter a valid school year, such as 2026-2027."
        )

    term_codes = {
        "1st": "1",
        "2nd": "2",
        "3rd": "3",
        "1": "1",
        "2": "2",
        "3": "3",
    }

    term_code = term_codes.get(term.strip())

    if not term_code:
        raise ValueError("Select a valid term.")

    campus_code = campus.strip().upper()
    campus_codes = {"A", "I", "R", "B", "D", "S", "M"}

    if campus_code not in campus_codes:
        raise ValueError("Select a valid incident campus.")

    # Find the highest sequence used by existing case numbers.
    existing_numbers = CaseRecord.objects.values_list(
        "case_number",
        flat=True
    )

    highest_sequence = 0

    for number in existing_numbers:
        parts = number.split("-")

        if len(parts) != 3:
            continue

        try:
            sequence_text = parts[2][:-1]
            sequence = int(sequence_text)
            highest_sequence = max(highest_sequence, sequence)
        except (ValueError, IndexError):
            continue

    next_sequence = highest_sequence + 1

    if next_sequence > 999:
        raise ValueError("The case-number sequence has exceeded 999.")

    return (
        f"{school_year_code}-"
        f"{term_code}-"
        f"{next_sequence:03d}"
        f"{campus_code}"
    )


# =========================================================
# LOGIN
# =========================================================

def login_view(request):

    if request.method == "POST":

        username = request.POST.get("username")
        password = request.POST.get("password")

        User = get_user_model()

        # Check if the username exists
        try:

            user_account = User.objects.get(
                username=username
            )

        except User.DoesNotExist:

            user_account = None

        # Reject disabled accounts even if their username/password are correct.
        if user_account is not None and not user_account.is_active:
            return render(
                request,
                "core/index.html",
                {
                    "error": "Your account is inactive. Please contact an administrator.",
                    "settings": SystemSettings.objects.get_or_create(id=1)[0],
                },
            )

        # Check if the Staff account is already locked
        if user_account is not None:

            if (
                not user_account.is_superuser
                and hasattr(user_account, "userprofile")
                and user_account.userprofile.role == "Staff"
            ):

                profile = user_account.userprofile

                if profile.locked:

                    return render(
                        request,
                        "core/index.html",
                        {
                            "error": (
                                "Your account has been locked after 3 failed "
                                "login attempts. Please contact an administrator "
                                "to unlock your account."
                            ),
                            "settings": SystemSettings.objects.get_or_create(
                                id=1
                            )[0],
                        }
                    )

        # Check username and password
        user = authenticate(
            request,
            username=username,
            password=password
        )

        # Correct username and password
        if user is not None:

            # Reset failed attempts after successful login
            if hasattr(user, "userprofile"):

                profile = user.userprofile

                profile.failed_login_attempts = 0
                profile.save()

            login(request, user)

            # Administrator
            if user.is_superuser:

                return redirect("admin_dashboard")

            # Staff
            if (
                hasattr(user, "userprofile")
                and user.userprofile.role == "Staff"
            ):

                return redirect("staff_dashboard")

            return redirect("login")

        # Wrong username or password
        else:

            if user_account is not None:

                # Only Staff accounts use the 3-attempt lock system
                if (
                    not user_account.is_superuser
                    and hasattr(user_account, "userprofile")
                    and user_account.userprofile.role == "Staff"
                ):

                    profile = user_account.userprofile

                    profile.failed_login_attempts += 1

                    # Lock after 3 failed attempts
                    if profile.failed_login_attempts >= 3:

                        profile.failed_login_attempts = 3
                        profile.locked = True
                        profile.save()

                        return render(
                            request,
                            "core/index.html",
                            {
                                "error": (
                                    "Your account has been locked after 3 "
                                    "failed login attempts. Please contact "
                                    "an administrator to unlock your account."
                                ),
                                "settings": SystemSettings.objects.get_or_create(
                                    id=1
                                )[0],
                            }
                        )

                    profile.save()

                    attempts_left = (
                        3 - profile.failed_login_attempts
                    )

                    return render(
                        request,
                        "core/index.html",
                        {
                            "error": (
                                f"Invalid username or password. "
                                f"You have {attempts_left} login attempts remaining."
                            ),
                            "settings": SystemSettings.objects.get_or_create(
                                id=1
                            )[0],
                        }
                    )

            return render(
                request,
                "core/index.html",
                {
                    "error": "Invalid username or password.",
                    "settings": SystemSettings.objects.get_or_create(
                        id=1
                    )[0],
                }
            )

    return render(
        request,
        "core/index.html",
        {
            "settings": SystemSettings.objects.get_or_create(
                id=1
            )[0],
        }
    )

# =========================================================
# ACCOUNT ACTIVATION
# =========================================================

def activate_account(request):

    if request.method == "POST":

        username = request.POST.get("username", "").strip()
        activation_code = request.POST.get("activation_code", "").strip()
        password = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")

        User = get_user_model()

        try:
            user = User.objects.get(username=username)
            profile = user.userprofile

        except (User.DoesNotExist, UserProfile.DoesNotExist):
            user = None
            profile = None

        if (
            user is None
            or profile is None
            or user.is_active
            or profile.is_activated
            or not profile.activation_code_hash
            or not check_password(
                activation_code,
                profile.activation_code_hash
            )
        ):
            return render(
                request,
                "core/activate_account.html",
                {
                    "error": "Invalid username or activation code.",
                    "settings": SystemSettings.objects.get_or_create(id=1)[0],
                }
            )

        if not activation_code.isdigit() or len(activation_code) != 5:
            return render(
                request,
                "core/activate_account.html",
                {
                    "error": "The activation code must contain exactly 5 digits.",
                    "settings": SystemSettings.objects.get_or_create(id=1)[0],
                }
            )

        if len(password) < 8:
            return render(
                request,
                "core/activate_account.html",
                {
                    "error": "Your password must contain at least 8 characters.",
                    "settings": SystemSettings.objects.get_or_create(id=1)[0],
                }
            )

        if password != confirm_password:
            return render(
                request,
                "core/activate_account.html",
                {
                    "error": "The passwords do not match.",
                    "settings": SystemSettings.objects.get_or_create(id=1)[0],
                }
            )

        user.set_password(password)
        user.is_active = True
        user.save(update_fields=["password", "is_active"])

        profile.is_activated = True
        profile.activation_code_hash = ""
        profile.save(
            update_fields=[
                "is_activated",
                "activation_code_hash"
            ]
        )

        messages.success(
            request,
            "Your account has been activated. You can now log in."
        )

        return redirect("login")

    return render(
        request,
        "core/activate_account.html",
        {
            "settings": SystemSettings.objects.get_or_create(id=1)[0],
        }
    )

# =========================================================
# ADMIN DASHBOARD
# =========================================================

@login_required
def admin_dashboard(request):
    if account_is_inactive(request):
        return redirect("login")
    if not request.user.is_superuser:
        return redirect("login")

    # Only count active, non-archived case records.
    active_cases = CaseRecord.objects.filter(
        is_archived=False
    )

    dashboard_cases = active_cases.select_related(
        "student",
        "offense"
    ).order_by("-date_reported")

    # Get unique school years and sort newest to oldest.
    school_years = set(
        active_cases.exclude(
            school_year__isnull=True
        ).exclude(
            school_year=""
        ).values_list(
            "school_year",
            flat=True
        )
    )

    def school_year_sort_key(year):
        try:
            start_year = int(year.split("-")[0].strip())
            return (1, start_year)
        except (ValueError, IndexError, AttributeError):
            return (0, 0)

    sorted_school_years = sorted(
        school_years,
        key=school_year_sort_key,
        reverse=True
    )

    # Keep N/A at the bottom.
    sorted_school_years = [
        year for year in sorted_school_years
        if year.strip().upper() != "N/A"
    ]

    if any(year.strip().upper() == "N/A" for year in school_years):
        sorted_school_years.append("N/A")

    # Count unique students with active case records.
    students_with_records = active_cases.values(
        "student_id"
    ).distinct().count()

    # Count resolved and open incidents.
    # This includes all resolution authorities.
    resolved_incidents = active_cases.filter(
        status__startswith="Resolved"
    ).count()

    open_incidents = active_cases.exclude(
        status__startswith="Resolved"
    ).count()

    # Count actual case classifications.
    # Use classification_override first.
    # Fall back to offense severity for older Auto records.
    major_incidents = active_cases.filter(
        Q(classification_override="Major Offense") |
        Q(
            classification_override__in=["Auto", ""],
            offense__severity="Major"
        )
    ).count()

    minor_incidents = active_cases.filter(
        Q(classification_override="Minor Offense") |
        Q(
            classification_override__in=["Auto", ""],
            offense__severity="Minor"
        )
    ).count()

    infraction_incidents = active_cases.filter(
        classification_override="Infraction"
    ).count()

    # Keep the total offense codes for the existing summary card.
    total_offense_codes = Offense.objects.count()

    locked_accounts = UserProfile.objects.filter(
        locked=True
    ).count()

    latest_reports = dashboard_cases[:5]

    return render(
        request,
        "core/admin_dashboard.html",
        {
            "students_with_records": students_with_records,
            "open_incidents": open_incidents,
            "total_offense_codes": total_offense_codes,
            "locked_accounts": locked_accounts,
            "latest_reports": latest_reports,
            "dashboard_cases": dashboard_cases,
            "school_years": sorted_school_years,
            "major_incidents": major_incidents,
            "minor_incidents": minor_incidents,
            "infraction_incidents": infraction_incidents,
            "resolved_incidents": resolved_incidents,
            "settings": SystemSettings.objects.get_or_create(id=1)[0],
        }
    )

# =========================================================
# STAFF DASHBOARD
# =========================================================

@login_required
def staff_dashboard(request):
    if account_is_inactive(request):
        return redirect("login")

    if not hasattr(request.user, "userprofile"):
        return redirect("login")

    if request.user.userprofile.role != "Staff":
        return redirect("login")

    cases = CaseRecord.objects.select_related(
        "student",
        "offense"
    ).order_by("-date_reported")

    cases_to_monitor = cases.exclude(
        status="Resolved"
    ).count()

    seven_days_ago = (
        timezone.now().date() - timedelta(days=7)
    )

    recent_updates = cases.filter(
        date_reported__gte=seven_days_ago
    ).count()

    pending_reviews = cases.filter(
        status="Under Review"
    ).count()

    status_counts = cases.values("status").annotate(
        total=Count("id")
        ).order_by("status")

    print("ACTUAL CASE STATUS COUNTS:", list(status_counts))
    print("TOTAL CASES:", cases.count())
    print("UNDER REVIEW:", cases.filter(status="Under Review").count())
    print("PENDING REVIEWS:", pending_reviews)

    recent_cases = cases

    return render(
        request,
        "core/staff_dashboard.html",
        {
            "cases_to_monitor": cases_to_monitor,
            "recent_updates": recent_updates,
            "pending_reviews": pending_reviews,
            "recent_cases": recent_cases,
            "settings": SystemSettings.objects.get_or_create(
                id=1
            )[0],
        }
    )

# =========================================================
# MOCK STUDENT API
# =========================================================

def mock_student_api(request):

    if request.method != "GET":
        return JsonResponse({
            "error": "Only GET requests are allowed."
        }, status=405)

    settings, _ = SystemSettings.objects.get_or_create(id=1)

    if not settings.obdms_student_file:
        return JsonResponse({
            "error": "No student Excel file has been uploaded."
        }, status=404)

    try:
        settings.obdms_student_file.open("rb")

        workbook = load_workbook(
            BytesIO(settings.obdms_student_file.read()),
            read_only=True,
            data_only=True
        )

        settings.obdms_student_file.close()

        if "Students" not in workbook.sheetnames:
            workbook.close()
            return JsonResponse({
                "error": "The Excel file does not contain a Students worksheet."
            }, status=400)

        sheet = workbook["Students"]

        headers = [
            str(cell.value).strip().lower() if cell.value else ""
            for cell in sheet[1]
        ]

        required_columns = [
            "student_id",
            "first_name",
            "middle_name",
            "last_name",
            "course",
            "program",
            "year_level",
            "sex",
        ]

        if not all(column in headers for column in required_columns):
            workbook.close()
            return JsonResponse({
                "error": "The Excel file has missing or incorrect column headers."
            }, status=400)

        students = []

        for row in sheet.iter_rows(min_row=2, values_only=True):

            student = {
                headers[index]: row[index]
                for index in range(len(headers))
                if index < len(row)
            }

            if not student.get("student_id"):
                continue

            students.append({
                "student_id": str(student.get("student_id")).strip(),
                "first_name": str(student.get("first_name") or "").strip(),
                "middle_name": str(student.get("middle_name") or "").strip(),
                "last_name": str(student.get("last_name") or "").strip(),
                "course": str(student.get("course") or "").strip(),
                "program": str(student.get("program") or "").strip(),
                "year_level": str(student.get("year_level") or "").strip(),
                "sex": str(student.get("sex") or "").strip(),
            })

        workbook.close()

        return JsonResponse({
            "students": students,
            "total": len(students)
        })

    except Exception as e:
        return JsonResponse({
            "error": f"Unable to read the student Excel file: {str(e)}"
        }, status=500)

# =========================================================
# STUDENT RECORDS
# =========================================================

@login_required
def student_records(request):
    if account_is_inactive(request):
        return redirect("login")
    if not is_main_admin(request.user) and not staff_has_permission(request.user, "student_records"):
        return HttpResponseForbidden("You do not have permission to view Student Records.")

    if request.method == "POST":
        if not is_main_admin(request.user):
            return HttpResponseForbidden("Only the Main Admin can add student records.")

        student_id = request.POST.get("student_id", "").strip()
        first_name = request.POST.get("first_name", "").strip()
        middle_name = request.POST.get("middle_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()

        if not student_id or not first_name or not last_name:
            messages.error(request, "Student ID, first name, and last name are required.")
            return redirect("student_records")
        if Student.objects.filter(student_id=student_id).exists():
            messages.error(request, "A student with this Student ID already exists.")
            return redirect("student_records")

        status = request.POST.get("status", "Active")

        if status not in ["Active", "LOA", "AWOL"]:
            messages.error(request, "Invalid student status.")
            return redirect("student_records")

        print("COURSE RECEIVED:", repr(request.POST.get("course")))

        student = Student.objects.create(
            student_id=student_id, first_name=first_name, middle_name=middle_name,
            last_name=last_name, image=request.FILES.get("image"),
            course=request.POST.get("course"), year_level=request.POST.get("year_level"),
            program=request.POST.get("program"), sex=request.POST.get("sex"),
            status=status,
            mother_name=request.POST.get("mother_name"),
            mother_address=request.POST.get("mother_address"),
            mother_email=request.POST.get("mother_email"),
            mother_contact_no=request.POST.get("mother_contact_no"),
            father_name=request.POST.get("father_name"),
            father_address=request.POST.get("father_address"),
            father_email=request.POST.get("father_email"),
            father_contact_no=request.POST.get("father_contact_no"),
            guardian_name=request.POST.get("guardian_name"),
            guardian_address=request.POST.get("guardian_address"),
            guardian_email=request.POST.get("guardian_email"),
            guardian_contact_no=request.POST.get("guardian_contact_no"),
        )
        AuditLog.objects.create(
            user=request.user, page="Student Records", action="Student Added",
            description=f"Added student {student.student_id} - {student.first_name} {student.last_name}"
        )
        messages.success(request, "Student record added successfully.")
        return redirect("student_records")

    return render(request, "core/student_records.html", {
        "students": Student.objects.exclude(
    status="Graduated"
).order_by("last_name", "first_name"),
        "settings": SystemSettings.objects.get_or_create(id=1)[0],
        "can_edit_students": is_main_admin(request.user),
    })



# =========================================================
# EDIT STUDENT
# =========================================================

@login_required
def edit_student(request, student_id):

    if account_is_inactive(request):
        return redirect("login")
    # Only the Main Admin can edit students.
    if not is_main_admin(request.user):
        return HttpResponseForbidden("Only the Main Admin can edit student records.")

    try:
        student = Student.objects.get(id=student_id)
    except Student.DoesNotExist:
        messages.error(request, "Student record not found.")
        return redirect("student_records")

    # Graduated students cannot be edited
    if student.status == "Graduated":
        messages.error(
            request,
            "Graduated student records cannot be edited."
        )
        return redirect("student_records")

    if request.method == "POST":

        new_student_id = request.POST.get("student_id", "").strip()
        first_name = request.POST.get("first_name", "").strip()
        middle_name = request.POST.get("middle_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        course = request.POST.get("course", "")
        year_level = request.POST.get("year_level", "")
        program = request.POST.get("program", "")
        sex = request.POST.get("sex", "")

        new_status = request.POST.get("status", "Active")
        graduation_date_input = request.POST.get("graduation_date", "").strip()

        valid_statuses = ["Active", "LOA", "AWOL", "Graduated"]

        if new_status not in valid_statuses:
            messages.error(request, "Invalid student status.")
            return redirect("student_records")

        # Graduation date is required for Graduated students
        graduation_date = None

        if new_status == "Graduated":

            if not graduation_date_input:
                messages.error(
                    request,
                    "Please enter the graduation date."
                )
                return redirect("student_records")

            graduation_date = parse_date(graduation_date_input)

            if graduation_date is None:
                messages.error(
                    request,
                    "Please enter a valid graduation date."
                )
                return redirect("student_records")

        if not new_student_id:
            messages.error(request, "Student ID is required.")
            return redirect("student_records")

        # Check for duplicate Student ID
        if Student.objects.filter(
            student_id=new_student_id
        ).exclude(id=student.id).exists():

            messages.error(
                request,
                "Another student already uses this Student ID."
            )
            return redirect("student_records")

        # Update student information
        student.student_id = new_student_id
        student.first_name = first_name
        student.middle_name = middle_name
        student.last_name = last_name
        student.course = course
        student.year_level = year_level
        student.program = program
        student.sex = sex
        student.status = new_status
        student.graduation_date = graduation_date

        # Mother details
        student.mother_name = request.POST.get("mother_name", "")
        student.mother_address = request.POST.get("mother_address", "")
        student.mother_email = request.POST.get("mother_email", "")
        student.mother_contact_no = request.POST.get(
            "mother_contact_no", ""
        )

        # Father details
        student.father_name = request.POST.get("father_name", "")
        student.father_address = request.POST.get("father_address", "")
        student.father_email = request.POST.get("father_email", "")
        student.father_contact_no = request.POST.get(
            "father_contact_no", ""
        )

        # Guardian details
        student.guardian_name = request.POST.get("guardian_name", "")
        student.guardian_address = request.POST.get("guardian_address", "")
        student.guardian_email = request.POST.get("guardian_email", "")
        student.guardian_contact_no = request.POST.get(
            "guardian_contact_no", ""
        )

        # Update profile picture only if a new one was selected
        if request.FILES.get("image"):
            student.image = request.FILES["image"]

        student.save()

        # Keep the student archive synchronized
        if student.status == "Graduated":

            ArchivedStudent.objects.update_or_create(
                student_id=student.student_id,
                defaults={
                    "first_name": student.first_name,
                    "last_name": student.last_name,
                    "course": student.course,
                    "program": student.program,
                    "year_level": student.year_level,
                    "sex": student.sex,
                    "graduation_date": student.graduation_date,
                    "mother_name": student.mother_name,
                    "mother_address": student.mother_address,
                    "mother_email": student.mother_email,
                    "mother_contact_no": student.mother_contact_no,
                    "father_name": student.father_name,
                    "father_address": student.father_address,
                    "father_email": student.father_email,
                    "father_contact_no": student.father_contact_no,
                    "guardian_name": student.guardian_name,
                    "guardian_address": student.guardian_address,
                    "guardian_email": student.guardian_email,
                    "guardian_contact_no": student.guardian_contact_no,
                }
            )

        else:

            # Remove any old archive snapshot if status is not Graduated
            ArchivedStudent.objects.filter(
                student_id=student.student_id
            ).delete()

# Audit log
        if new_status == "Graduated":
            action = "Student Archived"
            page = "Student Archive"
            description = (
                f"Archived student {student.student_id} - "
                f"{student.first_name} {student.last_name} "
                f"with graduation date {student.graduation_date}."
            )

        else:
            action = "Student Updated"
            page = "Student Records"
            description = (
                f"Updated student {student.student_id} - "
                f"{student.first_name} {student.last_name}; "
                f"Status: {student.status}"
            )

        AuditLog.objects.create(
            user=request.user,
            page=page,
            action=action,
            description=description
        )

        messages.success(
            request,
            "Student record updated successfully."
        )

        return redirect("student_records")


    return render(
        request,
        "core/edit_student.html",
        {
            "student": student,
            "settings": SystemSettings.objects.get_or_create(id=1)[0],
        }
    )


# =========================================================
# STUDENT ARCHIVE
# =========================================================

@login_required
def student_archive(request):
    if account_is_inactive(request):
        return redirect("login")
    is_admin = is_main_admin(request.user)
    if not is_admin:
        if not staff_has_permission(request.user, "student_archive"):
            return HttpResponseForbidden("You do not have permission to view Student Archive.")
        if request.method == "POST":
            return HttpResponseForbidden("Only the Main Admin can restore archived students.")

    if request.method == "POST" and request.POST.get("action") == "restore":
        archived_id = request.POST.get("archived_id")
        try:
            archived = ArchivedStudent.objects.get(id=archived_id)
            student = Student.objects.get(student_id=archived.student_id)
            student.status = "Active"
            student.graduation_date = None
            student.save()
            archived.delete()
            AuditLog.objects.create(
                user=request.user, page="Student Archive", action="Restore Student",
                description=f"Restored student {student.student_id} - {student.first_name} {student.last_name} to Active status."
            )
            messages.success(request, "Student restored successfully.")
        except ArchivedStudent.DoesNotExist:
            messages.error(request, "Archived student record not found.")
        except Student.DoesNotExist:
            messages.error(request, "The original student record could not be found.")
        return redirect("student_archive")

    return render(request, "core/student_archive.html", {
        "archived_students": ArchivedStudent.objects.all().order_by("-graduation_date", "-archived_at"),
        "settings": SystemSettings.objects.get_or_create(id=1)[0],
        "can_restore_students": is_admin,
    })




def get_case_classification(student, offense, exclude_case_id=None):
    # Handle offenses that do not use accumulation rules.
    if offense.accumulation_type == "direct_minor":
        return "Minor Offense"

    if offense.accumulation_type == "direct_major":
        return "Major Offense"

    previous_cases = CaseRecord.objects.filter(
        student=student,
        offense=offense,
    )

    if exclude_case_id is not None:
        previous_cases = previous_cases.exclude(
            id=exclude_case_id
        )

    instance_number = previous_cases.count() + 1

    # Apply the configured rule for this occurrence.
    for rule in offense.accumulation_rules.all():
        rule_range = rule.instance_range.strip().lower()

        try:
            if "-" in rule_range:
                start, end = rule_range.split("-", 1)
                matches = int(start.strip()) <= instance_number <= int(end.strip())

            elif rule_range.endswith("+"):
                start = int(rule_range[:-1].strip())
                matches = instance_number >= start

            else:
                matches = instance_number == int(rule_range)

            if matches:
                return rule.classification

        except (ValueError, TypeError):
            continue

    # Do not silently substitute Minor/Major if no rule matches.
    return "Unconfigured"

# =========================================================
# CASE RECORD
# =========================================================

@login_required
def case_record(request):

    if account_is_inactive(request):
        return redirect("login")

    is_admin = is_main_admin(request.user)
    permissions = set()
    if not is_admin:
        profile = get_profile(request.user)
        if not profile or profile.role != "Staff":
            return HttpResponseForbidden("You do not have permission to access Case Record.")
        permissions = set(profile.permissions or [])
        if "case_record" in permissions:
            permissions.add("case_view")

    can_view_cases = is_admin or bool(permissions.intersection({
    "case_view", "case_full", "case_create"
    }))
    can_create_cases = is_admin or bool(permissions.intersection({"case_full", "case_create"}))
    can_edit_cases = is_admin or "case_full" in permissions
    can_add_case_information = (
    is_admin
    or "case_full" in permissions
)

    if not can_view_cases:
        return HttpResponseForbidden("You do not have permission to view Case Record.")

    # Edit existing case — Admin only
    edit_id = request.GET.get("edit")
    edit_case = None

    if edit_id and can_edit_cases:
        try:
            edit_case = CaseRecord.objects.get(
                id=edit_id,
                is_archived=False
            )
        except CaseRecord.DoesNotExist:
            messages.error(
                request,
                "Case not found or it has already been archived."
            )
            return redirect("case_record")

    if edit_id and not can_edit_cases:
        return HttpResponseForbidden("You do not have permission to edit case records.")

    # Handle permitted case actions.
    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "add_information":
            if not can_add_case_information:
                return HttpResponseForbidden("You cannot add case information.")

            # Validate uploaded attachments
            uploaded_files = request.FILES.getlist("attachments")
            upload_error = validate_case_uploads(uploaded_files)

            if upload_error:
                messages.error(request, upload_error)
                return redirect("case_record")
            case_id_for_info = request.POST.get("case_id")
            note = request.POST.get("note", "").strip()
            if not case_id_for_info or not note:
                messages.error(request, "Select a case and enter case information.")
                return redirect("case_record")

            try:
                info_case = CaseRecord.objects.get(id=case_id_for_info, is_archived=False)
            except (CaseRecord.DoesNotExist, ValueError, TypeError):
                messages.error(request, "Case not found or it has been archived.")
                return redirect("case_record")

            CaseDiaryEntry.objects.create(
                case=info_case,
                entry_date=parse_date(request.POST.get("entry_date", "")) or timezone.localdate(),
                note=note,
                created_by=request.user,
            )

            for uploaded_file in uploaded_files:
                CaseAttachment.objects.create(
                    case=info_case,
                    file=uploaded_file,
                    file_name=uploaded_file.name,
                    uploaded_by=request.user
                )
            AuditLog.objects.create(
                user=request.user, page="Case Record", action="Added Case Information",
                description=f"Added information to case {info_case.case_number}."
            )
            messages.success(request, "Case information added successfully.")
            return redirect("case_record_details", case_id=info_case.id)

        if request.POST.get("case_id"):
            if not can_edit_cases:
                return HttpResponseForbidden("You do not have permission to edit existing case records.")
        elif not can_create_cases:
            return HttpResponseForbidden("You do not have permission to create case records.")

        if not is_admin and "case_full" not in permissions and "case_create" not in permissions:
            return HttpResponseForbidden("You do not have permission to create or edit case records.")

        case_number = request.POST.get("case_number", "").strip()
        student_id = request.POST.get("student_id")
        offense_id = request.POST.get("offense_id")
        case_id = request.POST.get("case_id")

        year_level = request.POST.get("year_level", "")
        term = request.POST.get("term", "")
        school_year = request.POST.get("school_year", "")

        status = request.POST.get("status", "Reported")
        remarks = request.POST.get("remarks", "")

        date_filed_input = request.POST.get("date_filed", "")
        campus = request.POST.get("campus", "")
        incident_location = request.POST.get("incident_location", "")
        formative_status = request.POST.get("formative_status", "Pending")
        handled_by_id = request.POST.get("handled_by")


        appeal_resolution = request.POST.get(
            "appeal_resolution", ""
        )

        resolution_authority = request.POST.get(
        "resolution_authority", ""
        )

        id_violation = request.POST.get("id_violation", "None")
        id_violation_remarks = request.POST.get(
            "id_violation_remarks", ""
        )
        sanction_remarks = request.POST.get("sanction_remarks", "")

        sanction_name = request.POST.get("sanction_name", "").strip()
        new_sanction_remarks = request.POST.get(
            "new_sanction_remarks", ""
        ).strip()

        evidence = request.FILES.get("evidence")
        attachments = request.FILES.getlist("attachments")

        print("FILES RECEIVED:", len(attachments))
        print("FILE NAMES:", [f.name for f in attachments])

        # Validate evidence and attachments
        all_uploaded_files = attachments + ([evidence] if evidence else [])
        upload_error = validate_case_uploads(all_uploaded_files)

        if upload_error:
            messages.error(request, upload_error)
            return redirect("case_record")

        try:
            student = Student.objects.get(id=student_id)
            offense = Offense.objects.get(id=offense_id)

            # Validate the selected case status
            valid_statuses = [
                choice[0]
                for choice in CaseRecord.CASE_STATUS_CHOICES
            ]

            if status not in valid_statuses:
                messages.error(request, "Invalid case status.")
                return redirect("case_record")

            # Validate formative status
            valid_formative_statuses = [
                choice[0]
                for choice in CaseRecord.FORMATIVE_STATUS_CHOICES
            ]

            if formative_status not in valid_formative_statuses:
                messages.error(request, "Invalid formative status.")
                return redirect("case_record")

            # Validate ID violation
            valid_id_violations = [
                choice[0]
                for choice in CaseRecord.ID_VIOLATION_CHOICES
            ]

            if id_violation not in valid_id_violations:
                messages.error(request, "Invalid ID violation.")
                return redirect("case_record")


            # Validate appeal resolution
            valid_appeal_resolutions = [
                choice[0]
                for choice in CaseRecord.APPEAL_RESOLUTION_CHOICES
            ]

            if appeal_resolution not in valid_appeal_resolutions:
                messages.error(request, "Invalid appeal resolution.")
                return redirect("case_record")

            valid_resolution_authorities = [
                choice[0]
                for choice in CaseRecord.RESOLUTION_AUTHORITY_CHOICES
            ]

            if resolution_authority not in valid_resolution_authorities:
                messages.error(request, "Invalid resolution authority.")
                return redirect("case_record")  

            # Parse Date Filed
            date_filed = None

            if date_filed_input:
                date_filed = parse_date(date_filed_input)

                if date_filed is None:
                    messages.error(
                        request,
                        "Please enter a valid Date Filed."
                    )
                    return redirect("case_record")

            # Find the assigned handler, if provided
            handled_by = None

            if handled_by_id:
                User = get_user_model()

                try:
                    handled_by = User.objects.get(
                        id=handled_by_id,
                        is_active=True
                    )
                except (User.DoesNotExist, ValueError, TypeError):
                    messages.error(
                        request,
                        "The selected case handler was not found or is inactive."
                    )
                    return redirect("case_record")

            # Update existing case
            if case_id:

                case = CaseRecord.objects.get(id=case_id)

                if case.is_archived:
                    messages.error(
                        request,
                        "Archived case records cannot be modified."
                    )
                    return redirect("case_record")

                previous_values = {
                    "status": case.status,
                    "student": (
                        f"{case.student.student_id} - "
                        f"{case.student.first_name} {case.student.last_name}"
                    ),
                    "offense": case.offense.offense_name,
                    "handled_by": (
                        case.handled_by.get_full_name() or case.handled_by.username
                        if case.handled_by else "Not assigned"
                    ),
                    "resolution_authority": case.resolution_authority,
                    "appeal_resolution": case.appeal_resolution,
                    "campus": case.campus,
                    "formative_status": case.formative_status,
                    "remarks": case.remarks,
                    "term": case.term,
                    "school_year": case.school_year,
                    "year_level": case.year_level,
                    "date_filed": case.date_filed,
                    "id_violation": case.id_violation,
                    "id_violation_remarks": case.id_violation_remarks,
                    "sanction_remarks": case.sanction_remarks,
                }

                if CaseRecord.objects.filter(
                    case_number=case_number
                ).exclude(id=case.id).exists():
                    messages.error(
                        request,
                        "That case number is already in use."
                    )
                    return redirect("case_record")

                # Check whether the student or offense changed.
                student_or_offense_changed = (
                    case.student_id != student.id
                    or case.offense_id != offense.id
                )

                case.student = student
                case.offense = offense

                # Preserve classification when editing an existing case.
                if student_or_offense_changed:
                    case.classification_override = get_case_classification(
                        student,
                        offense,
                        exclude_case_id=case.id,
                    )

                case.term = term
                case.school_year = school_year
                case.year_level = year_level
                case.status = status
                case.remarks = remarks
                case.campus = campus
                case.incident_location = incident_location
                case.formative_status = formative_status
                case.handled_by = handled_by
                case.appeal_resolution = appeal_resolution
                case.resolution_authority = resolution_authority
                case.id_violation = id_violation
                case.id_violation_remarks = id_violation_remarks
                case.sanction_remarks = sanction_remarks

                if date_filed:
                    case.date_filed = date_filed

                if evidence:
                    case.evidence = evidence

                case.save()

                # Save a new sanction if provided
                if sanction_name:

                    CaseSanction.objects.create(
                        case=case,
                        sanction_name=sanction_name,
                        remarks=new_sanction_remarks,
                    )

                    create_case_diary_entry(
                        case,
                        request.user,
                        f"Sanction added: {sanction_name}."
                    )

                    AuditLog.objects.create(
                        user=request.user,
                        page="Case Record",
                        action="Added Case Sanction",
                        description=(
                            f"Added sanction '{sanction_name}' "
                            f"to case {case.case_number}."
                        )
                    )

                # Automatically archive resolved cases
                resolved_statuses = [
                    "Resolved",
                    "Resolved by DO",
                    "Resolved by Director",
                    "Resolved by Other",
                    "Resolved by CRD Director",
                    "Resolved by Discipline Board",
                    "Resolved by CODI",
                ]

                if case.status in resolved_statuses:

                    case.is_archived = True
                    case.archived_at = timezone.now()
                    case.save(
                        update_fields=["is_archived", "archived_at"]
                    )

                    # Record automatic archiving in the case diary
                    create_case_diary_entry(
                        case,
                        request.user,
                        f"Case record {case.case_number} was automatically archived after resolution."
                    )

                    # Create archive snapshot
                    ArchivedRecord.objects.update_or_create(
                        case_id=case.case_number,
                        defaults={
                            "student_id": case.student.student_id,
                            "student_name": (
                                f"{case.student.first_name} "
                                f"{case.student.last_name}"
                            ),
                            "course": case.student.course,
                            "offense": case.offense.offense_name,
                            "status": case.status,
                            "date_reported": case.date_reported,
                            "archived_at": timezone.localdate(),
                            "notes": case.remarks,
                            "term": case.term,
                            "school_year": case.school_year,
                            "year_level": case.year_level,
                            "classification_override": (
                                case.classification_override
                            ),
                            "evidence_name": (
                                case.evidence.name
                                if case.evidence else ""
                            ),
                        }
                    )

                    AuditLog.objects.create(
                        user=request.user,
                        page="Archive Records",
                        action="Automatic Case Archive",
                        description=(
                            f"Case {case.case_number} was automatically "
                            f"archived with status '{case.status}'."
                        )
                    )

                # Record changes in the case diary
                current_values = {
                    "status": case.status,
                    "student": (
                        f"{case.student.student_id} - "
                        f"{case.student.first_name} {case.student.last_name}"
                    ),
                    "offense": case.offense.offense_name,
                    "handled_by": (
                        case.handled_by.get_full_name() or case.handled_by.username
                        if case.handled_by else "Not assigned"
                    ),
                    "resolution_authority": case.resolution_authority,
                    "appeal_resolution": case.appeal_resolution,
                    "campus": case.campus,
                    "formative_status": case.formative_status,
                    "remarks": case.remarks,
                    "term": case.term,
                    "school_year": case.school_year,
                    "year_level": case.year_level,
                    "date_filed": case.date_filed,
                    "id_violation": case.id_violation,
                    "id_violation_remarks": case.id_violation_remarks,
                    "sanction_remarks": case.sanction_remarks,
                }

                field_labels = {
                    "status": "Case status",
                    "student": "Student",
                    "offense": "Violation",
                    "handled_by": "Person handling the case",
                    "resolution_authority": "Resolution authority",
                    "appeal_resolution": "Appeal resolution",
                    "campus": "Incident campus",
                    "formative_status": "Formative intervention status",
                    "remarks": "Case details",
                    "term": "Term",
                    "school_year": "School year",
                    "year_level": "Year level",
                    "date_filed": "Date filed",
                    "id_violation": "ID violation",
                    "id_violation_remarks": "ID violation remarks",
                    "sanction_remarks": "Sanction remarks",
                }

                for field, old_value in previous_values.items():
                    new_value = current_values[field]

                    if old_value != new_value:
                        create_case_diary_entry(
                            case,
                            request.user,
                            f"{field_labels[field]} was updated."
                        )

                # Save additional attachments
                for attachment_file in attachments:
                    CaseAttachment.objects.create(
                        case=case,
                        file=attachment_file,
                        file_name=attachment_file.name,
                        uploaded_by=request.user
                    )

                    print(
                        "ATTACHMENTS SAVED:",
                        CaseAttachment.objects.filter(case=case).count()
                    )

                AuditLog.objects.create(
                    user=request.user,
                    page="Case Record",
                    action="Updated Case Record",
                    description=f"Case {case.case_number} was updated."
                )

                messages.success(
                    request,
                    "Case record updated successfully."
                )

            # Create new case
            else:

                try:
                    case_number = generate_case_number(
                        school_year,
                        term,
                        campus
                    )
                except ValueError as error:
                    messages.error(request, str(error))
                    return redirect("case_record")

                case = CaseRecord.objects.create(
                    case_number=case_number,
                    student=student,
                    offense=offense,
                    date_reported=timezone.localdate(),
                    date_filed=date_filed or timezone.localdate(),
                    term=term,
                    school_year=school_year.strip(),
                    year_level=year_level,
                    status=status,
                    remarks=remarks,
                    campus=campus,
                    incident_location=incident_location,
                    formative_status=formative_status,
                    handled_by=handled_by,
                    appeal_resolution=appeal_resolution,
                    resolution_authority=resolution_authority,
                    id_violation=id_violation,
                    id_violation_remarks=id_violation_remarks,
                    sanction_remarks=sanction_remarks,
                    classification_override=get_case_classification(
                        student,
                        offense,
                    ),
                    is_active=True,
                )

                # Record case creation in the diary
                create_case_diary_entry(
                    case,
                    request.user,
                    f"Case record {case.case_number} was created."
                )

                if evidence:
                    case.evidence = evidence
                    case.save()

                # Save additional attachments
                for attachment_file in attachments:
                    CaseAttachment.objects.create(
                        case=case,
                        file=attachment_file,
                        file_name=attachment_file.name,
                        uploaded_by=request.user
                    )

                    create_case_diary_entry(
                        case,
                        request.user,
                        f"Evidence file '{attachment_file.name}' was uploaded."
                    )

                AuditLog.objects.create(
                    user=request.user,
                    page="Case Record",
                    action="Created Case Record",
                    description=f"Case {case.case_number} was created."
                )

                messages.success(
                    request,
                    f"Case record {case.case_number} saved successfully."
                )

            return redirect("case_record")


        except Student.DoesNotExist:
            messages.error(request, "Student ID was not found.")
            return redirect("case_record")

        except Offense.DoesNotExist:
            messages.error(
                request,
                "The selected violation was not found."
            )
            return redirect("case_record")

        except CaseRecord.DoesNotExist:
            messages.error(
                request,
                "Case not found or it has already been archived."
            )
            return redirect("case_record")

    # Get students and active offenses
    students = Student.objects.all().order_by(
        "last_name",
        "first_name"
    )

    offenses = Offense.objects.filter(
        status="Active"
    ).order_by("offense_name")

    # Count occurrences using all original cases,
    # including archived cases.
    all_cases = list(
        CaseRecord.objects.select_related(
            "student",
            "offense"
        ).order_by("id")
    )

    offense_counts = {}

    for case in all_cases:
        key = (case.student_id, case.offense_id)
        offense_counts[key] = offense_counts.get(key, 0) + 1

        if not case.is_archived:
            case.offense_count = offense_counts[key]

    # Display only active cases.
    case_records = [
        case for case in all_cases
        if not case.is_archived
    ]

    # Show the newest-added case first.
    case_records.sort(
        key=lambda case: case.id,
        reverse=True
    )

    total_cases = len(case_records)

    # Available users for the handler dropdown.
    User = get_user_model()

    handlers = User.objects.filter(
        is_active=True
    ).order_by(
        "first_name",
        "last_name",
        "username"
    )

    return render(
        request,
        "core/case_record.html",
        {
            "students": students,
            "offenses": offenses,
            "case_records": case_records,
            "total_cases": total_cases,
            "edit_case": edit_case,
            "handlers": handlers,
            "settings": SystemSettings.objects.get_or_create(id=1)[0],
            "can_view_cases": can_view_cases,
            "can_create_cases": can_create_cases,
            "can_edit_cases": can_edit_cases,
            "can_add_case_information": can_add_case_information,
        }
    )

# =========================================================
# OFFENSE MASTER LIST
# =========================================================

@login_required
def offense_master_list(request):

    if not request.user.is_superuser:
        return redirect("login")

    if request.method == "POST":

        offense_id = request.POST.get("offense_id")
        action = request.POST.get("action")

        # DELETE OFFENSE
        if action == "delete":

            try:
                offense = Offense.objects.get(id=offense_id)
            except (Offense.DoesNotExist, ValueError, TypeError):
                messages.error(request, "Offense code not found.")
                return redirect("offense_master_list")

            if CaseRecord.objects.filter(offense=offense).exists():
                messages.error(
                    request,
                    "This offense is already used in a case record. "
                    "Set it to Inactive instead."
                )
                return redirect("offense_master_list")

            offense_code = offense.offense_code
            offense.accumulation_rules.all().delete()
            offense.delete()

            AuditLog.objects.create(
                user=request.user,
                page="Offense Master List",
                action="Deleted Offense Code",
                description=f"Deleted offense code {offense_code}."
            )

            messages.success(
                request,
                "Offense code deleted successfully."
            )
            return redirect("offense_master_list")

        offense_code = request.POST.get(
            "offense_code", ""
        ).strip()

        offense_name = request.POST.get(
            "offense_name", ""
        ).strip()

        severity = request.POST.get("severity", "")
        status = request.POST.get("status", "")

        accumulation_type = request.POST.get(
            "accumulation_type", "accumulating"
        )

        instance_ranges = request.POST.getlist("instance_range[]")
        classifications = request.POST.getlist("classification[]")
        action_notes = request.POST.getlist("action_note[]")

        # VALIDATE OFFENSE FIELDS
        if not offense_code or not offense_name:
            messages.error(
                request,
                "Offense code and offense name are required."
            )
            return redirect("offense_master_list")

        if severity not in ["Minor", "Major"]:
            messages.error(
                request,
                "Please select a valid severity."
            )
            return redirect("offense_master_list")

        if status not in ["Active", "Inactive"]:
            messages.error(
                request,
                "Please select a valid status."
            )
            return redirect("offense_master_list")

        valid_accumulation_types = [
            "accumulating",
            "direct_minor",
            "direct_major",
        ]

        if accumulation_type not in valid_accumulation_types:
            messages.error(
                request,
                "Please select a valid accumulation type."
            )
            return redirect("offense_master_list")

        # PREPARE AND VALIDATE RULES
        rules_to_save = []

        if accumulation_type == "accumulating":

            for index, instance_range in enumerate(instance_ranges):

                classification = (
                    classifications[index]
                    if index < len(classifications)
                    else ""
                ).strip()

                action_note = (
                    action_notes[index]
                    if index < len(action_notes)
                    else ""
                ).strip()

                instance_range = instance_range.strip()

                # Ignore completely empty rows.
                if not instance_range and not classification and not action_note:
                    continue

                if not instance_range or not classification:
                    messages.error(
                        request,
                        "Every accumulation rule needs an instance range "
                        "and a classification."
                    )
                    return redirect("offense_master_list")

                rules_to_save.append({
                    "instance_range": instance_range,
                    "classification": classification,
                    "action_note": action_note,
                    "order": len(rules_to_save),
                })

        # CHECK FOR DUPLICATE OFFENSE CODE
        duplicate_codes = Offense.objects.filter(
            offense_code=offense_code
        )

        if offense_id:
            duplicate_codes = duplicate_codes.exclude(id=offense_id)

        if duplicate_codes.exists():
            messages.error(
                request,
                "That offense code is already in use."
            )
            return redirect("offense_master_list")

        # FIND OR CREATE OFFENSE
        if offense_id:

            try:
                offense = Offense.objects.get(id=offense_id)
            except (Offense.DoesNotExist, ValueError, TypeError):
                messages.error(request, "Offense code not found.")
                return redirect("offense_master_list")

            offense.offense_code = offense_code
            offense.offense_name = offense_name
            offense.severity = severity
            offense.status = status
            offense.accumulation_type = accumulation_type
            offense.save()

            action_name = "Updated Offense Code"

        else:

            offense = Offense.objects.create(
                offense_code=offense_code,
                offense_name=offense_name,
                severity=severity,
                status=status,
                accumulation_type=accumulation_type,
            )

            action_name = "Created Offense Code"

        # REPLACE EXISTING ACCUMULATION RULES
        offense.accumulation_rules.all().delete()

        for rule in rules_to_save:
            offense.accumulation_rules.create(
                instance_range=rule["instance_range"],
                classification=rule["classification"],
                action_note=rule["action_note"],
                order=rule["order"],
            )

        # AUDIT LOG
        AuditLog.objects.create(
            user=request.user,
            page="Offense Master List",
            action=action_name,
            description=(
                f"{action_name}: {offense_code}. "
                f"Accumulation type: {accumulation_type}. "
                f"Accumulation rules saved: {len(rules_to_save)}."
            )
        )

        messages.success(
            request,
            "Offense code and accumulation settings saved successfully."
        )

        return redirect("offense_master_list")

    # LOAD OFFENSE FOR EDITING
    edit_id = request.GET.get("edit")
    edit_offense = None

    if edit_id:
        try:
            edit_offense = Offense.objects.get(id=edit_id)
        except (Offense.DoesNotExist, ValueError, TypeError):
            messages.error(request, "Offense code not found.")

    # GET ALL OFFENSE RECORDS
    offenses = Offense.objects.all().order_by("offense_code")

    return render(
        request,
        "core/offense_master_list.html",
        {
            "offenses": offenses,
            "edit_offense": edit_offense,
            "settings": SystemSettings.objects.get_or_create(id=1)[0],
        }
    )

# =========================================================
# SAVE OFFENSE RULES
# =========================================================

@login_required
def save_offense_rules(request, offense_id):

    # Only Admin can modify rules
    if not request.user.is_superuser:
        return redirect("login")

    if request.method != "POST":
        return redirect("offense_master_list")

    try:
        offense = Offense.objects.get(id=offense_id)
    except (Offense.DoesNotExist, ValueError, TypeError):
        messages.error(request, "Offense not found.")
        return redirect("offense_master_list")

    accumulation_type = request.POST.get("accumulation_type", "")

    valid_types = [
        value
        for value, label in Offense.ACCUMULATION_TYPE_CHOICES
    ]

    if accumulation_type not in valid_types:
        messages.error(request, "Invalid accumulation type.")
        return redirect("offense_master_list")

    ranges = request.POST.getlist("instance_range[]")
    classifications = request.POST.getlist("classification[]")
    notes = request.POST.getlist("action_note[]")

    if accumulation_type == "accumulating":

        if (
            not ranges
            or len(ranges) != len(classifications)
            or len(ranges) != len(notes)
        ):
            messages.error(request, "Please complete all rule fields.")
            return redirect("offense_master_list")

        valid_classifications = [
            value
            for value, label in AccumulationRule.CLASSIFICATION_CHOICES
        ]

        for instance_range, classification in zip(
            ranges, classifications
        ):
            if (
                not instance_range.strip()
                or classification not in valid_classifications
            ):
                messages.error(request, "Invalid accumulation rule.")
                return redirect("offense_master_list")

    with transaction.atomic():

        offense.accumulation_type = accumulation_type
        offense.save()

        AccumulationRule.objects.filter(
            offense=offense
        ).delete()

        if accumulation_type == "accumulating":

            for index, (instance_range, classification, note) in enumerate(
                zip(ranges, classifications, notes),
                start=1,
            ):
                AccumulationRule.objects.create(
                    offense=offense,
                    instance_range=instance_range.strip(),
                    classification=classification,
                    action_note=note.strip(),
                    order=index,
                )

        AuditLog.objects.create(
            user=request.user,
            page="Offense Master List",
            action="Updated Offense Rules",
            description=f"Updated rules for {offense.offense_name}."
        )

    messages.success(
        request,
        "Offense rules saved successfully."
    )

    return redirect("offense_master_list")

# =========================================================
# LOGOUT
# =========================================================

def logout_view(request):

    logout(request)

    return redirect("login")


# =========================================================
# GENERATE REPORTS
# =========================================================

@login_required
def generate_reports(request):
    if account_is_inactive(request):
        return redirect("login")

    is_admin = is_main_admin(request.user)

    can_view_report_dashboard = (
        is_admin or staff_has_permission(request.user, "report_dashboard")
    )

    can_print_reports = (
        is_admin or staff_has_permission(request.user, "print_reports")
    )

    can_view_case_report = (
        is_admin
        or staff_has_permission(request.user, "case_report")
        or can_print_reports
    )

    if not (can_view_report_dashboard or can_view_case_report):
        return HttpResponseForbidden(
            "You do not have permission to view reports."
        )

    report_queryset = CaseRecord.objects.filter(
    is_archived=False
)

    # Only provide individual case and student data
    # to users who have Case Report permission.
    case_records = []
    students = []
    offenses = []

    if can_view_case_report:
        case_records = list(
            report_queryset.select_related("student", "offense")
        )

        case_records.sort(
            key=case_number_sort_key,
            reverse=True
        )

        students = Student.objects.all().order_by(
            "last_name", "first_name"
        )

        offenses = Offense.objects.all().order_by("offense_name")

    # Dashboard statistics
    total_cases = 0
    under_review_cases = 0
    pending_cases = 0
    resolved_cases = 0
    chart_data = {}

    if can_view_report_dashboard:

        total_cases = report_queryset.count()

        under_review_cases = report_queryset.filter(
            status__iexact="Under Review"
        ).count()

        pending_cases = report_queryset.filter(
            status__icontains="Pending"
        ).count()

        resolved_cases = report_queryset.filter(
            status__startswith="Resolved"
        ).count()

        # Prepare grouped chart data without exposing individual records.
        def get_chart_data(field):
            results = report_queryset.values(field).annotate(
                total=Count("id")
            ).order_by()

            return [
                {
                    "label": str(item[field] or "Unspecified"),
                    "count": item["total"],
                }
                for item in results
            ]

        chart_data = {
            "status": get_chart_data("status"),
            "program": get_chart_data("student__program"),
            "offense": get_chart_data("offense__offense_name"),
            "year_level": get_chart_data("year_level"),
            "sex": get_chart_data("student__sex"),
            "date": get_chart_data("date_reported"),
        }

        # Group classifications using the override first.
        # If the classification is Auto, use the offense severity.
        classification_results = report_queryset.values(
            "classification_override",
            "offense__severity"
        ).annotate(
            total=Count("id")
        ).order_by()

        classification_counts = {}

        for item in classification_results:
            classification = item["classification_override"]

            if not classification or classification == "Auto":
                severity = item["offense__severity"]

                if severity == "Major":
                    classification = "Major Offense"
                elif severity == "Minor":
                    classification = "Minor Offense"
                else:
                    classification = "Unspecified"

            classification_counts[classification] = (
                classification_counts.get(classification, 0)
                + item["total"]
            )

        chart_data["classification"] = [
            {
                "label": label,
                "count": count,
            }
            for label, count in classification_counts.items()
        ]

    return render(request, "core/generate_reports.html", {
        "case_records": case_records,
        "students": students,
        "offenses": offenses,
        "settings": SystemSettings.objects.get_or_create(id=1)[0],

        "can_view_report_dashboard": can_view_report_dashboard,
        "can_view_case_report": can_view_case_report,
        "can_print_reports": can_print_reports,

        "total_cases": total_cases,
        "under_review_cases": under_review_cases,
        "pending_cases": pending_cases,
        "resolved_cases": resolved_cases,
        "chart_data": chart_data,
    })

# =========================================================
# PRINT REPORT
# =========================================================

@login_required
def print_report(request):
    if account_is_inactive(request):
        return redirect("login")
    if not is_main_admin(request.user):
        if not staff_has_permission(request.user, "print_reports"):
            return HttpResponseForbidden("You do not have permission to print reports.")

    case_ids = request.GET.getlist("case_id")
    if not case_ids:
        messages.error(request, "Please select at least one case record.")
        return redirect("generate_reports")

    case_records = list(CaseRecord.objects.filter(
        id__in=case_ids, is_archived=False
    ).select_related("student", "offense"))
    if not case_records:
        messages.error(request, "No valid case records were found.")
        return redirect("generate_reports")

    case_records.sort(key=case_number_sort_key, reverse=True)
    same_student = len({case.student_id for case in case_records}) == 1
    return render(request, "core/print_report.html", {
        "case_records": case_records,
        "same_student": same_student,
        "notes": request.GET.get("notes", ""),
        "agreement_text": request.GET.get("agreement_text", ""),
        "settings": SystemSettings.objects.get_or_create(id=1)[0],
    })

# =========================================================
# ARCHIVE RECORDS
# =========================================================


@login_required
def archive_records(request):

    if not request.user.is_superuser:
        return redirect("login")

    if request.method == "POST":

        action = request.POST.get("action")

        # Manual case archiving
        if action == "archive":

            case_id = request.POST.get("case_id")

            try:
                with transaction.atomic():

                    case = CaseRecord.objects.select_related(
                        "student",
                        "offense",
                        "handled_by"
                    ).get(
                        id=case_id,
                        is_archived=False
                    )

                    # A case is eligible for archiving only after resolution.
                    resolved_statuses = [
                        "Resolved",
                        "Resolved by DO",
                        "Resolved by Director",
                        "Resolved by Other",
                        "Resolved by CRD Director",
                        "Resolved by Discipline Board",
                        "Resolved by CODI",
                    ]

                    if case.status not in resolved_statuses:
                        messages.error(
                            request,
                            "Only resolved cases can be archived."
                        )
                        return redirect("archive_records")

                    # Mark the original case as archived.
                    case.is_archived = True
                    case.archived_at = timezone.now()
                    case.save(
                        update_fields=["is_archived", "archived_at"]
                    )

                    # Record the archiving in the case diary.
                    create_case_diary_entry(
                        case,
                        request.user,
                        f"Case record {case.case_number} was archived."
                    )

                    # Keep a snapshot for the existing archive template.
                    ArchivedRecord.objects.update_or_create(
                        case_id=case.case_number,
                        defaults={
                            "student_id": case.student.student_id,
                            "student_name": (
                                f"{case.student.first_name} "
                                f"{case.student.last_name}"
                            ),
                            "course": case.student.course,
                            "offense": case.offense.offense_name,
                            "status": case.status,
                            "date_reported": case.date_reported,
                            "archived_at": timezone.localdate(),
                            "notes": case.remarks,
                            "term": case.term,
                            "school_year": case.school_year,
                            "year_level": case.year_level,
                            "classification_override": (
                                case.classification_override
                            ),
                            "evidence_name": (
                                case.evidence.name
                                if case.evidence else ""
                            ),
                        }
                    )

                    AuditLog.objects.create(
                        user=request.user,
                        page="Archive Records",
                        action="Archive Case",
                        description=(
                            f"Archived case {case.case_number}. "
                            "Original case history was preserved."
                        )
                    )

                messages.success(
                    request,
                    f"Case {case.case_number} has been archived."
                )

            except CaseRecord.DoesNotExist:
                messages.error(
                    request,
                    "Case not found or it has already been archived."
                )

            except Exception:
                messages.error(
                    request,
                    "Unable to archive the case. Please check the case record."
                )

            return redirect("archive_records")

        # Restore archived case
        if action == "restore":

            archived_id = request.POST.get("archived_id")

            try:
                with transaction.atomic():

                    # Find the archived snapshot
                    archived = ArchivedRecord.objects.get(
                        id=archived_id
                    )

                    # Find the original case using its case number
                    case = CaseRecord.objects.get(
                        case_number=archived.case_id,
                        is_archived=True
                    )

                    # Restore the original case record
                    case.is_archived = False
                    case.archived_at = None
                    case.status = "Under Review"

                    # Reset resolution fields when restoring
                    case.resolution_authority = ""
                    case.appeal_resolution = ""

                    case.save(update_fields=[
                    "is_archived",
                    "archived_at",
                    "status",
                    "resolution_authority",
                    "appeal_resolution",
                    ]
                )

                    # Remove the archived snapshot
                    archived.delete()

                    # Record restoration in the case diary
                    create_case_diary_entry(
                        case,
                        request.user,
                        f"Case record {case.case_number} was restored from the archive."
                    )

                    # Create audit log
                    AuditLog.objects.create(
                        user=request.user,
                        page="Archive Records",
                        action="Restore Case",
                        description=(
                            f"Restored case {case.case_number} "
                            "to active Case Records."
                        )
                    )

                messages.success(
                    request,
                    f"Case {case.case_number} has been restored successfully."
                )

            except ArchivedRecord.DoesNotExist:
                messages.error(
                    request,
                    "Archived case not found or it has already been restored."
                )

            except CaseRecord.DoesNotExist:
                messages.error(
                    request,
                    "The original case record could not be found."
                )

            except (ValueError, TypeError):
                messages.error(
                    request,
                    "Invalid archived record ID."
                )

            except Exception:
                messages.error(
                    request,
                    "Unable to restore the case. Please try again."
                )

            return redirect("archive_records")

    # Separate active and archived case lists.
    case_records = list(
        CaseRecord.objects.filter(
            is_archived=False
        ).select_related(
            "student",
            "offense",
            "handled_by"
        )
    )

    case_records.sort(
        key=case_number_sort_key,
        reverse=True
    )

    archived_case_records = CaseRecord.objects.filter(
        is_archived=True
    ).select_related(
        "student",
        "offense",
        "handled_by"
    ).order_by("-archived_at")

    archived_records = ArchivedRecord.objects.all().order_by(
        "-archived_at"
    )

    return render(
        request,
        "core/archive_records.html",
        {
            "case_records": case_records,
            "archived_case_records": archived_case_records,
            "archived_records": archived_records,
            "settings": SystemSettings.objects.get_or_create(
                id=1
            )[0],
        }
    )




# =========================================================
# USER MANAGEMENT
# =========================================================

@login_required
def user_management(request):

    if account_is_inactive(request):
        return redirect("login")
    if not is_main_admin(request.user):
        return HttpResponseForbidden("Only the Main Admin can manage users.")

    User = get_user_model()

    if request.method == "POST":

        action = request.POST.get("action")
        user_id = request.POST.get("user_id")

        if action == "delete":

            try:

                user = User.objects.get(
                    id=user_id
                )

                # Never delete any Main Admin/superuser account.
                if user.is_superuser:
                    messages.error(
                        request,
                        "Main Admin accounts cannot be deleted."
                    )
                    return redirect("user_management")

                # Prevent deleting the currently logged-in account
                if user.id == request.user.id:

                    messages.error(
                        request,
                        (
                            "You cannot delete the account "
                            "you are currently using."
                        )
                    )

                    return redirect("user_management")

                username = user.username

                # Delete UserProfile first
                UserProfile.objects.filter(
                    user=user
                ).delete()

                # Delete the user account
                user.delete()

                # Create audit log
                AuditLog.objects.create(
                    user=request.user,
                    page="User Management",
                    action="Delete User",
                    description=(
                        f"Deleted user account {username}"
                    )
                )

                messages.success(
                    request,
                    (
                        f"User account {username} was "
                        "deleted successfully."
                    )
                )

            except User.DoesNotExist:

                messages.error(
                    request,
                    "User account not found."
                )

            return redirect("user_management")

    users = User.objects.all().order_by(
        "username"
    )

    new_activation_code = request.session.pop(
        "new_activation_code",
        None
    )

    new_activation_username = request.session.pop(
        "new_activation_username",
        None
    )

    return render(
        request,
        "core/user_management.html",
        {
            "users": users,
            "settings": SystemSettings.objects.get_or_create(
                id=1
            )[0],
            "new_activation_code": new_activation_code,
            "new_activation_username": new_activation_username,
        }
    )



def _permissions_from_post(request):
    allowed = {
        "student_records", "student_archive",
        "case_view", "case_full", "case_create",
        "report_dashboard", "case_report", "print_reports",
        "settings_login_background", "settings_theme_color",
        "settings_student_api_url",
    }
    selected = set()
    for field in ("permissions", "report_permissions", "settings_permissions"):
        selected.update(request.POST.getlist(field))
    # Also support checkboxes whose HTML name matches the permission key.
    selected.update(key for key in allowed if request.POST.get(key))
    # Radio selection used by the Case Record permission control.
    case_permission = request.POST.get("case_permission")
    if case_permission in {"case_view", "case_full", "case_create"}:
        selected.difference_update({"case_view", "case_full", "case_create"})
        selected.add(case_permission)
    # Some forms use a generic "case_record" checkbox for View Only.
    if request.POST.get("case_record") and not selected.intersection({"case_view", "case_full", "case_create"}):
        selected.add("case_view")
    return sorted(selected.intersection(allowed))


# =========================================================
# ADD USER
# =========================================================

@login_required
def add_user(request):
    if account_is_inactive(request):
        return redirect("login")
    if not is_main_admin(request.user):
        return HttpResponseForbidden("Only the Main Admin can add users.")

    if request.method == "POST":
        User = get_user_model()
        username = request.POST.get("username", "").strip()
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        role = "Staff"

        if not username:
            messages.error(request, "Username is required.")
            return redirect("add_user")

        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists.")
            return redirect("add_user")

        is_admin_role = role == "Administrator"

        # Generate a random 5-digit activation code
        activation_code = f"{secrets.randbelow(100000):05d}"

        # Create the account as inactive until activation
        new_user = User.objects.create_user(
            username=username,
            password=None,
            first_name=first_name,
            last_name=last_name,
            is_superuser=is_admin_role,
            is_staff=is_admin_role,
            is_active=False,
        )

        profile = UserProfile.objects.create(
            user=new_user,
            full_name=f"{first_name} {last_name}".strip(),
            role="Administrator" if is_admin_role else "Staff",
            activation_code_hash=make_password(activation_code),
            is_activated=False,
        )

        if not is_admin_role:
            profile.permissions = _permissions_from_post(request)
            profile.save(update_fields=["permissions"])

        AuditLog.objects.create(
            user=request.user,
            page="User Management",
            action="Create User",
            description=f"Created {profile.role} account {username}"
        )

        # Keep the code temporarily so it can be shown to the Main Admin
        request.session["new_activation_code"] = activation_code
        request.session["new_activation_username"] = username

        messages.success(
            request,
            f"{profile.role} account {username} was created successfully. "
            "Give the activation code to the account holder."
        )
        return redirect("user_management")

    return render(request, "core/add_user.html", {
        "settings": SystemSettings.objects.get_or_create(id=1)[0],
    })



# =========================================================
# EDIT USER
# =========================================================

@login_required
def edit_user(request, user_id):
    if account_is_inactive(request):
        return redirect("login")

    if not is_main_admin(request.user):
        return HttpResponseForbidden(
            "Only the Main Admin can edit users."
        )

    User = get_user_model()

    try:
        user = User.objects.get(id=user_id)
    except (User.DoesNotExist, ValueError, TypeError):
        messages.error(request, "User account not found.")
        return redirect("user_management")

    profile = get_profile(user)

    if request.method == "POST":
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        password = request.POST.get("password", "")

        # Preserve the account's existing role.
        is_admin_role = user.is_superuser

        user.first_name = first_name
        user.last_name = last_name

        if password:
            user.set_password(password)

        # Main Admin accounts must remain active.
        if is_admin_role:
            user.is_active = True
        else:
            status_value = request.POST.get(
                "is_active",
                request.POST.get("account_status", "")
            )

            if status_value:
                user.is_active = status_value.lower() in {
                    "1", "true", "active", "on", "yes"
                }

        user.save()

        # Create a profile if one does not exist.
        if profile is None:
            profile = UserProfile.objects.create(
                user=user,
                full_name=f"{first_name} {last_name}".strip(),
                role="Administrator" if is_admin_role else "Staff",
            )

        profile.full_name = f"{first_name} {last_name}".strip()
        profile.role = "Administrator" if is_admin_role else "Staff"

        if not is_admin_role:
            profile.permissions = _permissions_from_post(request)
        else:
            profile.permissions = []

        # Update profile picture only if a new image was selected.
        if request.FILES.get("image"):
            profile.image = request.FILES["image"]

        profile.save()

        AuditLog.objects.create(
            user=request.user,
            page="User Management",
            action="Edit User",
            description=f"Updated user account {user.username}"
        )

        messages.success(
            request,
            f"User account {user.username} was updated successfully."
        )

        return redirect("user_management")

    return render(
        request,
        "core/edit_user.html",
        {
            "account": user,
            "profile": profile,
            "settings": SystemSettings.objects.get_or_create(id=1)[0],
        }
    )


# =========================================================
# UNLOCK ACCOUNT
# =========================================================

@login_required
def unlock_account(request):

    if not request.user.is_superuser:
        return redirect("login")

    users = UserProfile.objects.select_related(
        "user"
    ).all().order_by(
        "user__username"
    )

    if request.method == "POST":

        user_id = request.POST.get(
            "user_id"
        )

        try:

            profile = UserProfile.objects.get(
                user_id=user_id
            )

            profile.locked = False
            profile.failed_login_attempts = 0
            profile.save()

            AuditLog.objects.create(
                user=request.user,
                page="Unlock Account",
                action="Unlock Account",
                description=(
                    f"Unlocked account for "
                    f"{profile.user.username}"
                )
            )

            messages.success(
                request,
                (
                    f"Account {profile.user.username} "
                    "has been unlocked."
                )
            )

        except UserProfile.DoesNotExist:

            messages.error(
                request,
                "User account not found."
            )

        return redirect(
            "unlock_account"
        )

    return render(
        request,
        "core/unlock_account.html",
        {
            "users": users,
            "settings": SystemSettings.objects.get_or_create(
                id=1
            )[0],
        }
    )


# =========================================================
# SETTINGS
# =========================================================

@login_required
def settings_view(request):
    if account_is_inactive(request):
        return redirect("login")

    is_admin = is_main_admin(request.user)
    granted = set()
    if not is_admin:
        profile = get_profile(request.user)
        if profile and profile.role == "Staff":
            granted = set(profile.permissions or [])
        if not granted.intersection({
            "settings_login_background", "settings_theme_color",
            "settings_student_api_url"
        }):
            return HttpResponseForbidden("You do not have permission to access Settings.")

    settings, _ = SystemSettings.objects.get_or_create(id=1)
    if request.method == "POST":
        changed = []

        if is_admin or "settings_theme_color" in granted:
            if "theme_color" in request.POST:
                settings.theme_color = request.POST.get("theme_color", "").strip()
                changed.append("theme color")

        if is_admin or "settings_student_api_url" in granted:
            if "student_api_url" in request.POST:
                settings.student_api_url = request.POST.get("student_api_url", "").strip()
                changed.append("student API URL")

        if (is_admin or "settings_login_background" in granted) and request.FILES.get("login_background"):
            uploaded_image = request.FILES["login_background"]

            if uploaded_image.size > 5 * 1024 * 1024:
                messages.error(request, "Image size must not exceed 5 MB.")
                return redirect("settings")

            try:
                from PIL import Image

                image_bytes = uploaded_image.read()
                image = Image.open(BytesIO(image_bytes))
                image.verify()

                content_type = uploaded_image.content_type or ""

                if content_type not in ["image/jpeg", "image/png", "image/webp"]:
                    messages.error(request, "Only JPG, PNG, and WEBP images are allowed.")
                    return redirect("settings")

                settings.login_background_data = image_bytes
                settings.login_background_content_type = content_type
                settings.login_background_filename = uploaded_image.name

                changed.append("login background")

            except Exception:
                messages.error(request, "Invalid image file. Please upload a valid JPG, PNG, or WEBP image.")
                return redirect("settings")

        if not changed:
            return HttpResponseForbidden("You do not have permission to change these settings.")

        settings.save()
        AuditLog.objects.create(
            user=request.user, page="Settings", action="Update Settings",
            description="Updated system settings: " + ", ".join(changed)
        )
        messages.success(request, "System settings have been updated successfully.")
        return redirect("settings")

    return render(request, "core/settings.html", {
    "settings": settings,
    "can_change_login_background": is_admin or "settings_login_background" in granted,
    "can_change_theme_color": is_admin or "settings_theme_color" in granted,
    "can_change_student_api_url": is_admin or "settings_student_api_url" in granted,
    "is_main_admin": is_admin,
    "student_api_preview": request.session.get("student_api_preview", []),
})

# =========================================================
# UPLOAD MOCK OBDMS STUDENT FILE
# =========================================================

@login_required
def upload_obdms_students(request):

    if account_is_inactive(request):
        return redirect("login")

    if request.method != "POST":
        return redirect("settings")

    if not (
        is_main_admin(request.user)
        or staff_has_permission(request.user, "settings_student_api_url")
    ):
        return HttpResponseForbidden(
            "You do not have permission to upload student data."
        )

    uploaded_file = request.FILES.get("obdms_student_file")

    if not uploaded_file:
        messages.error(request, "Please select an Excel file to upload.")
        return redirect("settings")

    if not uploaded_file.name.lower().endswith(".xlsx"):
        messages.error(request, "Only .xlsx Excel files are allowed.")
        return redirect("settings")

    try:
        workbook = load_workbook(
            uploaded_file,
            read_only=True,
            data_only=True
        )

        if "Students" not in workbook.sheetnames:
            workbook.close()
            messages.error(
                request,
                "The Excel file must contain a worksheet named Students."
            )
            return redirect("settings")

        sheet = workbook["Students"]

        headers = [
            str(cell.value).strip().lower() if cell.value else ""
            for cell in sheet[1]
        ]

        required_columns = [
            "student_id",
            "first_name",
            "middle_name",
            "last_name",
            "course",
            "program",
            "year_level",
            "sex",
        ]

        if not all(column in headers for column in required_columns):
            workbook.close()
            messages.error(
                request,
                "The Excel file has missing or incorrect column headers."
            )
            return redirect("settings")

        workbook.close()

        uploaded_file.seek(0)

        settings, _ = SystemSettings.objects.get_or_create(id=1)

        settings.obdms_student_file.save(
            uploaded_file.name,
            uploaded_file,
            save=True
        )

        AuditLog.objects.create(
            user=request.user,
            page="Settings",
            action="Upload Student Excel File",
            description=f"Uploaded student workbook: {uploaded_file.name}"
        )

        messages.success(
            request,
            "Student Excel file uploaded successfully."
        )

    except Exception as e:
        messages.error(
            request,
            f"Unable to upload the Excel file: {str(e)}"
        )

    return redirect("settings")

# =========================================================
# RETRIEVE STUDENTS FROM API
# =========================================================

@login_required
def retrieve_students_from_api(request):

    if request.method != "POST":
        return redirect("settings")

    if account_is_inactive(request):
        return redirect("login")

    if not (
        is_main_admin(request.user)
        or staff_has_permission(request.user, "settings_student_api_url")
    ):
        return HttpResponseForbidden(
            "You do not have permission to retrieve student records."
        )

    try:
        from django.test import RequestFactory
        import json

        # Call the local mock Student API directly.
        api_request = RequestFactory().get("/api/mock-students/")
        api_response = mock_student_api(api_request)

        if api_response.status_code != 200:
            error_data = json.loads(api_response.content.decode("utf-8"))
            messages.error(
                request,
                error_data.get(
                    "error",
                    "Unable to retrieve student records."
                )
            )
            return redirect("settings")

        data = json.loads(api_response.content.decode("utf-8"))
        students = data.get("students") if isinstance(data, dict) else None

        if not isinstance(students, list):
            messages.error(
                request,
                "The API response does not contain a valid students list."
            )
            return redirect("settings")

        required_fields = [
            "student_id",
            "first_name",
            "last_name",
            "course",
            "year_level",
        ]

        preview_students = []

        for student in students:

            if not isinstance(student, dict):
                continue

            if not all(
                str(student.get(field) or "").strip()
                for field in required_fields
            ):
                continue

            preview_students.append({
                "student_id": str(student.get("student_id")).strip(),
                "first_name": str(student.get("first_name") or "").strip(),
                "middle_name": str(student.get("middle_name") or "").strip(),
                "last_name": str(student.get("last_name") or "").strip(),
                "course": str(student.get("course") or "").strip(),
                "program": str(student.get("program") or "").strip(),
                "year_level": str(student.get("year_level") or "").strip(),
                "sex": str(student.get("sex") or "").strip(),
            })

        if not preview_students:
            messages.error(
                request,
                "No valid student records were found in the API response."
            )
            return redirect("settings")

        request.session["student_api_preview"] = preview_students

        messages.success(
            request,
            f"Successfully retrieved {len(preview_students)} student records. "
            "Please review the preview before importing."
        )

    except ValueError:
        messages.error(
            request,
            "The Student API did not return valid JSON data."
        )

    except Exception as e:
        messages.error(
            request,
            f"Unable to retrieve student records: {str(e)}"
        )

    return redirect("settings")

# =========================================================
# IMPORT STUDENTS FROM API PREVIEW
# =========================================================

@login_required
def import_students_from_api(request):

    if request.method != "POST":
        return redirect("settings")

    if account_is_inactive(request):
        return redirect("login")

    if not (
        is_main_admin(request.user)
        or staff_has_permission(request.user, "settings_student_api_url")
    ):
        return HttpResponseForbidden(
            "You do not have permission to import student records."
        )

    preview_students = request.session.get(
        "student_api_preview",
        []
    )

    if not preview_students:
        messages.error(
            request,
            "No student records are available to import. Please retrieve students first."
        )
        return redirect("settings")

    imported_count = 0
    skipped_count = 0

    try:

        for student in preview_students:

            student_id = str(
                student.get("student_id") or ""
            ).strip()

            first_name = str(
                student.get("first_name") or ""
            ).strip()

            last_name = str(
                student.get("last_name") or ""
            ).strip()

            course = str(
                student.get("course") or ""
            ).strip()

            year_level = str(
                student.get("year_level") or ""
            ).strip()

            if not all([
                student_id,
                first_name,
                last_name,
                course,
                year_level
            ]):
                skipped_count += 1
                continue

            student_record, created = Student.objects.get_or_create(
                student_id=student_id,
                defaults={
                    "first_name": first_name,
                    "middle_name": str(
                        student.get("middle_name") or ""
                    ).strip(),
                    "last_name": last_name,
                    "course": course,
                    "program": str(
                        student.get("program") or ""
                    ).strip(),
                    "year_level": year_level,
                    "sex": str(
                        student.get("sex") or ""
                    ).strip(),
                }
            )

            if created:
                imported_count += 1
            else:
                skipped_count += 1

        AuditLog.objects.create(
            user=request.user,
            page="Settings",
            action="Import Student Records",
            description=(
                f"Imported {imported_count} new student records "
                f"from the Student API. "
                f"{skipped_count} existing or invalid records were skipped."
            )
        )

        request.session.pop("student_api_preview", None)

        messages.success(
            request,
            f"Student import completed. "
            f"{imported_count} new records imported and "
            f"{skipped_count} existing or invalid records skipped."
        )

    except Exception as e:

        messages.error(
            request,
            f"An error occurred while importing student records: {str(e)}"
        )

    return redirect("settings")


# =========================================================
# CASE RECORD DETAILS
# =========================================================

@login_required
def case_record_details(request, case_id):
    if account_is_inactive(request):
        return redirect("login")

    if not is_main_admin(request.user) and not staff_has_any_case_permission(request.user):
        return HttpResponseForbidden("You do not have permission to view case details.")

    case = get_object_or_404(
        CaseRecord.objects.select_related("student", "offense", "handled_by"),
        id=case_id,
    )

    can_edit_cases = (
        is_main_admin(request.user)
        or staff_has_permission(request.user, "case_full")
    )

    can_view_student_records = (
        is_main_admin(request.user)
        or staff_has_permission(request.user, "student_records")
    )
    can_view_student_archive = (
        is_main_admin(request.user)
        or staff_has_permission(request.user, "student_archive")
    )

    profile = get_profile(request.user)

    print("USER:", request.user.username)
    print("IS SUPERUSER:", request.user.is_superuser)
    print("SAVED PERMISSIONS:", profile.permissions if profile else None)
    print("CAN VIEW STUDENT RECORDS:", can_view_student_records)
    print("CAN VIEW STUDENT ARCHIVE:", can_view_student_archive)

    # Add a sanction
    if request.method == "POST":
        if request.POST.get("action") == "add_sanction":
            if not can_edit_cases:
                return HttpResponseForbidden(
                    "You do not have permission to add sanctions."
                )

            if case.is_archived:
                messages.error(
                    request,
                    "Sanctions cannot be added to archived cases."
                )
                return redirect("case_record_details", case_id=case.id)

            sanction_name = request.POST.get("sanction_name", "").strip()
            remarks = request.POST.get("sanction_remarks", "").strip()

            if not sanction_name:
                messages.error(request, "Please enter a sanction.")
                return redirect("case_record_details", case_id=case.id)

            CaseSanction.objects.create(
                case=case,
                sanction_name=sanction_name,
                remarks=remarks,
            )

            create_case_diary_entry(
                case,
                request.user,
                f"Sanction added: {sanction_name}."
            )

            AuditLog.objects.create(
                user=request.user,
                page="Case Record Details",
                action="Added Case Sanction",
                description=(
                    f"Added sanction '{sanction_name}' "
                    f"to case {case.case_number}."
                )
            )

            messages.success(request, "Sanction added successfully.")
            return redirect("case_record_details", case_id=case.id)

    settings = SystemSettings.objects.get_or_create(id=1)[0]

    context = {
        "case": case,
        "settings": settings,
        "can_view_student_records": can_view_student_records,
        "can_view_student_archive": can_view_student_archive,
        "case_count": CaseRecord.objects.filter(
            student=case.student
        ).count(),
        "sanctions": case.sanctions.all().order_by("-date_added"),
        "diary_entries": case.diary_entries.order_by("-entry_date"),
        "attachments": case.attachments.order_by("-date_added"),
        "can_edit_cases": can_edit_cases,
        "can_add_case_information": (
            is_main_admin(request.user)
        or staff_has_permission(request.user, "case_full")
    ),
    }

    return render(
        request,
        "core/case_record_details.html",
        context
    )   

# =========================================================
# CASE RECORD EVIDENCE
# =========================================================

@login_required
def case_record_evidence(request, case_id):
    if account_is_inactive(request):
        return redirect("login")
    if not is_main_admin(request.user) and not staff_has_any_case_permission(request.user):
        return HttpResponseForbidden("You do not have permission to access this file.")

    try:
        case = CaseRecord.objects.get(id=case_id)
    except CaseRecord.DoesNotExist:
        raise Http404("Case record not found.")
    if not case.evidence:
        raise Http404("Evidence file not found.")
    return FileResponse(
        case.evidence.open("rb"),
        as_attachment=False,
        filename=os.path.basename(case.evidence.name),
    )


@login_required
def audit_log_view(request):
    if account_is_inactive(request):
        return redirect("login")
    if not is_main_admin(request.user):
        return redirect("login")

    audit_logs = AuditLog.objects.select_related(
        "user"
    ).order_by(
        "-date_created"
    )

    return render(
        request,
        "core/audit_log.html",
        {
            "audit_logs": audit_logs,
            "settings": SystemSettings.objects.get_or_create(
                id=1
            )[0],
        }
    )

# =========================================================
# CASE ATTACHMENT VIEW
# =========================================================

@login_required
def case_attachment_view(request, attachment_id):
    if account_is_inactive(request):
        return redirect("login")
    attachment = get_object_or_404(
        CaseAttachment.objects.select_related("case"), id=attachment_id
    )
    if not is_main_admin(request.user) and not staff_has_any_case_permission(request.user):
        return HttpResponseForbidden("You do not have permission to access this file.")
    if not attachment.file:
        raise Http404("Attachment file not found.")
    return FileResponse(
        attachment.file.open("rb"),
        as_attachment=False,
        filename=os.path.basename(attachment.file.name),
    )

def login_background_image(request):
    settings = SystemSettings.objects.filter(id=1).first()

    if not settings or not settings.login_background_data:
        raise Http404("Login background not found.")

    response = HttpResponse(
        bytes(settings.login_background_data),
        content_type=settings.login_background_content_type or "image/png"
    )

    response["Cache-Control"] = "no-store"

    return response