from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from .models import (
    PhotographerProfile,
    License,
    Wedding,
    Photo,
    GoogleDriveAccounts,
    WeddingDriveConnection,
    Guest,
    Favorite,
)
import qrcode, math, os, mimetypes, json
from io import BytesIO
from django.core.files.base import ContentFile
from django.urls import reverse
from django.http import JsonResponse
from django.conf import settings

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request


os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"


# =========================================================
# LOGIN
# =========================================================

def photographer_login(request):

    if request.method == "POST":

        username = request.POST.get("username")
        password = request.POST.get("password")

        user = authenticate(
            request,
            username=username,
            password=password
        )

        if user is not None:

            login(request, user)

            return redirect("dashboard")

        return render(
            request,
            "accounts/login.html",
            {
                "error": "Invalid username or password"
            }
        )

    return render(
        request,
        "accounts/login.html"
    )


# =========================================================
# DASHBOARD
# =========================================================

@login_required
def dashboard(request):

    profile = PhotographerProfile.objects.get(
        user=request.user
    )

    try:
        license = profile.license
    except Exception:
        license = None

    if license is None or not license.is_active:

        return redirect("activate_license")

    weddings = Wedding.objects.filter(
        photographer=profile
    ).order_by("-created_at")

    return render(
        request,
        "accounts/dashboard.html",
        {
            "profile": profile,
            "license": license,
            "weddings": weddings,
        }
    )


# =========================================================
# LICENSE ACTIVATION
# =========================================================

@login_required
def activate_license(request):

    profile = PhotographerProfile.objects.get(
        user=request.user
    )

    if request.method == "POST":

        key = request.POST.get("license_key")

        try:

            license = License.objects.get(
                key=key,
                photographer=profile
            )

            if license.plan == "LIFETIME":

                license.is_active = True
                license.expires_at = None
                license.save()

                return redirect("dashboard")

            return render(
                request,
                "accounts/activate_license.html",
                {
                    "error": "This license type is not configured yet."
                }
            )

        except License.DoesNotExist:

            return render(
                request,
                "accounts/activate_license.html",
                {
                    "error": "Invalid license key."
                }
            )

    return render(
        request,
        "accounts/activate_license.html"
    )


# =========================================================
# GOOGLE DRIVE HELPERS
# =========================================================

def get_drive_credentials(drive_account):
    """
    Rebuild Google OAuth credentials from the saved Drive account.
    Refresh the access token automatically when it has expired.
    """

    client_secret_file = os.environ.get(
        "GOOGLE_CLIENT_SECRET_FILE"
    )

    if not client_secret_file:
        client_secret_file = os.path.join(
            settings.BASE_DIR,
            "credentials",
            "client_secret.json"
        )

    with open(
        client_secret_file,
        "r",
        encoding="utf-8"
    ) as file:

        client_config = json.load(file)

    web_config = client_config.get(
        "web",
        client_config.get("installed", {})
    )

    credentials = Credentials(
        token=drive_account.access_token,
        refresh_token=drive_account.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=web_config.get("client_id"),
        client_secret=web_config.get("client_secret"),
        scopes=[
            "https://www.googleapis.com/auth/drive"
        ],
    )

    if credentials.expired and credentials.refresh_token:

        credentials.refresh(Request())

        drive_account.access_token = credentials.token

        drive_account.save(
            update_fields=["access_token"]
        )

    return credentials


def create_drive_folder_for_wedding(
    wedding,
    drive_account
):
    """
    Create a wedding folder in the connected Google Drive.
    Returns the folder ID, or None if Drive creation fails.
    """

    try:

        credentials = get_drive_credentials(
            drive_account
        )

        drive_service = build(
            "drive",
            "v3",
            credentials=credentials
        )

        folder_metadata = {
            "name": f"Wedding - {wedding.event_name}",
            "mimeType": "application/vnd.google-apps.folder",
        }

        folder = drive_service.files().create(
            body=folder_metadata,
            fields="id,name"
        ).execute()

        return folder.get("id")

    except (HttpError, Exception) as e:

        print(
            f"Google Drive folder creation failed: {e}"
        )

        return None


def upload_photo_to_drive(
    photo,
    wedding,
    drive_account,
    folder_id
):
    """
    Upload one locally saved wedding photo to a specific
    Google Drive account/folder.

    Returns the Drive file ID when successful,
    otherwise None.
    """

    try:

        credentials = get_drive_credentials(
            drive_account
        )

        drive_service = build(
            "drive",
            "v3",
            credentials=credentials
        )

        photo_path = photo.image.path

        mime_type, _ = mimetypes.guess_type(
            photo.image.name
        )

        if not mime_type:
            mime_type = "application/octet-stream"

        file_metadata = {
            "name": os.path.basename(
                photo.image.name
            ),
            "parents": [folder_id],
        }

        with open(
            photo_path,
            "rb"
        ) as photo_file:

            media = MediaIoBaseUpload(
                photo_file,
                mimetype=mime_type,
                resumable=True
            )

            uploaded_file = drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id,name"
            ).execute()

        return uploaded_file.get("id")

    except Exception as e:

        print(
            f"Google Drive photo upload failed "
            f"for Photo {photo.id} "
            f"on {drive_account.google_email}: {e}"
        )

        return None


