from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', RedirectView.as_view(url='/issues', permanent=False)),
    path('', include('accounts.urls')),
    path('', include('issues.urls')),
    path('', include('reports.urls')),
]
