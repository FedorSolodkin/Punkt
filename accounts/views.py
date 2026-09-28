from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .services import consume_login_token, safe_next_path


@require_http_methods(['GET'])
def login_page(request):
    """GET /login?t=... — ТЗ §4.

    Без токена показывает инструкцию написать /web боту. GET не
    расходует токен: только показывает кнопку входа.
    """
    token = request.GET.get('t', '')
    return render(request, 'accounts/login.html', {'token': token})


@require_http_methods(['POST'])
def session_create(request):
    """POST /session — единственное применение токена (ТЗ §4, §5.4)."""
    token = request.POST.get('token', '')
    user, next_path = consume_login_token(token)
    if user is None:
        return render(
            request, 'accounts/login.html',
            {'token': '', 'error': 'Ссылка недействительна или устарела. Запросите новую командой /web в боте.'},
            status=400,
        )
    auth_login(request, user)
    return redirect(safe_next_path(next_path))


@require_http_methods(['POST'])
def session_destroy(request):
    """POST /logout — ТЗ §4."""
    auth_logout(request)
    return redirect('/login')