def upload_photo_with_fallback(
    photo,
    wedding
):
    """
    Upload photo to a connected Wedding Drive.

    Try the wedding's existing Drive connections first.
    If no usable connection exists, create a wedding
    folder in an active Drive account and create a
    WeddingDriveConnection.

    Returns the Google Drive file ID on success.
    """

    profile = wedding.photographer

    # ---------------------------------------------
    # GET ALL ACTIVE CONNECTIONS
    # ---------------------------------------------

    connections = list(
        WeddingDriveConnection.objects.filter(
            wedding=wedding,
            drive_account__photographer=profile,
            drive_account__is_active=True
        )
        .select_related("drive_account")
        .order_by("id")
    )

    # ---------------------------------------------
    # BACKWARD COMPATIBILITY
    # ---------------------------------------------
    # If old Wedding fields exist but connection
    # record does not, create the connection.

    if not connections:

        if (
            wedding.drive_account
            and wedding.drive_folder_id
            and wedding.drive_account.is_active
        ):

            connection, created = (
                WeddingDriveConnection.objects.get_or_create(
                    wedding=wedding,
                    drive_account=wedding.drive_account,
                    folder_id=wedding.drive_folder_id
                )
            )

            connections = [connection]

    # ---------------------------------------------
    # TRY EXISTING CONNECTIONS
    # ---------------------------------------------

    for connection in connections:

        drive_account = connection.drive_account
        folder_id = connection.folder_id

        drive_file_id = upload_photo_to_drive(
            photo,
            wedding,
            drive_account,
            folder_id
        )

        if drive_file_id:

            photo.drive_connection = connection

            photo.drive_file_id = drive_file_id

            photo.save(
                update_fields=[
                    "drive_connection",
                    "drive_file_id"
                ]
            )

            print(
                "Photo uploaded to Drive:",
                drive_account.google_email,
                "Connection:",
                connection.id,
                "File:",
                drive_file_id
            )

            return drive_file_id

    # ---------------------------------------------
    # NO EXISTING CONNECTION WORKED
    # ---------------------------------------------
    # Try active Drive accounts and create a new
    # WeddingDriveConnection when needed.

    accounts = (
        GoogleDriveAccounts.objects.filter(
            photographer=profile,
            is_active=True
        )
        .order_by("-created_at")
    )

    for account in accounts:

        # Skip accounts already tried above.
        if any(
            connection.drive_account_id == account.id
            for connection in connections
        ):
            continue

        # -----------------------------------------
        # CREATE WEDDING FOLDER
        # -----------------------------------------

        folder_id = create_drive_folder_for_wedding(
            wedding,
            account
        )

        if not folder_id:
            continue

        # -----------------------------------------
        # CREATE MULTI-DRIVE CONNECTION
        # -----------------------------------------

        connection, created = (
            WeddingDriveConnection.objects.get_or_create(
                wedding=wedding,
                drive_account=account,
                folder_id=folder_id
            )
        )

        # -----------------------------------------
        # TRY UPLOAD
        # -----------------------------------------

        drive_file_id = upload_photo_to_drive(
            photo,
            wedding,
            account,
            folder_id
        )

        if drive_file_id:

            photo.drive_connection = connection

            photo.drive_file_id = drive_file_id

            photo.save(
                update_fields=[
                    "drive_connection",
                    "drive_file_id"
                ]
            )

            # Keep old fields only for backward
            # compatibility.
            if not wedding.drive_account:

                wedding.drive_account = account
                wedding.drive_folder_id = folder_id

                wedding.save(
                    update_fields=[
                        "drive_account",
                        "drive_folder_id"
                    ]
                )

            print(
                "Photo uploaded using fallback Drive:",
                account.google_email,
                "Connection:",
                connection.id,
                "File:",
                drive_file_id
            )

            return drive_file_id

    # ---------------------------------------------
    # ALL DRIVES FAILED
    # ---------------------------------------------

    print(
        "Photo upload failed on all connected "
        "Google Drive accounts."
    )

    return None


# =========================================================
# CREATE WEDDING
# =========================================================

