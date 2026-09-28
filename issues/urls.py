from django.urls import path

from . import views

urlpatterns = [
    path('issues', views.registry, name='issue_registry'),
    path('issues/<uuid:public_id>', views.issue_detail, name='issue_detail'),
    path('issues/<uuid:public_id>/submit', views.issue_submit, name='issue_submit'),
    path('issues/<uuid:public_id>/review', views.issue_review, name='issue_review'),
    path('issues/<uuid:public_id>/edit', views.issue_edit, name='issue_edit'),
    path('photos/<uuid:photo_id>', views.photo_view, name='photo_view'),
]
