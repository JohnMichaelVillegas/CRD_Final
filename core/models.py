from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Student(models.Model):
    student_id = models.CharField(max_length=50, unique=True)
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True, default="")
    last_name = models.CharField(max_length=100)
    image = models.ImageField(
        upload_to="student_profiles/",
        blank=True,
        null=True
    )
    course = models.CharField(max_length=100)
    year_level = models.CharField(max_length=20)
    program = models.CharField(max_length=100, blank=True)
    sex = models.CharField(max_length=20, blank=True)

    STATUS_CHOICES = [
    ("Active", "Active"),
    ("LOA", "LOA"),
    ("AWOL", "AWOL"),
    ("Graduated", "Graduated"),
]

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="Active"
    )
    graduation_date = models.DateField(
        blank=True,
        null=True
    )

    mother_name = models.CharField(max_length=200, blank=True, default="")
    mother_address = models.TextField(blank=True, default="")
    mother_email = models.EmailField(blank=True, default="")
    mother_contact_no = models.CharField(max_length=30, blank=True, default="")

    father_name = models.CharField(max_length=200, blank=True, default="")
    father_address = models.TextField(blank=True, default="")
    father_email = models.EmailField(blank=True, default="")
    father_contact_no = models.CharField(max_length=30, blank=True, default="")

    guardian_name = models.CharField(max_length=200, blank=True, default="")
    guardian_address = models.TextField(blank=True, default="")
    guardian_email = models.EmailField(blank=True, default="")
    guardian_contact_no = models.CharField(max_length=30, blank=True, default="")

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class ArchivedStudent(models.Model):
    student_id = models.CharField(max_length=50, unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    course = models.CharField(max_length=100)
    program = models.CharField(max_length=100, blank=True)
    year_level = models.CharField(max_length=20)
    sex = models.CharField(max_length=20, blank=True)
    graduation_date = models.DateField(blank=True, null=True)
    archived_at = models.DateTimeField(auto_now_add=True)

    mother_name = models.CharField(max_length=200, blank=True, default="")
    mother_address = models.TextField(blank=True, default="")
    mother_email = models.EmailField(blank=True, default="")
    mother_contact_no = models.CharField(max_length=30, blank=True, default="")

    father_name = models.CharField(max_length=200, blank=True, default="")
    father_address = models.TextField(blank=True, default="")
    father_email = models.EmailField(blank=True, default="")
    father_contact_no = models.CharField(max_length=30, blank=True, default="")

    guardian_name = models.CharField(max_length=200, blank=True, default="")
    guardian_address = models.TextField(blank=True, default="")
    guardian_email = models.EmailField(blank=True, default="")
    guardian_contact_no = models.CharField(max_length=30, blank=True, default="")

    def __str__(self):
        return f"{self.student_id} - {self.first_name} {self.last_name}"



class Offense(models.Model):

    PRIOR_WARNING_CATEGORY_CHOICES = [
        ("", "Not a prior-warning category"),
        ("attire", "Inappropriate attire inside campus"),
        ("dress_code", "Program dress code / good grooming"),
        ("enter_without_id", "Entering campus without Benilde ID"),
        ("not_wearing_id", "Failure to wear Benilde ID inside campus"),
        ("dormitory", "Benilde dormitory violations"),
        ("parking", "Benilde parking rules and regulations"),
    ]

    ACCUMULATION_TYPE_CHOICES = [
        ("accumulating", "Accumulating Infraction"),
        ("direct_minor", "Direct Minor Offense"),
        ("direct_major", "Direct Major Offense"),
    ]

    offense_code = models.CharField(max_length=50, unique=True)
    offense_name = models.CharField(max_length=200)
    severity = models.CharField(max_length=20)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, default="Active")
    last_updated = models.DateField(auto_now=True)

    prior_warning_category = models.CharField(
        max_length=30,
        choices=PRIOR_WARNING_CATEGORY_CHOICES,
        blank=True,
        default="",
    )

    accumulation_type = models.CharField(
        max_length=20,
        choices=ACCUMULATION_TYPE_CHOICES,
        default="direct_minor",
    )

    def __str__(self):
        return self.offense_name