@login_required
def create_wedding(request):

    profile = PhotographerProfile.objects.get(
        user=request.user
    )

    if request.method == "POST":

        event_name = request.POST.get(
            "event_name"
        )

        bride_name = request.POST.get(
            "bride_name"
        )

        groom_name = request.POST.get(
            "groom_name"
        )

        wedding_date = request.POST.get(
            "wedding_date"
        )

        # Create wedding locally first.
        wedding = Wedding.objects.create(
            photographer=profile,
            event_name=event_name,
            bride_name=bride_name,
            groom_name=groom_name,
            wedding_date=wedding_date,
        )

        # -------------------------------------------------
        # GOOGLE DRIVE
        # -------------------------------------------------

        drive_accounts = (
            GoogleDriveAccounts.objects.filter(
                photographer=profile,
                is_active=True
            )
            .order_by("-created_at")
        )

        for drive_account in drive_accounts:

            folder_id = create_drive_folder_for_wedding(
                wedding,
                drive_account
            )

            if folder_id:

                # -----------------------------------------
                # CREATE MULTI-DRIVE CONNECTION
                # -----------------------------------------

                connection, created = (
                    WeddingDriveConnection.objects.get_or_create(
                        wedding=wedding,
                        drive_account=drive_account,
                        folder_id=folder_id
                    )
                )

                print(
                    "Wedding Drive connection created:",
                    connection.id,
                    drive_account.google_email,
                    folder_id
                )

                # -----------------------------------------
                # KEEP OLD FIELDS FOR BACKWARD
                # COMPATIBILITY
                # -----------------------------------------

                wedding.drive_account = drive_account
                wedding.drive_folder_id = folder_id

                wedding.save(
                    update_fields=[
                        "drive_account",
                        "drive_folder_id"
                    ]
                )

                break

        # -------------------------------------------------
        # QR CODE
        # -------------------------------------------------

        qr_url = request.build_absolute_uri(
            reverse(
                "guest_match",
                args=[wedding.id]
            )
        )

        qr_image = qrcode.make(
            qr_url
        )

        buffer = BytesIO()

        qr_image.save(
            buffer,
            format="PNG"
        )

        wedding.qr_code.save(
            f"wedding_{wedding.id}_qr.png",
            ContentFile(
                buffer.getvalue()
            ),
            save=True
        )

        return redirect(
            "dashboard"
        )

    return render(
        request,
        "accounts/create_wedding.html"
    )


# =========================================================
# UPLOAD PHOTOS
# =========================================================

@login_required
def upload_photos(
    request,
    wedding_id
):

    profile = PhotographerProfile.objects.get(
        user=request.user
    )

    wedding = Wedding.objects.get(
        id=wedding_id,
        photographer=profile
    )

    if request.method == "POST":

        photos = request.FILES.getlist(
            "photos"
        )

        descriptors = request.POST.getlist(
            "face_descriptors"
        )

        for index, image in enumerate(photos):

            descriptor = ""

            if index < len(descriptors):

                descriptor = descriptors[index]

            # Save photo locally first.
            photo = Photo.objects.create(
                wedding=wedding,
                image=image,
                face_descriptor=descriptor
            )

            # Upload to current Drive account first.
            # If it fails, try other active accounts.
            drive_file_id = upload_photo_with_fallback(
                photo,
                wedding
            )

            if drive_file_id:

                photo.drive_file_id = drive_file_id

                photo.save(
                    update_fields=[
                        "drive_file_id"
                    ]
                )

        return redirect(
            "dashboard"
        )

    return render(
        request,
        "accounts/upload_photos.html",
        {
            "wedding": wedding
        }
    )


# =========================================================
# SYNC GOOGLE DRIVE PHOTOS
# =========================================================

