import os
import json
import io
import urllib.request
import pytest
from datetime import datetime

import scripting.metadata_sorter as ms
from tests.conftest import make_test_image, make_test_video


class TestFormattingAndHashing:
    def test_format_size(self):
        assert ms.format_size(500) == "500 B"
        assert ms.format_size(1024) == "1.00 KB"
        assert ms.format_size(2560) == "2.50 KB"
        assert ms.format_size(1048576) == "1.00 MB"
        assert ms.format_size(1048576 * 3.5) == "3.50 MB"

    def test_format_duration(self):
        assert ms.format_duration(None) == "Unknown"
        assert ms.format_duration(0) == "00:00"
        assert ms.format_duration(45) == "00:45"
        assert ms.format_duration(65) == "01:05"
        assert ms.format_duration(3600) == "01:00:00"
        assert ms.format_duration(3665) == "01:01:05"

    def test_get_file_hash(self, tmp_path):
        f1 = tmp_path / "file1.txt"
        f2 = tmp_path / "file2.txt"
        f3 = tmp_path / "file3.txt"

        f1.write_bytes(b"MediaSorter Test Data")
        f2.write_bytes(b"MediaSorter Test Data")
        f3.write_bytes(b"Different Data")

        hash1 = ms.get_file_hash(str(f1))
        hash2 = ms.get_file_hash(str(f2))
        hash3 = ms.get_file_hash(str(f3))

        assert len(hash1) == 64
        assert hash1 == hash2
        assert hash1 != hash3

        # Nonexistent file returns None
        assert ms.get_file_hash(str(tmp_path / "does_not_exist.bin")) is None


class TestFilenameDateParsing:
    def test_whatsapp_filename(self):
        fn = "WhatsApp Image 2024-11-11 at 11.25.26.jpeg"
        res = ms.parse_date_from_filename(fn)
        assert res == "2024:11:11 11:25:26"

    def test_hyphen_and_underscore_format(self):
        fn1 = "IMG_2023-05-12-14-30-00.jpg"
        assert ms.parse_date_from_filename(fn1) == "2023:05:12 14:30:00"

        fn2 = "VID_2022_08_19_09_15_45.mp4"
        assert ms.parse_date_from_filename(fn2) == "2022:08:19 09:15:45"

    def test_compact_yyyymmdd_hhmmss(self):
        fn = "20240101_120000.png"
        assert ms.parse_date_from_filename(fn) == "2024:01:01 12:00:00"

    def test_short_yymmdd_hhmmss(self):
        # Year >= 70 -> 19xx
        assert ms.parse_date_from_filename("851230_102030.jpg") == "1985:12:30 10:20:30"
        # Year < 70 -> 20xx
        assert ms.parse_date_from_filename("240615_183045.jpg") == "2024:06:15 18:30:45"

    def test_non_matching_filename(self):
        assert ms.parse_date_from_filename("holiday_photo.jpg") is None
        assert ms.parse_date_from_filename("IMG_0001.JPG") is None


class TestFileCollisionResolution:
    def test_same_filename_returns_as_is(self, tmp_path):
        res = ms.get_unique_filepath(str(tmp_path), "test.jpg", "test.jpg")
        assert res == "test.jpg"

    def test_no_existing_file_returns_proposed(self, tmp_path):
        res = ms.get_unique_filepath(str(tmp_path), "original.jpg", "240615_Berlin.jpg")
        assert res == "240615_Berlin.jpg"

    def test_collision_appends_suffix(self, tmp_path):
        (tmp_path / "photo.jpg").write_text("exists")
        res1 = ms.get_unique_filepath(str(tmp_path), "orig.jpg", "photo.jpg")
        assert res1 == "photo_1.jpg"

        (tmp_path / "photo_1.jpg").write_text("exists too")
        res2 = ms.get_unique_filepath(str(tmp_path), "orig.jpg", "photo.jpg")
        assert res2 == "photo_2.jpg"


