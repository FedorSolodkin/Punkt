from django.contrib import admin

from .models import BotDraft, BotState


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(BotState)
class BotStateAdmin(ReadOnlyAdmin):
    list_display = ('id', 'next_offset')

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BotDraft)
class BotDraftAdmin(ReadOnlyAdmin):
    list_display = ('user', 'step', 'started_at', 'expires_at')

    def has_delete_permission(self, request, obj=None):
        return True
