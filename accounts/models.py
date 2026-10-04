from django.db import models
from django.contrib.auth.models import User


class PhotographerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    phone = models.CharField(max_length=15, blank=True)
    business_name = models.CharField(max_length=150, blank=True)
    logo = models.ImageField(
        upload_to="photographer_logos/",
        blank=True,
        null=True
    )

    def __str__(self):
        return self.business_name or self.user.username


class License(models.Model):
    PLAN_CHOICES = [
        ("LIFETIME", "Lifetime"),
        ("MONTHLY", "Monthly"),
        ("YEARLY", "Yearly"),
    ]

    photographer = models.OneToOneField(
        PhotographerProfile,
        on_delete=models.CASCADE,
        related_name="license"
    )

    key = models.CharField(max_length=50, unique=True)
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES)

    is_active = models.BooleanField(default=False)
    expires_at = models.DateTimeField(
        blank=True,
        null=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.key} - {self.plan}"


class Wedding(models.Model):
    photographer = models.ForeignKey(
        PhotographerProfile,
        on_delete=models.CASCADE,
        related_name="weddings"
    )

    bride_name = models.CharField(max_length=100)
    groom_name = models.CharField(max_length=100)
    wedding_date = models.DateField()
    event_name = models.CharField(max_length=150)

    qr_code = models.ImageField(
        upload_to="wedding_qr/",
        blank=True,
        null=True
    )

    # Google Drive integration
    drive_folder_id = models.CharField(
        max_length=200,
        blank=True,
        null=True
    )

    drive_account = models.ForeignKey(
        "GoogleDriveAccounts",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="weddings"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.event_name} - {self.bride_name} & {self.groom_name}"


class Photo(models.Model):
    wedding = models.ForeignKey(
        Wedding,
        on_delete=models.CASCADE,
        related_name="photos"
    )

    image = models.ImageField(
        upload_to="wedding_photos/"
    )

    face_descriptor = models.TextField(
        blank=True,
        null=True
    )
    drive_file_id = models.CharField(
    max_length=200,
    blank=True,
    null=True 
    )
    drive_connection = models.ForeignKey(
        "WeddingDriveConnection",
        on_delete = models.SET_NULL,
        blank=True,
        null=True,
        related_name="photos")

    uploaded_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return f"{self.wedding.event_name} - {self.id}"

class Guest(models.Model):
    wedding = models.ForeignKey(
        Wedding,
        on_delete=models.CASCADE,
        related_name="guests"
    )

    name = models.CharField(
        max_length=100
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return f"{self.name} - {self.wedding.event_name}"


class Favorite(models.Model):
    guest = models.ForeignKey(
        Guest,
        on_delete=models.CASCADE,
        related_name="favorites"
    )

    photo = models.ForeignKey(
        Photo,
        on_delete=models.CASCADE,
        related_name="favorites"
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        unique_together = (
            "guest",
            "photo"
        )

    def __str__(self):
        return f"{self.guest.name} - Photo {self.photo.id}"

class GoogleDriveAccounts(models.Model):
    photographer = models.ForeignKey(
        PhotographerProfile,
        on_delete=models.CASCADE,
        related_name="drive_accounts"
    )

    google_email = models.EmailField()

    access_token = models.TextField(
        blank=True,
        null=True
    )

    refresh_token = models.TextField(
        blank=True,
        null=True
    )

    is_active = models.BooleanField(
        default=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return self.google_email

class WeddingDriveConnection(models.Model):
    wedding = models.ForeignKey(
        Wedding,
        on_delete=models.CASCADE,
        related_name="drive_connectoins"
    )
    drive_account = models.ForeignKey(
        GoogleDriveAccounts,
        on_delete=models.CASCADE,
        related_name="wedding_connecitons"
    )
    folder_id=models.CharField(
        max_length=200
    )
    created_at= models.DateTimeField(
        auto_now_add=True
    )
    class Meta:
        unique_together = (
            "wedding",
            "drive_account",
            "folder_id"
        )
    def __str__(self) :
        return f"{self.wedding.event_name} - {self.drive_account.google_email}"

# class Event(models.Model):
#     photographer = models.ForeignKey(
#         User,
#         on_delete=models.CASCADE,
#         related_name="events"
#     )
#     name = models.CharField(max_length=200)
#     event_date = models.DateField()
#     created_at = models.DateTimeField(auto_now_add=True)

#     def __str__(self):
#         return self.name