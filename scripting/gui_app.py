import os
import sys
import threading
import webbrowser
import time
import io
import tkinter as tk
from tkinter import filedialog
from datetime import datetime
from collections import Counter

import uvicorn
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel
from PIL import Image

# Ensure scripting folder is in python path to import metadata_sorter
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

import metadata_sorter

app = FastAPI(title="Media Sorter & GPS Map GUI")

# In-memory application state
class AppState:
    def __init__(self):
        self.status = "idle"  # idle, analyzing, confirming, copying, completed, error
        self.source = ""
        self.destination = ""
        self.logs = []
        self.progress_current = 0
        self.progress_total = 0
        self.progress_percent = 0
        self.error_message = ""
        self.abort_requested = False  # Flag to track if user wants to interrupt copying
        
        # Pre-execution statistics
        self.stats_before = {}
        # Post-execution statistics
        self.stats_after = {}
        # Metadata classification report
        self.metadata_report = {}
        
        # Temporary lists compiled in Pass 1
        self.files_to_process = []
        self.unique_with_ts = []
        self.unique_without_ts = []
        self.file_log_entries = {}
        self.folder_destinations = {}
        self.duplicates_found = 0
        self.files_copied = 0
        self.files_renamed = 0
        
        # Processed counts
        self.copied_images_count = 0
        self.copied_images_size = 0
        self.copied_videos_count = 0
        self.copied_videos_size = 0

state = AppState()

