from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import LoginToken, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Администратор настраивает участников и роли здесь (ТЗ §1.1)."""

    fieldsets = DjangoUserAdmin.fieldsets + (
        ('PUNKT', {'fields': ('site', 'telegram_id', 'display_name', 'role')}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ('PUNKT', {'fields': ('site', 'telegram_id', 'display_name', 'role')}),
    )
    list_display = ('username', 'display_name', 'role', 'site', 'telegram_id', 'is_active', 'is_staff')
    list_filter = ('role', 'site', 'is_active')
    search_fields = ('username', 'display_name', 'telegram_id')


@admin.register(LoginToken)
class LoginTokenAdmin(admin.ModelAdmin):
    """Только просмотр — токены не редактируются вручную."""

    list_display = ('user', 'next_path', 'created_at', 'expires_at', 'used_at')
    list_filter = ('user',)
    readonly_fields = [f.name for f in LoginToken._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return True