@login_required
def sync_drive_photos(
    request,
    wedding_id
):

    profile = PhotographerProfile.objects.get(
        user=request.user
    )

    wedding = Wedding.objects.get(
        id=wedding_id,
        photographer=profile
    )

    #get all the connected drive folders 

    connections = (
        WeddingDriveConnection.objects.filter(
            wedding = wedding,
            drive_account__is_active = True
        ).select_related("drive_account").order_by("id"))

    #backward compatibility
    if not connections.exists():
        if (
            wedding.drive_account
            and wedding.drive_folder_id):
            connection, created = (
                WeddingDriveConnection.objects.get_or_create(
                    wedding = wedding,
                    drive_account= wedding.drive_account,
                    folder_id = wedding.drive_folder_id
                )
            )
            connections =(
                WeddingDriveConnection.objects.filter(
                    wedding=wedding,
                    drive_account__is_active = True
                ).select_related("drive_account")
            )
        if not connections.exists():
            return redirect(
                "wedding_gallery",
                wedding_id = wedding_id
            )        

        try:
            for connection in connections:
                drive_account=connection.drive_account
                folder_id = connection.folder_id

            print("Syncing Drive",
            drive_account.google_email,
            "Folder:",
            folder_id)

            credentials = get_drive_credentials(
                drive_account
            )

            drive_service = build(
                "drive",
                "v3",
                credentials=credentials
            )

            results = drive_service.files().list(
                q=(
                    f"'{folder_id}' in parents "
                    "and trashed = false"
                ),
                fields="files(id, name, mimeType)"
            ).execute()

            drive_files = results.get(
                "files",
                []
            )

            existing_drive_ids = set(
                Photo.objects.filter(
                    wedding=wedding,
                    drive_connection=connection
                ).exclude(
                    drive_file_id__isnull=True
                ).exclude(
                    drive_file_id=""
                ).values_list(
                    "drive_file_id",
                    flat=True
                )
            )

            for drive_file in drive_files:

                drive_file_id = drive_file.get(
                    "id"
                )

                mime_type = drive_file.get(
                    "mimeType",
                    ""
                )

                # Only sync image files.
                if not mime_type.startswith(
                    "image/"
                ):
                    continue

                # Already synced.
                if drive_file_id in existing_drive_ids:
                    continue

                request_file = drive_service.files().get_media(
                    fileId=drive_file_id
                )

                file_buffer = BytesIO()

                downloader = MediaIoBaseDownload(
                    file_buffer,
                    request_file
                )

                done = False

                while not done:

                    _, done = downloader.next_chunk()

                file_buffer.seek(0)
                #unique local filename
                original_name = drive_file.get(
                    "name",
                    "drive_photo.jpg"
                )
                base_name, extension = os.path.splitext(
                    original_name
                )
                unique_name = (
                    f"wedding_{wedding_id}_"
                    f"connection_{connection.id}_"
                    f"{extension}"
                )

                photo = Photo.objects.create(
                    wedding=wedding,

                    image=ContentFile(
                        file_buffer.read(),
                        name=unique_name
                    ),

                    face_descriptor="",

                    drive_file_id=drive_file_id,
                    drive_connecition = connection
                )
                print(
                    "Synced.", 
                    original_name,
                    "from",
                    drive_account.google_email
                )

            return redirect(
                "wedding_gallery",
                wedding_id=wedding.id
            )

        except Exception as e:

            print(
                "Google Drive sync failed:",
                e
            )

            return redirect(
                "wedding_gallery",
                wedding_id=wedding.id
            )


# =========================================================
# WEDDING GALLERY
# =========================================================

@login_required
def wedding_gallery(
    request,
    wedding_id
):

    profile = PhotographerProfile.objects.get(
        user=request.user
    )

    wedding = Wedding.objects.get(
        id=wedding_id,
        photographer=profile
    )

    photos = wedding.photos.all().order_by(
        "-uploaded_at"
    )

    return render(
        request,
        "accounts/wedding_gallery.html",
        {
            "wedding": wedding,
            "photos": photos,
        }
    )


# =========================================================
# OLD FACE TEST
# =========================================================

def face_test(request):

    return render(
        request,
        "accounts/face_test.html"
    )


def face_match_test(
    request,
    wedding_id
):

    wedding = Wedding.objects.get(
        id=wedding_id
    )

    photos = wedding.photos.exclude(
        face_descriptor__isnull=True
    ).exclude(
        face_descriptor=""
    )

    return render(
        request,
        "accounts/face_match_test.html",
        {
            "wedding": wedding,
            "photos": photos,
        }
    )


# =========================================================
# GUEST MATCH PAGE
# =========================================================

def guest_match(
    request,
    wedding_id
):

    try:

        wedding = Wedding.objects.get(
            id=wedding_id
        )

    except Wedding.DoesNotExist:

        return render(
            request,
            "accounts/guest_match.html",
            {
                "error": "Wedding not found."
            }
        )

    if request.method == "POST":

        guest_name = request.POST.get(
            "guest_name",
            ""
        ).strip()

        if not guest_name:

            return render(
                request,
                "accounts/guest_match.html",
                {
                    "wedding": wedding,
                    "error": "Please enter your name."
                }
            )

        guest = Guest.objects.create(
            wedding=wedding,
            name=guest_name
        )

        return render(
            request,
            "accounts/guest_match.html",
            {
                "wedding": wedding,
                "guest": guest
            }
        )

    return render(
        request,
        "accounts/guest_match.html",
        {
            "wedding": wedding
        }
    )


# =========================================================
# CREATE GUEST
# =========================================================

