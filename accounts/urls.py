from django.urls import path
from . import views
from django.contrib.auth.views import LogoutView

urlpatterns = [
    path("login/", views.photographer_login, name="photographer_login"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("activate-license/",views.activate_license, name="activate_license"),
    path("create-wedding/",views.create_wedding,name="create_wedding"),
    path("wedding/<int:wedding_id>/upload-photos/",views.upload_photos,name="upload_photos"),
    path("wedding/<int:wedding_id>/sync-drive/",views.sync_drive_photos, name = "sync_drive_photos"),

    path("wedding/<int:wedding_id>/",views.wedding_gallery,name="wedding_gallery"),
    path(
    "wedding/<int:wedding_id>/guest-favorites/",
    views.guest_favorites,
    name="guest_favorites"
),
    path("face-test/",views.face_test,name="face_test"),
    path("face-match-test/<int:wedding_id>/",views.face_match_test,name="face_match_test"),
    path("guest-match/<int:wedding_id>/",views.guest_match,name="guest_match"),
    path("guest-match/create-guest/", views.create_guest, name="create_guest"),
    path("guest-match/favorite/",views.add_favorite, name="add_favorite"),
    path("guest-match/photos/<int:wedding_id>/",views.guest_wedding_photos,name="guest_wedding_photos"),
    path("branding/",views.photographer_branding,name="photographer_branding"),
    path("wedding/<int:wedding_id>/delete/",views.delete_wedding,name="delete_wedding"),
    path("photo/<int:photo_id>/delete/",views.delete_photo,name="delete_photo"),
    path("logout/",LogoutView.as_view(next_page="photographer_login"),name="logout"),
    path("drive/", views.google_drive, name="google_drive"),
    path("drive/<int:account_id>/folders/", views.google_drive_folders,   name="google_drive_folders"),
    path("drive/<int:account_id>/disconnect/",views.disconnect_google_drive, name="disconnect_google_drive"),
    path("drive/connect/", views.google_drive_connect, name="google_drive_connect"),
    path("drive/callback/", views.google_drive_callback, name="google_drive_callback"),
    path("drive/<int:account_id>/connect-folder/",views.connect_drive_folder,
    name="connect_drive_folder"
),]