class TestGeocoding:
    def test_cache_hit_returns_cached_city(self, monkeypatch):
        ms.GPS_CITY_CACHE[(52.52, 13.405)] = "CachedBerlin"
        # Calling get_city_from_gps should immediately return from cache without network
        res = ms.get_city_from_gps(52.52, 13.405)
        assert res == "CachedBerlin"

    def test_mock_network_geocoding(self, monkeypatch):
        # Clear cache for this coordinate
        ms.GPS_CITY_CACHE.pop((48.8566, 2.3522), None)

        fake_json_response = json.dumps({
            "address": {
                "city": "Paris",
                "country": "France"
            }
        }).encode("utf-8")

        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return fake_json_response

        def fake_urlopen(*args, **kwargs):
            return FakeResponse()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        city = ms.get_city_from_gps(48.8566, 2.3522)
        assert city == "Paris"


class TestMetadataExtraction:
    def test_get_image_metadata_full(self, tmp_path):
        img_path = str(tmp_path / "full_meta.jpg")
        make_test_image(
            img_path,
            width=640,
            height=480,
            date_taken="2024:06:15 14:30:00",
            gps=(52.52, 13.40),
            camera_make="Nikon",
            camera_model="Z6",
        )

        meta = ms.get_image_metadata(img_path)
        assert meta["Type"] == "Image"
        assert meta["Format"] == "JPEG"
        assert meta["Width"] == 640
        assert meta["Height"] == 480
        assert meta["Date Taken"] == "2024:06:15 14:30:00"
        assert meta["Camera Make"] == "Nikon"
        assert meta["Camera Model"] == "Z6"
        assert meta["GPS_Lat"] is not None
        assert abs(meta["GPS_Lat"] - 52.52) < 0.01
        assert abs(meta["GPS_Lon"] - 13.40) < 0.01
        assert meta["Error"] is None

    def test_get_image_metadata_filename_fallback(self, tmp_path):
        img_path = str(tmp_path / "20240315_102030.jpg")
        make_test_image(img_path, width=200, height=200)

        meta = ms.get_image_metadata(img_path)
        assert meta["Date Taken"] == "2024:03:15 10:20:30"

    def test_get_image_metadata_corrupt_file(self, tmp_path):
        corrupt_path = tmp_path / "bad.jpg"
        corrupt_path.write_bytes(b"not a valid image")

        meta = ms.get_image_metadata(str(corrupt_path))
        assert meta["Error"] is not None

    def test_get_video_metadata_valid(self, tmp_path):
        vid_path = str(tmp_path / "sample.mp4")
        make_test_video(vid_path, width=1280, height=720, duration_seconds=12)

        meta = ms.get_video_metadata(vid_path)
        assert meta["Type"] == "Video"
        assert meta["Width"] == 1280
        assert meta["Height"] == 720
        assert meta["Duration"] == "00:12"
        assert meta["Date Created"] is not None
        assert meta["Date Created"] != "Unknown"
        assert meta["Error"] is None

    def test_get_video_metadata_corrupt_file(self, tmp_path):
        bad_vid = tmp_path / "corrupt.mp4"
        bad_vid.write_bytes(b"empty")

        meta = ms.get_video_metadata(str(bad_vid))
        # Corrupt file won't find atoms, returns unknown dimensions or error
        assert meta["Type"] == "Video"
        assert meta["Width"] == "Unknown"