def create_guest(request):

    if request.method != "POST":

        return JsonResponse(
            {
                "success": False,
                "error": "Invalid request."
            },
            status=400
        )

    guest_name = request.POST.get(
        "guest_name",
        ""
    ).strip()

    wedding_id = request.POST.get(
        "wedding_id"
    )

    if not guest_name:

        return JsonResponse(
            {
                "success": False,
                "error": "Please enter your name."
            },
            status=400
        )

    if not wedding_id:

        return JsonResponse(
            {
                "success": False,
                "error": "Wedding is required."
            },
            status=400
        )

    try:

        wedding = Wedding.objects.get(
            id=wedding_id
        )

    except Wedding.DoesNotExist:

        return JsonResponse(
            {
                "success": False,
                "error": "Wedding not found."
            },
            status=404
        )

    guest = Guest.objects.create(
        wedding=wedding,
        name=guest_name
    )

    return JsonResponse(
        {
            "success": True,
            "guest_id": guest.id
        }
    )


# =========================================================
# ADD FAVORITE
# =========================================================

def add_favorite(request):

    if request.method != "POST":

        return JsonResponse(
            {
                "success": False,
                "error": "Invalid request."
            },
            status=400
        )

    guest_id = request.POST.get(
        "guest_id"
    )

    photo_id = request.POST.get(
        "photo_id"
    )

    if not guest_id or not photo_id:

        return JsonResponse(
            {
                "success": False,
                "error": "Guest and photo are required."
            },
            status=400
        )

    try:

        guest = Guest.objects.get(
            id=guest_id
        )

        photo = Photo.objects.get(
            id=photo_id,
            wedding=guest.wedding
        )

    except (
        Guest.DoesNotExist,
        Photo.DoesNotExist
    ):

        return JsonResponse(
            {
                "success": False,
                "error": "Guest or photo not found."
            },
            status=404
        )

    favorite, created = Favorite.objects.get_or_create(
        guest=guest,
        photo=photo
    )

    return JsonResponse(
        {
            "success": True,
            "favorite": True,
            "created": created
        }
    )


# =========================================================
# GUEST FAVORITES
# =========================================================

@login_required
def guest_favorites(
    request,
    wedding_id
):

    profile = PhotographerProfile.objects.get(
        user=request.user
    )

    wedding = Wedding.objects.get(
        id=wedding_id,
        photographer=profile
    )

    guests = (
        wedding.guests
        .prefetch_related(
            "favorites__photo"
        )
        .filter(
            favorites__isnull=False
        )
        .distinct()
        .order_by("name")
    )

    return render(
        request,
        "accounts/guest_favorites.html",
        {
            "wedding": wedding,
            "guests": guests,
        }
    )



# GUEST WEDDING PHOTOS
# OPTIMIZED SERVER-SIDE FACE MATCHING
# =========================================================

