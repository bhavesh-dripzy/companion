from django.contrib import admin
from django.urls import include, path
from app.views import food_ui

urlpatterns = [
    path("", food_ui, name="food-ui"),
    path("admin/", admin.site.urls),
    path("api/", include("app.routes")),
]
