from django.contrib import admin
from .models import (
    Student,
    ArchivedStudent,
    Offense,
    CaseRecord,
    ArchivedRecord,
    AuditLog,
    UserProfile,
    SystemSettings,
)


admin.site.register(Student)
admin.site.register(ArchivedStudent)
admin.site.register(Offense)
admin.site.register(CaseRecord)
admin.site.register(ArchivedRecord)
admin.site.register(AuditLog)
admin.site.register(UserProfile)
admin.site.register(SystemSettings)