def guest_wedding_photos(request, wedding_id):

    try:
        wedding = Wedding.objects.get(
            id=wedding_id
        )

    except Wedding.DoesNotExist:

        return JsonResponse(
            {
                "success": False,
                "error": "Wedding not found."
            },
            status=404
        )

    # -----------------------------------------------------
    # ONLY POST REQUEST
    # -----------------------------------------------------

    if request.method != "POST":

        return JsonResponse(
            {
                "success": False,
                "error": "POST request required."
            },
            status=405
        )

    # -----------------------------------------------------
    # READ GUEST FACE DESCRIPTOR
    # -----------------------------------------------------

    try:

        data = json.loads(
            request.body
        )

        guest_descriptor = data.get(
            "descriptor"
        )

    except (
        json.JSONDecodeError,
        TypeError,
        ValueError
    ):

        return JsonResponse(
            {
                "success": False,
                "error": "Invalid request."
            },
            status=400
        )

    # -----------------------------------------------------
    # VALIDATE DESCRIPTOR
    # -----------------------------------------------------

    if not isinstance(
        guest_descriptor,
        list
    ):

        return JsonResponse(
            {
                "success": False,
                "error": "Invalid face descriptor."
            },
            status=400
        )

    if not guest_descriptor:

        return JsonResponse(
            {
                "success": False,
                "error": "Face descriptor missing."
            },
            status=400
        )

    # -----------------------------------------------------
    # CONVERT GUEST VALUES ONCE
    # -----------------------------------------------------

    try:

        guest_vector = [
            float(value)
            for value in guest_descriptor
        ]

    except (
        TypeError,
        ValueError
    ):

        return JsonResponse(
            {
                "success": False,
                "error": "Invalid face descriptor."
            },
            status=400
        )

    guest_length = len(
        guest_vector
    )

    # -----------------------------------------------------
    # 0.6 THRESHOLD
    #
    # Instead of calculating:
    #
    # sqrt(distance)
    #
    # every time, compare squared distance:
    #
    # distance² < 0.6²
    #
    # This avoids sqrt() for every comparison.
    # -----------------------------------------------------

    threshold_squared = 0.6 * 0.6

    matches = []

    # -----------------------------------------------------
    # LOAD ONLY REQUIRED DATABASE FIELDS
    # -----------------------------------------------------

    photos = (
        wedding.photos
        .exclude(
            face_descriptor__isnull=True
        )
        .exclude(
            face_descriptor=""
        )
        .only(
            "id",
            "image",
            "face_descriptor"
        )
    )

    # -----------------------------------------------------
    # MATCH EACH PHOTO
    # -----------------------------------------------------

    for photo in photos:

        raw_descriptors = (
            photo.face_descriptor
        )

        if not raw_descriptors:
            continue

        # -------------------------------------------------
        # FACE DESCRIPTOR MAY BE JSON STRING
        # OR ALREADY A PYTHON LIST
        # -------------------------------------------------

        if isinstance(
            raw_descriptors,
            str
        ):

            try:

                descriptors = json.loads(
                    raw_descriptors
                )

            except (
                json.JSONDecodeError,
                TypeError,
                ValueError
            ):

                continue

        else:

            descriptors = raw_descriptors

        if not descriptors:
            continue

        # -------------------------------------------------
        # SINGLE DESCRIPTOR
        #
        # Example:
        # [0.12, 0.34, 0.56, ...]
        #
        # Convert it to:
        # [[0.12, 0.34, 0.56, ...]]
        # -------------------------------------------------

        if (
            isinstance(descriptors, list)
            and descriptors
            and isinstance(
                descriptors[0],
                (int, float)
            )
        ):

            descriptors = [
                descriptors
            ]

        if not isinstance(
            descriptors,
            list
        ):

            continue

        # -------------------------------------------------
        # CHECK FACES IN THIS PHOTO
        # -------------------------------------------------

        photo_matched = False
        best_distance_squared = None

        for descriptor in descriptors:

            if not isinstance(
                descriptor,
                (list, tuple)
            ):

                continue

            if len(descriptor) != guest_length:

                continue

            try:

                # -----------------------------------------
                # CALCULATE SQUARED EUCLIDEAN DISTANCE
                # WITHOUT sqrt()
                # -----------------------------------------

                distance_squared = sum(
                    (
                        float(a) -
                        float(b)
                    ) ** 2
                    for a, b in zip(
                        guest_vector,
                        descriptor
                    )
                )

            except (
                TypeError,
                ValueError
            ):

                continue

            # ---------------------------------------------
            # KEEP BEST DISTANCE FOR THIS PHOTO
            # ---------------------------------------------

            if (
                best_distance_squared is None
                or distance_squared <
                best_distance_squared
            ):

                best_distance_squared = (
                    distance_squared
                )

            # ---------------------------------------------
            # MATCH FOUND
            # ---------------------------------------------

            if distance_squared < threshold_squared:

                photo_matched = True

                break

        # -------------------------------------------------
        # ADD ONLY MATCHING PHOTO
        # -------------------------------------------------

        if photo_matched:

            matches.append(
                {
                    "id": photo.id,
                    "image": photo.image.url,
                    "distance": math.sqrt(
                        best_distance_squared
                    )
                    if best_distance_squared is not None
                    else 0
                }
            )

    # -----------------------------------------------------
    # BEST MATCHES FIRST
    # -----------------------------------------------------

    matches.sort(
        key=lambda item: item["distance"]
    )

    # -----------------------------------------------------
    # RETURN ONLY MATCHING PHOTOS
    # -----------------------------------------------------

    return JsonResponse(
        {
            "success": True,
            "event_name": wedding.event_name,
            "matches": matches
        }
    )

# =========================================================
# PHOTOGRAPHER BRANDING
# =========================================================

@login_required
def photographer_branding(request):

    profile = PhotographerProfile.objects.get(
        user=request.user
    )

    if request.method == "POST":

        profile.business_name = request.POST.get(
            "business_name"
        )

        if request.FILES.get("logo"):

            profile.logo = request.FILES.get(
                "logo"
            )

        profile.save()

        return redirect(
            "dashboard"
        )

    return render(
        request,
        "accounts/photographer_branding.html",
        {
            "profile": profile
        }
    )


# =========================================================
# DELETE WEDDING
# =========================================================

@login_required
def delete_wedding(
    request,
    wedding_id
):

    profile = PhotographerProfile.objects.get(
        user=request.user
    )

    wedding = Wedding.objects.get(
        id=wedding_id,
        photographer=profile
    )

    if request.method == "POST":

        # Delete Google Drive wedding folder first.
        if (
            wedding.drive_account
            and wedding.drive_folder_id
        ):

            try:

                credentials = get_drive_credentials(
                    wedding.drive_account
                )

                drive_service = build(
                    "drive",
                    "v3",
                    credentials=credentials
                )

                drive_service.files().delete(
                    fileId=wedding.drive_folder_id
                ).execute()

                print(
                    "Google Drive folder deleted: "
                    f"{wedding.drive_folder_id}"
                )

            except Exception as e:

                print(
                    "Google Drive folder deletion failed: "
                    f"{e}"
                )

                # Stop here so database wedding is
                # not deleted if Drive deletion failed.
                return redirect(
                    "dashboard"
                )

        # Delete wedding from database.
        wedding.delete()

    return redirect(
        "dashboard"
    )


