from django.urls import path
from . import views


urlpatterns = [
    path("", views.login_view, name="login"),

    path(
        "activate-account/",
        views.activate_account,
        name="activate_account"
    ),

    path(
        "api/mock-students/",
        views.mock_student_api,
        name="mock_student_api"
    ),

    path(
        "student-records/",
        views.student_records,
        name="student_records"
    ),

    path(
        "edit-student/<int:student_id>/",
        views.edit_student,
        name="edit_student"
    ),

    path(
        "admin-dashboard/",
        views.admin_dashboard,
        name="admin_dashboard"
    ),

    path(
        "staff-dashboard/",
        views.staff_dashboard,
        name="staff_dashboard"
    ),

    path(
        "case-record/",
        views.case_record,  
        name="case_record"
    ),

    path(
        "case-record/<int:case_id>/",
        views.case_record_details,
        name="case_record_details"
    ),

    path(
        "case-record/<int:case_id>/evidence/",
        views.case_record_evidence,
        name="case_record_evidence"
    ),

    path(
        "offense-master-list/",
        views.offense_master_list,
        name="offense_master_list"
    ),

    path(
        "offense-master-list/<int:offense_id>/save-rules/",
        views.save_offense_rules,
        name="save_offense_rules",
    ),

    path(
        "generate-reports/",
        views.generate_reports,
        name="generate_reports"
    ),

    path(
        "archive-records/",
        views.archive_records,
        name="archive_records"
    ),

    path(
        "user-management/",
        views.user_management,
        name="user_management"
    ),

    path(
        "unlock-account/",
        views.unlock_account,
        name="unlock_account"
    ),

    path(
        "settings/",
        views.settings_view,
        name="settings"
    ),

    path(
        "settings/upload-obdms-students/",
        views.upload_obdms_students,
        name="upload_obdms_students"
    ),

    path(
        "settings/retrieve-students/",
         views.retrieve_students_from_api,
        name="retrieve_students_from_api"
    ),

    path(
        "settings/retrieve-students/",
        views.retrieve_students_from_api,
        name="retrieve_students_from_api"
    ),

    path(
        "settings/import-students/",
        views.import_students_from_api,
        name="import_students_from_api"
    ),


    path(
        "add-user/",
        views.add_user,
        name="add_user"
    ),

    path(
        "edit-user/<int:user_id>/",
        views.edit_user,
        name="edit_user"
    ),

    path(
        "print-report/",
        views.print_report,
        name="print_report"
    ),

    path(
        "audit-log/",
        views.audit_log_view,
        name="audit_log"
    ),

    path(
        "logout/",
        views.logout_view,
        name="logout"
    ),

    path(
        "student-archive/",
        views.student_archive,
        name="student_archive"
    ),

    path(
        "case-attachment/<int:attachment_id>/",
        views.case_attachment_view,
        name="case_attachment_view"
    ),
]