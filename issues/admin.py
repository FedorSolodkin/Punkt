from django.contrib import admin

from .models import Issue, IssueEvent, Photo, Site, Zone


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ('name', 'timezone')


@admin.register(Zone)
class ZoneAdmin(admin.ModelAdmin):
    list_display = ('name', 'site', 'active')
    list_filter = ('site', 'active')


class ReadOnlyAdmin(admin.ModelAdmin):
    """Предметные записи доступны в админке только для чтения (ТЗ §1.1):
    все изменения обязаны идти через issues.services, чтобы соблюдались
    инварианты, версии и идемпотентность.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Issue)
class IssueAdmin(ReadOnlyAdmin):
    list_display = ('id', 'zone', 'assignee', 'status', 'due_date', 'version', 'created_at')
    list_filter = ('status', 'zone')
    search_fields = ('id', 'public_id', 'description')


@admin.register(Photo)
class PhotoAdmin(ReadOnlyAdmin):
    list_display = ('id', 'issue', 'kind', 'author', 'created_at')
    list_filter = ('kind',)


@admin.register(IssueEvent)
class IssueEventAdmin(ReadOnlyAdmin):
    list_display = ('id', 'issue', 'action', 'actor', 'created_at')
    list_filter = ('action',)