class TestProposedFilenameAndDestinations:
    def test_get_file_datetime(self):
        img_meta = {"Type": "Image", "Date Taken": "2024:06:15 14:30:00"}
        dt = ms.get_file_datetime(img_meta)
        assert isinstance(dt, datetime)
        assert dt.year == 2024
        assert dt.month == 6
        assert dt.day == 15

        vid_meta = {"Type": "Video", "Date Created": "2024-06-15 14:30:00"}
        dt_vid = ms.get_file_datetime(vid_meta)
        assert isinstance(dt_vid, datetime)

        # File system fallback dates are ignored for sorting/renaming
        fs_meta = {"Type": "Image", "Date Taken": "2024:06:15 14:30:00 (file system)"}
        assert ms.get_file_datetime(fs_meta) is None

    def test_get_proposed_filename(self, mock_geocoding):
        meta_with_gps = {
            "Type": "Image",
            "Date Taken": "2024:06:15 14:30:00",
            "GPS_Lat": 52.52,
            "GPS_Lon": 13.405,
        }
        res = ms.get_proposed_filename("original.jpg", meta_with_gps, ".jpg")
        assert res == "240615_143000_Berlin.jpg"

        meta_no_gps = {
            "Type": "Image",
            "Date Taken": "2024:06:15 14:30:00",
            "GPS_Lat": None,
            "GPS_Lon": None,
        }
        res_no_gps = ms.get_proposed_filename("original.jpg", meta_no_gps, ".jpg")
        assert res_no_gps == "240615_143000.jpg"


class TestDirectoryScanningAndSorting:
    def test_get_dir_stats(self, sample_media_dir):
        src = sample_media_dir["src"]
        stats = ms.get_dir_stats(src)

        # In sample_media_dir we created:
        # 4 images at root + 1 image in nested = 5 images
        # 1 video
        # 1 other file (readme.txt)
        assert stats["img_count"] == 5
        assert stats["vid_count"] == 1
        assert stats["other_count"] == 1
        assert stats["img_size"] > 0
        assert stats["vid_size"] > 0
        assert stats["other_size"] > 0

    def test_scan_directory_end_to_end_option1_skip_duplicates(self, sample_media_dir, monkeypatch):
        src = sample_media_dir["src"]
        dest = sample_media_dir["dest"]

        # Option 1: Year & Month (202406)
        # Rename: Yes ('y')
        # Duplicate: Skip ('1')
        # Confirm execution: Yes ('y')
        inputs = iter(["1", "y", "1", "y"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

        ms.scan_directory(src, results_dir=dest)

        # Verify summary.json was created
        summary_path = os.path.join(dest, "summary.json")
        assert os.path.exists(summary_path)

        with open(summary_path, "r", encoding="utf-8") as f:
            summary = json.load(f)

        assert "timestamp" in summary
        assert "initial_summary" in summary
        assert "final_summary" in summary
        assert summary["execution_actions"]["sorting_option"] == 1
        assert summary["execution_actions"]["duplicate_handling"] == "skip"
        assert summary["execution_results"]["duplicates_found"] == 1
        assert summary["execution_results"]["files_copied"] > 0
        assert len(summary["processed_files"]) > 0

        # Verify month folders created inside destination
        folders = [d for d in os.listdir(dest) if os.path.isdir(os.path.join(dest, d))]
        assert any(f.startswith("2024") for f in folders)

    def test_scan_directory_option3_keep_duplicates(self, sample_media_dir, monkeypatch):
        src = sample_media_dir["src"]
        dest = os.path.join(os.path.dirname(sample_media_dir["dest"]), "SortedOpt3")

        # Option 3: YearMonth_Location (e.g. 202406_Berlin)
        # Rename: Yes ('y')
        # Duplicate: Keep ('2')
        # Confirm execution: Yes ('y')
        inputs = iter(["3", "y", "2", "y"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

        ms.scan_directory(src, results_dir=dest)

        summary_path = os.path.join(dest, "summary.json")
        assert os.path.exists(summary_path)

        with open(summary_path, "r", encoding="utf-8") as f:
            summary = json.load(f)

        assert summary["execution_actions"]["sorting_option"] == 3
        assert summary["execution_actions"]["duplicate_handling"] == "keep"
        assert summary["execution_results"]["duplicates_found"] == 1
        assert summary["execution_results"]["files_copied"] == 6
        # Check folder structure
        folders = os.listdir(dest)
        assert any("Berlin" in f for f in folders)