# =========================================================
# DELETE PHOTO
# =========================================================

@login_required
def delete_photo(
    request,
    photo_id
):

    profile = PhotographerProfile.objects.get(
        user=request.user
    )

    photo = Photo.objects.get(
        id=photo_id,
        wedding__photographer=profile
    )

    if request.method == "POST":
        drive_connection = (photo.drive_connection)

        # Delete photo from Google Drive first.
        if (
            photo.drive_file_id 
            and drive_connection
            and drive_connection.drive_account
        ):

            try:

                credentials = get_drive_credentials(
                    drive_connection.drive_account
                )

                drive_service = build(
                    "drive",
                    "v3",
                    credentials=credentials
                )

                drive_service.files().delete(
                    fileId=photo.drive_file_id
                ).execute()

                print(
                    "Google Drive photo deleted: ", photo.drive_file_id
                )

            except Exception as e:

                print(
                    "Google Drive photo deletion failed: ", e
                )

                # Do not delete database photo
                # if Drive deletion failed.
                return redirect(
                    "dashboard"
                )
        
        #delete local photo

        if photo.image:
            try:
                photo.image.delete(
                    save = False
                )
            except Exception as e :
                print(
                    "Local photo deleteion failed:", e
                )


        # Delete local/database photo.
        photo.delete()

    return redirect(
        "dashboard"
    )


# =========================================================
# GOOGLE DRIVE ACCOUNTS
# =========================================================

@login_required
def google_drive(request):

    profile = PhotographerProfile.objects.get(
        user=request.user
    )

    drive_accounts = GoogleDriveAccounts.objects.filter(
        photographer=profile
    ).order_by("-created_at")

    return render(
        request,
        "accounts/google_drive.html",
        {
            "profile": profile,
            "drive_accounts": drive_accounts
        }
    )


# =========================================================
# DISCONNECT GOOGLE DRIVE
# =========================================================

@login_required
def disconnect_google_drive(
    request,
    account_id
):

    profile = PhotographerProfile.objects.get(
        user=request.user
    )

    account = GoogleDriveAccounts.objects.get(
        id=account_id,
        photographer=profile
    )

    if request.method == "POST":

        #find all wedding connections, for this google drive account
        connections = (
            WeddingDriveConnection.objects.filter(
                drive_account = account,
                wedding__photographer = profile
            ).select_related("wedding")
        )

        affected_weddings = set(
            connection.wedding_id for connection in connections
        )
        #removes only those photos belonging, to these drive connections

        photos = Photo.objects.filter(
            drive_connection__in= connections
        )
        for photo in photos:
            if photo.image:
                try:
                    photo.image.delete(
                        save=False
                    )
                except Exception as e:
                    print(
                        "Local photo deletion failed.", e
                    )
            photo.delete()


        #delete connection records

        connections.delete()

        #fix old backward compatibility fields

        for wedding_id in affected_weddings:
            wedding = Wedding.objects.get(
                id = wedding_id,
                photographer = profile
            )

            #find another remaining connection.

            remaining_connection = (
                WeddingDriveConnection.objects.filter(
                    wedding= wedding,
                    drive_account__is_active=True
                ).select_related("drive_account").order_by("id").first()
            )
            if remaining_connection:
                wedding.drive_account: (
                    remaining_connection.drive_account
                )
                wedding.drive_folder_id = (
                    remaining_connection.folder_id
                )
            else:
                wedding.drive_account:None
                wedding.drive_folder_id = None
            wedding.save(
                update_fields=[
                    "drive_account",
                    "drive_folder_id"
                ]
            )

        # Remove account from this application.
        account.delete()

    return redirect(
        "google_drive"
    )


# =========================================================
# GOOGLE DRIVE FOLDERS
# =========================================================

@login_required
def google_drive_folders(
    request,
    account_id
):

    profile = PhotographerProfile.objects.get(
        user=request.user
    )

    account = GoogleDriveAccounts.objects.get(
        id=account_id,
        photographer=profile
    )

    try:

        credentials = get_drive_credentials(
            account
        )

        drive_service = build(
            "drive",
            "v3",
            credentials=credentials
        )

        results = drive_service.files().list(
            q=(
                "mimeType = "
                "'application/vnd.google-apps.folder' "
                "and trashed = false"
            ),
            fields="files(id, name, parents)",
            orderBy="name"
        ).execute()

        folders = results.get(
            "files",
            []
        )

    except Exception as e:

        print(
            "Google Drive folder loading failed: "
            f"{e}"
        )

        folders = []

    return render(
        request,
        "accounts/google_drive_folders.html",
        {
            "account": account,
            "folders": folders
        }
    )


