from django.contrib import admin
from .models import PhotographerProfile, License, Wedding, Photo


@admin.register(PhotographerProfile)
class PhotographerProfileAdmin(admin.ModelAdmin):
    list_display = ("business_name", "user", "phone")


@admin.register(License)
class LicenseAdmin(admin.ModelAdmin):
    list_display = ("key", "photographer", "plan", "is_active", "expires_at")
    list_filter = ("plan", "is_active")
    search_fields = ("key", "photographer__business_name")

@admin.register(Wedding)
class WeddingAdmin(admin.ModelAdmin):
    list_display = (
        "event_name",
        "bride_name",
        "groom_name",
        "wedding_date",
        "photographer",
    )

    list_filter = ("wedding_date",)
    search_fields = (
        "event_name",
        "bride_name",
        "groom_name",
    )

@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = ("id", "wedding", "uploaded_at")
    list_filter = ("wedding",)