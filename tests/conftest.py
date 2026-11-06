import os
import struct
import pytest
from datetime import datetime
from PIL import Image

import scripting.metadata_sorter as metadata_sorter


def make_test_image(
    path: str,
    width: int = 120,
    height: int = 80,
    color: str = "blue",
    date_taken: str = None,
    gps: tuple = None,  # (lat, lon)
    camera_make: str = None,
    camera_model: str = None,
) -> str:
    """Create a synthetic JPEG image with customizable EXIF and GPS metadata."""
    img = Image.new("RGB", (width, height), color=color)
    exif = img.getexif()

    if camera_make:
        exif[0x010F] = camera_make
    if camera_model:
        exif[0x0110] = camera_model

    if date_taken:
        exif_ifd = exif.get_ifd(0x8769)
        exif_ifd[36867] = date_taken  # DateTimeOriginal
        exif_ifd[306] = date_taken    # DateTime

    if gps:
        lat, lon = gps
        gps_ifd = exif.get_ifd(0x8825)
        lat_ref = "N" if lat >= 0 else "S"
        lon_ref = "E" if lon >= 0 else "W"
        lat_abs = abs(lat)
        lon_abs = abs(lon)

        lat_d = int(lat_abs)
        lat_m = int((lat_abs - lat_d) * 60)
        lat_s = int(((lat_abs - lat_d) * 60 - lat_m) * 60)

        lon_d = int(lon_abs)
        lon_m = int((lon_abs - lon_d) * 60)
        lon_s = int(((lon_abs - lon_d) * 60 - lon_m) * 60)

        gps_ifd[1] = lat_ref
        gps_ifd[2] = (lat_d, lat_m, lat_s)
        gps_ifd[3] = lon_ref
        gps_ifd[4] = (lon_d, lon_m, lon_s)

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    img.save(path, "JPEG", exif=exif)
    return path


def make_test_video(
    path: str,
    width: int = 1920,
    height: int = 1080,
    duration_seconds: int = 10,
    created_epoch: int = 1718452800,  # 2024-06-15 12:00:00 UTC
) -> str:
    """Create a minimal synthetic MP4 file valid for metadata_sorter's atom parser."""
    timescale = 1000
    duration_units = duration_seconds * timescale
    mac_epoch_offset = 2082844800
    c_time = created_epoch + mac_epoch_offset

    # mvhd atom (version 0): version(1), flags(3), c_time(4), m_time(4), timescale(4), duration(4), reserved/rate/etc. (80)
    mvhd_body = b"\x00\x00\x00\x00" + struct.pack(">IIII", c_time, c_time, timescale, duration_units) + (b"\x00" * 80)
    mvhd = struct.pack(">I4s", len(mvhd_body) + 8, b"mvhd") + mvhd_body

    # tkhd atom (version 0): version(1), flags(3), skip 20 bytes, skip 52 bytes, w(4 bytes fixed-point), h(4 bytes fixed-point)
    tkhd_body = b"\x00\x00\x00\x00" + (b"\x00" * 20) + (b"\x00" * 52) + struct.pack(">II", width << 16, height << 16)
    tkhd = struct.pack(">I4s", len(tkhd_body) + 8, b"tkhd") + tkhd_body
    trak = struct.pack(">I4s", len(tkhd) + 8, b"trak") + tkhd

    moov_body = mvhd + trak
    moov = struct.pack(">I4s", len(moov_body) + 8, b"moov") + moov_body

    ftyp = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(ftyp)
        f.write(moov)
        # Pad with dummy media data so size is realistic
        f.write(b"\x00" * 1024)

    return path


@pytest.fixture
def mock_geocoding(monkeypatch):
    """Mocks reverse geocoding to prevent external HTTP calls and provide deterministic cities."""
    # Pre-populate cache or mock get_city_from_gps
    known_locations = {
        (52.52, 13.405): "Berlin",
        (52.5199, 13.4): "Berlin",
        (48.8566, 2.3522): "Paris",
        (51.5074, -0.1278): "London",
    }

    def fake_get_city(lat, lon):
        if lat is None or lon is None:
            return None
        for (klat, klon), city in known_locations.items():
            if abs(lat - klat) < 0.05 and abs(lon - klon) < 0.05:
                return city
        return "TestCity"

    monkeypatch.setattr(metadata_sorter, "get_city_from_gps", fake_get_city)
    return fake_get_city


@pytest.fixture
def sample_media_dir(tmp_path, mock_geocoding):
    """
    Creates a realistic source folder containing diverse media types:
    - Image with EXIF timestamp and GPS (Berlin)
    - Image with EXIF timestamp only
    - Image with timestamp embedded in filename
    - Duplicate image identical to the first
    - MP4 video file
    - Non-media text file (notes.txt)
    """
    src = tmp_path / "source"
    src.mkdir()
    dest = tmp_path / "Sorted"

    # 1. Image with date and GPS (Berlin coordinates ~52.52, 13.40)
    p1 = src / "img_gps.jpg"
    make_test_image(
        str(p1),
        width=800,
        height=600,
        color="red",
        date_taken="2024:06:15 14:30:00",
        gps=(52.52, 13.40),
        camera_make="Sony",
        camera_model="Alpha 7",
    )

    # 2. Duplicate of p1 (exact same bytes)
    p1_dup = src / "img_gps_duplicate.jpg"
    with open(p1, "rb") as f_in, open(p1_dup, "wb") as f_out:
        f_out.write(f_in.read())

    # 3. Image with EXIF date only
    p2 = src / "img_date_only.jpg"
    make_test_image(
        str(p2),
        width=1024,
        height=768,
        color="green",
        date_taken="2024:08:20 18:45:10",
        camera_make="Canon",
        camera_model="EOS R5",
    )

    # 4. Image with no EXIF date, but WhatsApp style filename
    p3 = src / "WhatsApp Image 2024-11-11 at 11.25.26.jpeg"
    make_test_image(str(p3), width=640, height=480, color="yellow")

    # 5. Video MP4 file
    v1 = src / "clip_2024.mp4"
    make_test_video(str(v1), width=1920, height=1080, duration_seconds=15)

    # 6. Non-media file
    n1 = src / "readme.txt"
    n1.write_text("This is an unhandled text document.", encoding="utf-8")

    # 7. Nested folder with a photo
    sub = src / "nested"
    sub.mkdir()
    p4 = sub / "nested_photo.png"
    Image.new("RGB", (100, 100), "purple").save(str(p4))

    return {
        "src": str(src),
        "dest": str(dest),
        "files": {
            "img_gps": str(p1),
            "img_dup": str(p1_dup),
            "img_date_only": str(p2),
            "img_whatsapp": str(p3),
            "video": str(v1),
            "notes": str(n1),
            "nested_photo": str(p4),
        },
    }