# Custom log function
def log_message(msg: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    state.logs.append(f"[{timestamp}] {msg}")

# Save original stdout
original_stdout = sys.stdout

# Redirect standard stdout print calls to our state logs
class LogRedirector:
    def __init__(self, original):
        self.original = original
    def write(self, message):
        self.original.write(message)
        clean_msg = message.strip()
        if clean_msg:
            log_message(clean_msg)
    def flush(self):
        self.original.flush()
    def isatty(self):
        return hasattr(self.original, 'isatty') and self.original.isatty()

def ask_directory_dialog():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    folder = filedialog.askdirectory(parent=root, title="Select Directory")
    root.destroy()
    return folder

@app.post("/api/select-folder")
def select_folder():
    """Opens OS file browser to select a folder."""
    try:
        folder_path = ask_directory_dialog()
        # Return path normalized with forward slashes for cross-platform compatibility
        return {"folder": os.path.abspath(folder_path).replace("\\", "/") if folder_path else ""}
    except Exception as e:
        log_message(f"Error opening folder picker: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/status")
def get_status():
    """Returns the current state of the analysis or copying process."""
    return {
        "status": state.status,
        "source": state.source,
        "destination": state.destination,
        "logs": state.logs,
        "progress_current": state.progress_current,
        "progress_total": state.progress_total,
        "progress_percent": state.progress_percent,
        "error_message": state.error_message,
        "stats_before": state.stats_before,
        "stats_after": state.stats_after,
        "metadata_report": state.metadata_report,
        "summary": {
            "files_copied": state.files_copied,
            "files_renamed": state.files_renamed,
            "duplicates_skipped": state.duplicates_found
        }
    }

@app.post("/api/reset")
def reset_status():
    """Resets the state back to idle."""
    global state
    state = AppState()
    return {"status": "reset"}

class AnalyzeRequest(BaseModel):
    source: str
    destination: str

def analysis_task_thread(source: str, destination: str):
    sys.stdout = LogRedirector(original_stdout)
    try:
        state.status = "analyzing"
        state.source = source
        state.destination = destination
        state.logs = []
        state.progress_current = 0
        state.progress_percent = 0
        
        log_message("Starting Initial Workspace Summary scan...")
        state.stats_before = metadata_sorter.get_dir_stats(source)
        log_message(f"Found {state.stats_before['img_count']} images and {state.stats_before['vid_count']} videos.")
        
        # Scan files
        files_to_process = []
        for root, dirs, files in os.walk(source):
            dirs.sort()
            files.sort()
            files = [f for f in files if not f.startswith('.')]
            for file in files:
                _, ext = os.path.splitext(file.lower())
                if ext in metadata_sorter.IMAGE_EXTENSIONS or ext in metadata_sorter.VIDEO_EXTENSIONS:
                    files_to_process.append((root, file))
                    
        state.files_to_process = files_to_process
        state.progress_total = len(files_to_process)
        
        if state.progress_total == 0:
            log_message("No supported images or videos found in the selected folder.")
            state.status = "idle"
            return
            
        state.unique_with_ts = []
        state.unique_without_ts = []
        state.file_log_entries = {}
        state.folder_destinations = {}
        state.duplicates_found = 0
        seen_hashes = {}
        
        log_message("Analyzing files and metadata...")
        
        for idx, (root, file) in enumerate(files_to_process, 1):
            filepath = os.path.join(root, file)
            _, ext = os.path.splitext(file.lower())
            
            state.progress_current = idx
            state.progress_percent = int((idx / state.progress_total) * 100)
            
            try:
                stat = os.stat(filepath)
                size = stat.st_size
            except FileNotFoundError:
                continue
                
            rel_path = os.path.relpath(filepath, source)
            
            state.file_log_entries[filepath] = {
                "relative_path": rel_path,
                "filename": file,
                "size_bytes": size,
                "size_formatted": metadata_sorter.format_size(size),
                "type": "Image" if ext in metadata_sorter.IMAGE_EXTENSIONS else "Video",
                "duplicate_details": {
                    "is_duplicate": False,
                    "duplicate_of": None,
                    "status": "Unique"
                },
                "metadata": {},
                "rename_details": {
                    "proposed_name": None,
                    "status": "No rename proposed"
                },
                "copy_details": {
                    "destination": None,
                    "status": "No copy proposed"
                }
            }
            
            # Duplicate check
            file_hash = metadata_sorter.get_file_hash(filepath)
            if file_hash:
                if file_hash in seen_hashes:
                    state.duplicates_found += 1
                    original_rel_path = seen_hashes[file_hash]
                    state.file_log_entries[filepath]["duplicate_details"] = {
                        "is_duplicate": True,
                        "duplicate_of": original_rel_path,
                        "status": "Skipped (Duplicate)"
                    }
                    log_message(f"Skipped duplicate: {rel_path} (identical to {original_rel_path})")
                    continue
                else:
                    seen_hashes[file_hash] = rel_path
                    
            # Extract metadata
            meta = None
            if ext in metadata_sorter.IMAGE_EXTENSIONS:
                meta = metadata_sorter.get_image_metadata(filepath)
            elif ext in metadata_sorter.VIDEO_EXTENSIONS:
                meta = metadata_sorter.get_video_metadata(filepath)
                
            if meta is None:
                meta = {"Type": "Image" if ext in metadata_sorter.IMAGE_EXTENSIONS else "Video", "Error": "Failed to parse metadata"}
                
            proposed = metadata_sorter.get_proposed_filename(filepath, meta, ext)
            unique_name = file
            rename_status = "No rename proposed"
            if proposed:
                unique_name = metadata_sorter.get_unique_filepath(root, file, proposed)
                if unique_name.lower() != file.lower():
                    rename_status = "Renamed"
                else:
                    rename_status = "Already formatted"
                    
            # Populate logs
            meta_log = {
                "width": meta.get("Width"),
                "height": meta.get("Height")
            }
            if meta.get("Width") != "Unknown" and meta.get("Height") != "Unknown":
                meta_log["dimensions"] = f"{meta['Width']} x {meta['Height']} px"
            
            if ext in metadata_sorter.IMAGE_EXTENSIONS:
                state.file_log_entries[filepath]["type"] = f"{meta.get('Format', 'Unknown')} Image"
                meta_log["format"] = meta.get("Format")
                meta_log["date_taken"] = meta.get("Date Taken", "Unknown")
                camera = f"{meta.get('Camera Make', '')} {meta.get('Camera Model', '')}".strip()
                meta_log["camera_model"] = camera if camera and camera != "Unknown Unknown" and camera != "" else None
                meta_log["gps_location"] = meta.get("GPS")
                if meta.get("GPS_Lat") is not None:
                    # Retrieve city directly from cache or API
                    meta_log["city"] = metadata_sorter.get_city_from_gps(meta["GPS_Lat"], meta["GPS_Lon"])
            else:
                meta_log["duration"] = meta.get("Duration")
                meta_log["bitrate"] = meta.get("Bitrate")
                meta_log["date_created"] = meta.get("Date Created")
                
            state.file_log_entries[filepath]["metadata"] = meta_log
            state.file_log_entries[filepath]["rename_details"] = {
                "proposed_name": unique_name if proposed else None,
                "status": rename_status
            }
            
            if not meta.get("Error"):
                dt = metadata_sorter.get_file_datetime(meta)
                file_data = {
                    "root": root, "file": file, "meta": meta, "file_hash": file_hash,
                    "size": size, "ext": ext, "filepath": filepath, "unique_name": unique_name,
                    "rename_status": rename_status
                }
                if dt:
                    file_data["datetime"] = dt
                    state.unique_with_ts.append(file_data)
                else:
                    state.unique_without_ts.append(file_data)
            else:
                file_data = {
                    "root": root, "file": file, "meta": {"Type": "Image" if ext in metadata_sorter.IMAGE_EXTENSIONS else "Video", "Error": "Failed to parse metadata"},
                    "file_hash": file_hash, "size": size, "ext": ext, "filepath": filepath,
                    "unique_name": unique_name, "rename_status": rename_status
                }
                state.unique_without_ts.append(file_data)

        # Build analysis report stats
        both_ts_and_loc = 0
        only_ts = 0
        only_loc = 0
        neither_ts_nor_loc = 0
        
        for file_data in state.unique_with_ts:
            meta = file_data["meta"]
            lat = meta.get("GPS_Lat")
            lon = meta.get("GPS_Lon")
            has_loc = (lat is not None and lon is not None)
            if has_loc:
                both_ts_and_loc += 1
            else:
                only_ts += 1
                
        for file_data in state.unique_without_ts:
            meta = file_data["meta"]
            lat = meta.get("GPS_Lat")
            lon = meta.get("GPS_Lon")
            has_loc = (lat is not None and lon is not None)
            if has_loc:
                only_loc += 1
            else:
                neither_ts_nor_loc += 1
                
        state.metadata_report = {
            "both_ts_and_loc": both_ts_and_loc,
            "only_ts": only_ts,
            "only_loc": only_loc,
            "neither_ts_nor_loc": neither_ts_nor_loc,
            "other_files": state.stats_before["other_count"]
        }
        
        log_message("Analysis completed. Awaiting user confirmation to proceed.")
        state.status = "confirming"
        
    except Exception as e:
        log_message(f"Error during analysis: {e}")
        state.status = "error"
        state.error_message = str(e)
    finally:
        sys.stdout = original_stdout

@app.post("/api/analyze")
def analyze_directory(request: AnalyzeRequest, background_tasks: BackgroundTasks):
    """Triggers analysis in a background thread."""
    if state.status not in ("idle", "completed", "error"):
        raise HTTPException(status_code=400, detail="An operation is already in progress.")
        
    background_tasks.add_task(analysis_task_thread, request.source, request.destination)
    return {"status": "started"}

def copy_task_thread():
    sys.stdout = LogRedirector(original_stdout)
    try:
        state.status = "copying"
        state.files_copied = 0
        state.files_renamed = 0
        state.copied_images_count = 0
        state.copied_images_size = 0
        state.copied_videos_count = 0
        state.copied_videos_size = 0
        copied_index = 0
        
        total_to_copy = len(state.unique_with_ts) + len(state.unique_without_ts)
        state.progress_total = total_to_copy
        state.progress_current = 0
        state.progress_percent = 0
        
        # Ensure destination folder is results_dir / "Sorted"
        actual_destination = os.path.join(state.destination, "Sorted")
        
        log_message(f"Starting copy and rename phase into: {actual_destination}")
        
        # Second Pass: Process files with timestamps
        if state.unique_with_ts:
            log_message("Copying files with valid timestamps...")
            for file_data in state.unique_with_ts:
                if state.abort_requested:
                    log_message("Copy operation interrupted by user.")
                    state.status = "completed"
                    break
                
                copied_index += 1
                state.progress_current = copied_index
                state.progress_percent = int((copied_index / total_to_copy) * 100)
                
                root = file_data["root"]
                filepath = file_data["filepath"]
                dt = file_data["datetime"]
                unique_name = file_data["unique_name"]
                ext = file_data["ext"]
                size = file_data.get("size", 0)
                
                folder_name = dt.strftime("%Y%m")
                final_name = unique_name
                dest_folder = os.path.join(actual_destination, folder_name)
                
                parent_path = os.path.abspath(root)
                if parent_path not in state.folder_destinations:
                    state.folder_destinations[parent_path] = []
                state.folder_destinations[parent_path].append(folder_name)
                
                try:
                    os.makedirs(dest_folder, exist_ok=True)
                    dest_unique_name = metadata_sorter.get_unique_filepath(dest_folder, final_name, final_name)
                    dest_filepath = os.path.join(dest_folder, dest_unique_name)
                    
                    import shutil
                    shutil.copy2(filepath, dest_filepath)
                    state.files_copied += 1
                    time.sleep(0.1)
                    
                    if dest_unique_name.lower() != file_data["file"].lower():
                        state.files_renamed += 1
                        
                    if ext in metadata_sorter.IMAGE_EXTENSIONS:
                        state.copied_images_count += 1
                        state.copied_images_size += size
                    elif ext in metadata_sorter.VIDEO_EXTENSIONS:
                        state.copied_videos_count += 1
                        state.copied_videos_size += size
                        
                    rel_dest_print = os.path.join("Sorted", folder_name, dest_unique_name)
                    state.file_log_entries[filepath]["copy_details"] = {
                        "destination": rel_dest_print,
                        "status": "Copied"
                    }
                except Exception as e:
                    log_message(f"Error copying {filepath}: {e}")
                    
        # Third Pass: Process files without timestamps using parent folder majority destinations
        if state.unique_without_ts and not state.abort_requested:
            log_message("Copying files without timestamps (folder majority)...")
            
            all_dests = []
            for dests in state.folder_destinations.values():
                all_dests.extend(dests)
            global_majority = Counter(all_dests).most_common(1)[0][0] if all_dests else "unknown"
            
            for file_data in state.unique_without_ts:
                if state.abort_requested:
                    log_message("Copy operation interrupted by user.")
                    state.status = "completed"
                    break
                
                copied_index += 1
                state.progress_current = copied_index
                state.progress_percent = int((copied_index / total_to_copy) * 100)
                
                root = file_data["root"]
                filepath = file_data["filepath"]
                unique_name = file_data["unique_name"]
                ext = file_data["ext"]
                size = file_data.get("size", 0)
                
                parent_path = os.path.abspath(root)
                folder_dests = state.folder_destinations.get(parent_path, [])
                basis_str = f"based on {len(folder_dests)} other files in folder" if folder_dests else "global fallback, folder has no sorted files"
                
                if folder_dests:
                    majority_folder = Counter(folder_dests).most_common(1)[0][0]
                else:
                    majority_folder = global_majority
                    
                dest_folder = os.path.join(actual_destination, majority_folder)
                final_name = unique_name
                
                try:
                    os.makedirs(dest_folder, exist_ok=True)
                    dest_unique_name = metadata_sorter.get_unique_filepath(dest_folder, final_name, final_name)
                    dest_filepath = os.path.join(dest_folder, dest_unique_name)
                    
                    import shutil
                    shutil.copy2(filepath, dest_filepath)
                    state.files_copied += 1
                    time.sleep(0.1)
                    
                    if dest_unique_name.lower() != file_data["file"].lower():
                        state.files_renamed += 1
                        
                    if ext in metadata_sorter.IMAGE_EXTENSIONS:
                        state.copied_images_count += 1
                        state.copied_images_size += size
                    elif ext in metadata_sorter.VIDEO_EXTENSIONS:
                        state.copied_videos_count += 1
                        state.copied_videos_size += size
                        
                    rel_dest_print = os.path.join("Sorted", majority_folder, dest_unique_name)
                    state.file_log_entries[filepath]["copy_details"] = {
                        "destination": rel_dest_print,
                        "status": f"Copied to majority folder '{majority_folder}'",
                        "folder_majority": majority_folder,
                        "majority_basis": basis_str
                    }
                except Exception as e:
                    log_message(f"Error copying {filepath}: {e}")
                    
        # Compute final summary
        state.stats_after = {
            "img_count": state.copied_images_count,
            "img_size": state.copied_images_size,
            "vid_count": state.copied_videos_count,
            "vid_size": state.copied_videos_size,
            "other_count": 0,
            "other_size": 0
        }
        
        # Save JSON Summary
        summary_data = {
            "timestamp": datetime.now().isoformat(),
            "selected_directory": state.source,
            "results_directory": actual_destination,
            "initial_summary": {
                "images": { "count": state.stats_before['img_count'], "size_bytes": state.stats_before['img_size'], "size_formatted": metadata_sorter.format_size(state.stats_before['img_size']) },
                "videos": { "count": state.stats_before['vid_count'], "size_bytes": state.stats_before['vid_size'], "size_formatted": metadata_sorter.format_size(state.stats_before['vid_size']) },
                "other_files": { "count": state.stats_before['other_count'], "size_bytes": state.stats_before['other_size'], "size_formatted": metadata_sorter.format_size(state.stats_before['other_size']) },
                "total": { 
                    "count": state.stats_before['img_count'] + state.stats_before['vid_count'] + state.stats_before['other_count'], 
                    "size_bytes": state.stats_before['img_size'] + state.stats_before['vid_size'] + state.stats_before['other_size'], 
                    "size_formatted": metadata_sorter.format_size(state.stats_before['img_size'] + state.stats_before['vid_size'] + state.stats_before['other_size']) 
                }
            },
            "execution_results": {
                "files_renamed": state.files_renamed,
                "duplicates_found": state.duplicates_found,
                "duplicates_removed": 0,
                "files_copied": state.files_copied
            },
            "final_summary": {
                "images": { "count": state.stats_after['img_count'], "size_bytes": state.stats_after['img_size'], "size_formatted": metadata_sorter.format_size(state.stats_after['img_size']) },
                "videos": { "count": state.stats_after['vid_count'], "size_bytes": state.stats_after['vid_size'], "size_formatted": metadata_sorter.format_size(state.stats_after['vid_size']) },
                "other_files": { "count": state.stats_after['other_count'], "size_bytes": state.stats_after['other_size'], "size_formatted": metadata_sorter.format_size(state.stats_after['other_size']) },
                "total": { 
                    "count": state.stats_after['img_count'] + state.stats_after['vid_count'] + state.stats_after['other_count'], 
                    "size_bytes": state.stats_after['img_size'] + state.stats_after['vid_size'] + state.stats_after['other_size'], 
                    "size_formatted": metadata_sorter.format_size(state.stats_after['img_size'] + state.stats_after['vid_size'] + state.stats_after['other_size']) 
                }
            },
            "processed_files": list(state.file_log_entries.values())
        }
        
        os.makedirs(actual_destination, exist_ok=True)
        json_path = os.path.join(actual_destination, "summary.json")
        import json
        with open(json_path, 'w', encoding='utf-8') as json_file:
            json.dump(summary_data, json_file, indent=2, ensure_ascii=False)
            
        log_message(f"Summary successfully written to: {json_path}")
        if state.abort_requested:
            log_message("Copy operation stopped. Some files were copied.")
        else:
            log_message("Copy operation completed successfully!")
        state.status = "completed"
        
    except Exception as e:
        log_message(f"Error during copy operation: {e}")
        state.status = "error"
        state.error_message = str(e)
    finally:
        sys.stdout = original_stdout

@app.post("/api/proceed")
def proceed_copy(background_tasks: BackgroundTasks):
    """Proceeds with renaming/copying files after analysis."""
    if state.status != "confirming":
        raise HTTPException(status_code=400, detail="No active analysis is awaiting confirmation.")
        
    background_tasks.add_task(copy_task_thread)
    return {"status": "started"}

@app.post("/api/cancel")
def cancel_copy():
    """Cancels/aborts copying after analysis, resetting to idle."""
    global state
    if state.status != "confirming":
        raise HTTPException(status_code=400, detail="Nothing to cancel.")
        
    state = AppState()
    return {"status": "cancelled"}

@app.post("/api/abort")
def abort_copy():
    """Interrupts/aborts the copy operation while it is running."""
    if state.status != "copying":
        raise HTTPException(status_code=400, detail="No active copy process to abort.")
    state.abort_requested = True
    log_message("Abort request received. Stopping copy process...")
    return {"status": "aborting"}

@app.get("/api/gps-clusters")
def get_gps_clusters(source: str):
    """
    Recursively scans the directory, retrieves GPS coordinates, and groups them
    into spatial clusters representing cities/regions to avoid map lag.
    """
    if not source or not os.path.exists(source):
        raise HTTPException(status_code=400, detail="Source path does not exist.")
        
    locations = []
    
    # 1. Scan and extract all coordinates
    for root, dirs, files in os.walk(source):
        files = [f for f in files if not f.startswith('.')]
        for file in files:
            filepath = os.path.join(root, file)
            _, ext = os.path.splitext(file.lower())
            
            if ext in metadata_sorter.IMAGE_EXTENSIONS:
                meta = metadata_sorter.get_image_metadata(filepath)
                if meta and meta.get("GPS_Lat") is not None and meta.get("GPS_Lon") is not None:
                    locations.append({
                        "filepath": filepath.replace("\\", "/"),
                        "filename": file,
                        "lat": meta["GPS_Lat"],
                        "lon": meta["GPS_Lon"],
                        "date_taken": meta.get("Date Taken", "Unknown"),
                        "type": "image"
                    })
            elif ext in metadata_sorter.VIDEO_EXTENSIONS:
                meta = metadata_sorter.get_video_metadata(filepath)
                if meta and meta.get("GPS_Lat") is not None and meta.get("GPS_Lon") is not None:
                    locations.append({
                        "filepath": filepath.replace("\\", "/"),
                        "filename": file,
                        "lat": meta["GPS_Lat"],
                        "lon": meta["GPS_Lon"],
                        "date_taken": meta.get("Date Created", "Unknown"),
                        "type": "video"
                    })

    # 2. Perform clustering (spatial proximity threshold: 0.15 degrees, approx 15-20km)
    clusters = []
    for loc in locations:
        placed = False
        for c in clusters:
            lat_diff = abs(loc["lat"] - c["avg_lat"])
            lon_diff = abs(loc["lon"] - c["avg_lon"])
            if lat_diff < 0.15 and lon_diff < 0.15:
                c["files"].append(loc)
                # Recalculate average coordinates of cluster
                c["avg_lat"] = sum(f["lat"] for f in c["files"]) / len(c["files"])
                c["avg_lon"] = sum(f["lon"] for f in c["files"]) / len(c["files"])
                placed = True
                break
        if not placed:
            clusters.append({
                "id": len(clusters),
                "avg_lat": loc["lat"],
                "avg_lon": loc["lon"],
                "files": [loc]
            })

    # 3. Resolve city name for each cluster center using Nominatim
    for c in clusters:
        city = metadata_sorter.get_city_from_gps(c["avg_lat"], c["avg_lon"])
        if not city:
            city = f"Region near {c['avg_lat']:.2f}, {c['avg_lon']:.2f}"
        c["city"] = city
        c["count"] = len(c["files"])

    return {"clusters": clusters}

@app.get("/api/thumbnail")
def get_thumbnail(path: str = Query(..., description="Absolute path to the media file")):
    """Generates and serves a small 200px thumbnail of the image for Leaflet map previews."""
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
        
    _, ext = os.path.splitext(path.lower())
    
    # If it is an image, make a thumbnail
    if ext in metadata_sorter.IMAGE_EXTENSIONS:
        try:
            with Image.open(path) as img:
                img.thumbnail((200, 200))
                buf = io.BytesIO()
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.save(buf, format="JPEG", quality=80)
                return Response(content=buf.getvalue(), media_type="image/jpeg")
        except Exception as e:
            # Fallback to generic icon if image opening fails
            pass
            
    # For videos or failed images, return a generic placeholder
    placeholder_path = os.path.join(script_dir, "static", "placeholder.png")
    if os.path.exists(placeholder_path):
        with open(placeholder_path, "rb") as f:
            return Response(content=f.read(), media_type="image/png")
            
    # Inline fallback 1x1 transparent PNG if placeholder is missing
    transparent_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x01\x00\x00\x0c\x00\x01\x07\xcd\xf3\xb0\x00\x00\x00\x00IEND\xaeB`\x82'
    return Response(content=transparent_png, media_type="image/png")

# Serve the HTML page directly on root
@app.get("/", response_class=HTMLResponse)
def read_root():
    html_path = os.path.join(script_dir, "static", "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    else:
        return "<h3>GUI Frontend Assets not found. Please verify the 'static' folder exists inside scripting.</h3>"

# Mount static folder
static_path = os.path.join(script_dir, "static")
os.makedirs(static_path, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_path), name="static")

port = int(os.environ.get("PORT", 8000))
def open_browser():
    time.sleep(1.0)
    webbrowser.open(f"http://127.0.0.1:{port}")

if __name__ == "__main__":
    # Start web browser in background thread
    threading.Thread(target=open_browser, daemon=True).start()
    
    # Run server
    uvicorn.run(app, host="127.0.0.1", port=port)
