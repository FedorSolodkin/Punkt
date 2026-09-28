from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm
from django.contrib.auth.models import Group

from .models import LoginToken, User

# Группы — стандартный механизм прав Django, в PUNKT не используется:
# роли (инженер/исполнитель/руководитель) — отдельное поле User.role
# (ТЗ §1.1), а не группы с наборами permissions.
admin.site.unregister(Group)


class PunktUserCreationForm(AdminUserCreationForm):
    """Обычные участники входят только по ссылке из бота (ТЗ §1.1, §5.4)
    и пароль им не нужен — поэтому по умолчанию выключаем переключатель
    «Аутентификация по паролю». Включать вручную нужно только технической
    роли администратора, если ей требуется прямой вход в /admin.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['usable_password'].initial = 'false'


def _without_groups(fieldsets):
    """Убирает поле groups (не используется — см. unregister(Group) выше),
    оставляя user_permissions: это рабочий способ делегировать доступ в
    /admin без выдачи полного суперпользователя."""
    result = []
    for name, opts in fieldsets:
        opts = dict(opts)
        opts['fields'] = tuple(f for f in opts['fields'] if f != 'groups')
        result.append((name, opts))
    return tuple(result)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Администратор настраивает участников и роли здесь (ТЗ §1.1)."""

    add_form = PunktUserCreationForm
    fieldsets = _without_groups(DjangoUserAdmin.fieldsets) + (
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
