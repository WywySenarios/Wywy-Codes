"""URL routing for the orchestrator API."""

from django.urls import path

from apps.orchestrator import views

app_name = "orchestrator"

urlpatterns = [
    path("api/pipelines/", views.api_pipelines, name="api-pipelines"),
    path("api/pipelines/blocked/", views.api_list_blocked_pipelines, name="api-blocked"),
    path("api/pipelines/<uuid:pipeline_id>/", views.api_pipeline_detail, name="api-detail"),
    path("api/pipelines/<uuid:pipeline_id>/files/", views.api_pipeline_files, name="api-files"),
    path("api/pipelines/<uuid:pipeline_id>/respond/", views.api_respond, name="api-respond"),
    path("api/pipelines/<uuid:pipeline_id>/abort/", views.api_abort, name="api-abort"),
    path("api/pipelines/<uuid:pipeline_id>/logs/", views.api_log_files, name="api-log-files"),
    path("api/pipelines/<uuid:pipeline_id>/logs/entries/<str:log_filename>/", views.api_log_entries, name="api-log-entries"),
]
