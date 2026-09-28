from django.urls import path

from . import views

urlpatterns = [
    path('report.pdf', views.report_pdf, name='report_pdf'),
]