class CaseRecord(models.Model):

    CASE_STATUS_CHOICES = [
    ("Reported", "Reported"),
    ("Preliminary", "Preliminary"),
    ("Notice of Complaint Sent", "Notice of Complaint Sent"),
    ("Under Review", "Under Review"),

    ("Resolved by DO", "Resolved by DO"),
    ("Resolved by Director", "Resolved by Director"),
    ("Resolved by Other", "Resolved by Other"),

    ("Resolved by CRD Director", "Resolved by CRD Director"),
    ("Resolved by Discipline Board", "Resolved by Discipline Board"),
    ("Resolved by CODI", "Resolved by CODI"),

    ("Resolved", "Resolved"),
]

    RESOLUTION_AUTHORITY_CHOICES = [
        ("", "Not yet resolved"),
        ("CRD Director", "CRD Director"),
        ("Discipline Board", "Discipline Board"),
        ("CODI", "Committee on Decorum and Investigation"),
    ]

    APPEAL_RESOLUTION_CHOICES = [
        ("", "Not applicable"),
        ("VP LMSL", "Appeal Resolved by VP LMSL"),
        ("Chancellor", "Appeal Resolved by the Chancellor"),
    ]

    FORMATIVE_STATUS_CHOICES = [
        ("Pending", "Pending"),
        ("Completed", "Completed"),
    ]

    ID_VIOLATION_CHOICES = [
        ("None", "None"),
        ("Lost ID", "Lost ID"),
        ("Left ID", "Left ID"),
        ("Other", "Other"),
    ]

    case_number = models.CharField(max_length=50, unique=True)

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE
    )

    offense = models.ForeignKey(
        Offense,
        on_delete=models.CASCADE
    )

    is_active = models.BooleanField(default=True)

    date_reported = models.DateField()
    date_filed = models.DateField(default=timezone.localdate)

    term = models.CharField(max_length=20)
    school_year = models.CharField(max_length=20)
    year_level = models.CharField(max_length=20)

    status = models.CharField(
        max_length=50,
        choices=CASE_STATUS_CHOICES,
        default="Reported"
    )

    formative_status = models.CharField(
        max_length=20,
        choices=FORMATIVE_STATUS_CHOICES,
        default="Pending"
    )

    campus = models.CharField(
        max_length=100,
        blank=True,
        default=""
    )

    incident_location = models.CharField(
        max_length=255,
        blank=True,
        default=""
    )

    handled_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handled_cases"
    )
    
    resolution_authority = models.CharField(
        max_length=100,
        choices=RESOLUTION_AUTHORITY_CHOICES,
        blank=True,
        default=""
    )

    appeal_resolution = models.CharField(
        max_length=100,
        choices=APPEAL_RESOLUTION_CHOICES,
        blank=True,
        default=""
    )

    id_violation = models.CharField(
        max_length=30,
        choices=ID_VIOLATION_CHOICES,
        default="None"
    )

    id_violation_remarks = models.TextField(
        blank=True,
        default=""
    )

    remarks = models.TextField(blank=True)

    sanction_remarks = models.TextField(
        blank=True,
        default=""
    )

    # Retained for compatibility with existing evidence files
    evidence = models.FileField(
        upload_to="evidence/",
        blank=True,
        null=True
    )

    classification_override = models.CharField(
        max_length=30,
        default="Auto"
    )

    is_archived = models.BooleanField(default=False)

    archived_at = models.DateTimeField(
        null=True,
        blank=True
    )

    def __str__(self):
        return self.case_number


class CaseSanction(models.Model):
    case = models.ForeignKey(
        CaseRecord,
        on_delete=models.CASCADE,
        related_name="sanctions"
    )

    sanction_name = models.CharField(max_length=200)

    remarks = models.TextField(
        blank=True,
        default=""
    )

    date_added = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.case.case_number} - {self.sanction_name}"


class CaseDiaryEntry(models.Model):
    case = models.ForeignKey(
        CaseRecord,
        on_delete=models.CASCADE,
        related_name="diary_entries"
    )

    entry_date = models.DateTimeField(default=timezone.now)

    note = models.TextField()

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    date_created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Diary entry - {self.case.case_number}"