# =========================================================
# CONNECT EXISTING DRIVE FOLDER
# =========================================================

@login_required
def connect_drive_folder(
    request,
    account_id
):

    profile = PhotographerProfile.objects.get(
        user=request.user
    )

    account = GoogleDriveAccounts.objects.get(
        id=account_id,
        photographer=profile
    )

    if request.method == "POST":

        folder_id = request.POST.get(
            "folder_id"
        )

        wedding_id = request.POST.get(
            "wedding_id"
        )

        wedding = Wedding.objects.get(
            id=wedding_id,
            photographer=profile
        )

        # Make sure selected folder belongs
        # to the selected Google Drive account.
        try:

            credentials = get_drive_credentials(
                account
            )

            drive_service = build(
                "drive",
                "v3",
                credentials=credentials
            )

            folder = drive_service.files().get(
                fileId=folder_id,
                fields="id,name,mimeType"
            ).execute()

            if folder.get("mimeType") != (
                "application/vnd.google-apps.folder"
            ):

                return redirect(
                    "google_drive_folders",
                    account_id=account.id
                )

        except Exception as e:

            print(
                "Google Drive folder verification failed: "
                f"{e}"
            )

            return redirect(
                "google_drive_folders",
                account_id=account.id
            )
        
        connection, created = (
            WeddingDriveConnection.objects.get_or_create(
                wedding=wedding,
                drive_account = account,
                folder_id = folder_id
            )
        )
        print(
            "Wedding Drive connection:",
            connection.id,
            account.google_email,
            folder_id,
            "created =",
            created
        )

        # Connect selected Drive account + folder
        # to selected wedding.
        if not wedding.drive_account :

            wedding.drive_account = account
            wedding.drive_folder_id = folder_id

            wedding.save(
                update_fields=[
                    "drive_account",
                    "drive_folder_id"
                ]
            )

            return redirect(
                "wedding_gallery",
                wedding_id=wedding.id
            )

    return redirect(
        "google_drive_folders",
        account_id=account.id
    )


# =========================================================
# GOOGLE DRIVE CONNECT
# =========================================================

@login_required
def google_drive_connect(request):

    

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
                "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=[
            "https://www.googleapis.com/auth/drive",
        ],
        redirect_uri=request.build_absolute_uri(
            "/accounts/drive/callback/"
        )
    )

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"
    )

    request.session[
        "google_oauth_state"
    ] = state

    return redirect(
        authorization_url
    )


# =========================================================
# GOOGLE DRIVE CALLBACK
# =========================================================

@login_required
def google_drive_callback(request):

    state = request.session.get(
        "google_oauth_state"
    )

    if not state:

        return render(
            request,
            "accounts/google_drive.html",
            {
                "error":
                    "Google authorization session expired."
            }
        )

    

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
                "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=[
            "https://www.googleapis.com/auth/drive",
        ],
        state=state,
        redirect_uri=request.build_absolute_uri(
            "/accounts/drive/callback/"
        )
    )

    try:

        flow.fetch_token(
            authorization_response=
            request.build_absolute_uri()
        )

    except Exception as e:

        return render(
            request,
            "accounts/google_drive.html",
            {
                "error":
                    f"Google connection failed: {str(e)}"
            }
        )

    credentials = flow.credentials

    profile = PhotographerProfile.objects.get(
        user=request.user
    )

    drive_service = build(
        "drive",
        "v3",
        credentials=credentials
    )

    about = drive_service.about().get(
        fields="user(emailAddress)"
    ).execute()

    google_email = about.get(
        "user",
        {}
    ).get(
        "emailAddress",
        "Google Drive Account"
    )

    # Prevent duplicate account for same
    # photographer + Google email.
    drive_account = GoogleDriveAccounts.objects.filter(
        photographer=profile,
        google_email=google_email
    ).first()

    if drive_account:

        drive_account.access_token = (
            credentials.token
        )

        if credentials.refresh_token:

            drive_account.refresh_token = (
                credentials.refresh_token
            )

        drive_account.is_active = True

        drive_account.save()

    else:

        GoogleDriveAccounts.objects.create(
            photographer=profile,
            google_email=google_email,
            access_token=credentials.token,
            refresh_token=credentials.refresh_token,
            is_active=True
        )

    request.session.pop(
        "google_oauth_state",
        None
    )

    return redirect(
        "google_drive"
    )