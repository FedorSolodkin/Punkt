from django.urls import path

from . import views

urlpatterns = [
    path('login', views.login_page, name='login'),
    path('session', views.session_create, name='session_create'),
    path('logout', views.session_destroy, name='logout'),
]