class CaseAttachment(models.Model):
    case = models.ForeignKey(
        CaseRecord,
        on_delete=models.CASCADE,
        related_name="attachments"
    )

    file = models.FileField(upload_to="case_attachments/")

    file_name = models.CharField(
        max_length=255,
        blank=True,
        default=""
    )

    date_added = models.DateTimeField(auto_now_add=True)

    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    def __str__(self):
        return f"Attachment - {self.case.case_number}"


class ArchivedRecord(models.Model):
    case_id = models.CharField(max_length=50, unique=True)
    student_id = models.CharField(max_length=50)
    student_name = models.CharField(max_length=200)
    course = models.CharField(max_length=100)
    offense = models.CharField(max_length=200)
    status = models.CharField(max_length=50)
    date_reported = models.DateField()
    archived_at = models.DateField()
    notes = models.TextField(blank=True)

    term = models.CharField(max_length=20, blank=True)
    school_year = models.CharField(max_length=20, blank=True)
    year_level = models.CharField(max_length=20, blank=True)

    classification_override = models.CharField(
        max_length=30,
        default="Auto"
    )

    evidence_name = models.CharField(
        max_length=500,
        blank=True
    )

    def __str__(self):
        return self.case_id


class AuditLog(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    page = models.CharField(max_length=100, blank=True, default="")
    action = models.CharField(max_length=100)
    description = models.TextField()
    date_created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.action



class UserProfile(models.Model):
    PERMISSION_CHOICES = [
        ("student_records", "Student Records"),
        ("student_archive", "Student Archive"),

        # Case Record access levels
        ("case_view", "Case Record - View Only"),
        ("case_full", "Case Record - Full Access"),
        ("case_create", "Case Record - View and Create"),
        ("case_information", "Case Record - Add Case Information"),

        # Individual report permissions
        ("report_dashboard", "Reports - View Dashboard"),
        ("case_report", "Reports - View Case Report"),
        ("print_reports", "Reports - Print Selected Cases"),

        # Individual System Settings permissions
        ("settings_login_background", "Settings - Login Background"),
        ("settings_theme_color", "Settings - Theme Color"),
        ("settings_student_api_url", "Settings - Student API URL"),
    ]

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE
    )

    full_name = models.CharField(
        max_length=200,
        blank=True
    )

    role = models.CharField(
        max_length=20,
        default="Staff"
    )

    google_linked = models.BooleanField(default=False)

    image = models.ImageField(
        upload_to="profile_images/",
        blank=True,
        null=True
    )

    # Permissions individually assigned by the Main Admin
    permissions = models.JSONField(
        default=list,
        blank=True
    )

    # Login security
    locked = models.BooleanField(default=False)
    failed_login_attempts = models.IntegerField(default=0)

    # Account activation
    activation_code_hash = models.CharField(
        max_length=128,
        blank=True,
        default=""
    )

    is_activated = models.BooleanField(default=True)    

    def __str__(self):
        return self.user.username


class SystemSettings(models.Model):
    login_background = models.ImageField(
        upload_to="login_backgrounds/",
        blank=True,
        null=True
    )

    theme_color = models.CharField(
        max_length=20,
        default="#15803d"
    )

    student_api_url = models.URLField(blank=True)

    obdms_student_file = models.FileField(
        upload_to="obdms_uploads/",
        blank=True,
        null=True
    )

    def __str__(self):
        return "CRD System Settings"


class AccumulationRule(models.Model):

    CLASSIFICATION_CHOICES = [
        ("Infraction", "Infraction"),
        ("Minor Offense", "Minor Offense"),
        ("Major Offense", "Major Offense"),
    ]

    offense = models.ForeignKey(
        Offense,
        on_delete=models.CASCADE,
        related_name="accumulation_rules",
    )

    instance_range = models.CharField(max_length=50)

    classification = models.CharField(
        max_length=30,
        choices=CLASSIFICATION_CHOICES,
    )

    action_note = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return (
            f"{self.offense.offense_name} - "
            f"{self.instance_range}"
        )
