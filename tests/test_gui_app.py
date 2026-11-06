import os
import time
import json
import pytest
from starlette.testclient import TestClient

import scripting.gui_app as gui_app
from scripting.gui_app import app


@pytest.fixture
def client():
    # Reset app state before each test
    gui_app.state = gui_app.AppState()
    return TestClient(app)


class TestBasicEndpoints:
    def test_read_root(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

    def test_get_status_idle(self, client):
        response = client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "idle"
        assert "summary" in data
        assert "logs" in data

    def test_reset_status(self, client):
        gui_app.state.status = "error"
        gui_app.state.error_message = "Something went wrong"

        response = client.post("/api/reset")
        assert response.status_code == 200
        assert response.json()["status"] == "reset"

        # Check state is back to idle
        assert gui_app.state.status == "idle"
        assert gui_app.state.error_message == ""

    def test_select_folder(self, client, monkeypatch, tmp_path):
        dummy_dir = str(tmp_path / "my_photos")
        os.makedirs(dummy_dir, exist_ok=True)

        monkeypatch.setattr(gui_app, "ask_directory_dialog", lambda: dummy_dir)
        response = client.post("/api/select-folder")
        assert response.status_code == 200
        assert response.json()["folder"] == os.path.abspath(dummy_dir).replace("\\", "/")


class TestMediaInspectionEndpoints:
    def test_thumbnail_image(self, client, sample_media_dir):
        img_path = sample_media_dir["files"]["img_gps"]
        response = client.get(f"/api/thumbnail?path={img_path}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert len(response.content) > 0

    def test_thumbnail_video_fallback(self, client, sample_media_dir):
        vid_path = sample_media_dir["files"]["video"]
        response = client.get(f"/api/thumbnail?path={vid_path}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    def test_thumbnail_not_found(self, client, tmp_path):
        fake_path = str(tmp_path / "ghost.jpg")
        response = client.get(f"/api/thumbnail?path={fake_path}")
        assert response.status_code == 404

    def test_gps_clusters(self, client, sample_media_dir):
        src = sample_media_dir["src"]
        response = client.get(f"/api/gps-clusters?source={src}")
        assert response.status_code == 200
        data = response.json()
        assert "clusters" in data
        clusters = data["clusters"]
        assert isinstance(clusters, list)
        assert len(clusters) > 0
        
        # Verify cluster fields
        c = clusters[0]
        assert "avg_lat" in c
        assert "avg_lon" in c
        assert "city" in c
        assert "count" in c
        assert "files" in c
        assert c["city"] == "Berlin"

    def test_gps_clusters_invalid_source(self, client, tmp_path):
        ghost_dir = str(tmp_path / "does_not_exist")
        response = client.get(f"/api/gps-clusters?source={ghost_dir}")
        assert response.status_code == 400


class TestAnalysisAndCopyWorkflow:
    def test_full_analysis_and_proceed_lifecycle(self, client, sample_media_dir):
        src = sample_media_dir["src"]
        dest = sample_media_dir["dest"]

        payload = {
            "source": src,
            "destination": dest,
            "option": 1,
            "rename": True,
            "duplicate_opt": 1
        }

        # 1. Start Analysis
        start_resp = client.post("/api/analyze", json=payload)
        assert start_resp.status_code == 200
        assert start_resp.json()["status"] == "started"

        # 2. Wait for analysis thread to transition to confirming
        max_wait = 10.0
        start_time = time.time()
        while time.time() - start_time < max_wait:
            status_data = client.get("/api/status").json()
            if status_data["status"] == "confirming":
                break
            time.sleep(0.1)

        assert status_data["status"] == "confirming"
        assert status_data["metadata_report"]["both_ts_and_loc"] >= 1
        assert status_data["stats_before"]["img_count"] > 0

        # 3. Proceed with copy
        proceed_resp = client.post("/api/proceed")
        assert proceed_resp.status_code == 200
        assert proceed_resp.json()["status"] == "started"

        # 4. Wait for copy thread to complete
        start_time = time.time()
        while time.time() - start_time < max_wait:
            status_data = client.get("/api/status").json()
            if status_data["status"] == "completed":
                break
            time.sleep(0.1)

        assert status_data["status"] == "completed"
        assert status_data["summary"]["files_copied"] > 0
        assert status_data["summary"]["duplicates_skipped"] == 1

        # 5. Check destination on disk (gui_app writes to destination / "Sorted" / "summary.json")
        summary_file = os.path.join(dest, "Sorted", "summary.json")
        assert os.path.exists(summary_file)
        with open(summary_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["execution_results"]["files_copied"] > 0

    def test_analyze_and_cancel_lifecycle(self, client, sample_media_dir):
        src = sample_media_dir["src"]
        dest = sample_media_dir["dest"]

        payload = {
            "source": src,
            "destination": dest,
            "option": 1,
            "rename": True,
            "duplicate_opt": 1
        }

        client.post("/api/analyze", json=payload)

        # Wait for confirming state
        max_wait = 10.0
        start_time = time.time()
        while time.time() - start_time < max_wait:
            if client.get("/api/status").json()["status"] == "confirming":
                break
            time.sleep(0.1)

        # Cancel
        cancel_resp = client.post("/api/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"

        # Verify state is idle
        assert client.get("/api/status").json()["status"] == "idle"

    def test_abort_operation(self, client):
        # When not copying, abort raises 400
        assert client.post("/api/abort").status_code == 400

        # When status is copying, abort succeeds
        gui_app.state.status = "copying"
        response = client.post("/api/abort")
        assert response.status_code == 200
        assert response.json()["status"] == "aborting"
        assert gui_app.state.abort_requested is True

    def test_analyze_already_in_progress_returns_400(self, client, sample_media_dir):
        gui_app.state.status = "analyzing"
        payload = {
            "source": sample_media_dir["src"],
            "destination": sample_media_dir["dest"],
            "option": 1,
            "rename": True,
            "duplicate_opt": 1
        }
        resp = client.post("/api/analyze", json=payload)
        assert resp.status_code == 400
        assert "already in progress" in resp.json()["detail"]
