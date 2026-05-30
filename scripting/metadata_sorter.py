import os
import sys
import re
import struct
import ssl
import json
import time
import urllib.request
import hashlib
import shutil
from datetime import datetime
from collections import Counter
from PIL import Image
from PIL.ExifTags import TAGS

# Reconfigure stdout/stderr to UTF-8 on Windows to prevent UnicodeEncodeError in legacy consoles
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.tiff', '.webp', '.bmp'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.m4v', '.webm'}

# In-memory cache for GPS coordinate to city mappings to prevent excessive network requests
GPS_CITY_CACHE = {}

def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"

def format_duration(seconds):
    if seconds is None:
        return "Unknown"
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"

def get_file_hash(filepath):
    """Calculate SHA-256 hash of a file to uniquely identify duplicates."""
    hasher = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception as e:
        print(f"  Error calculating hash for {filepath}: {e}")
        return None

def get_city_from_gps(lat, lon):
    cache_key = (round(float(lat), 4), round(float(lon), 4))
    if cache_key in GPS_CITY_CACHE:
        return GPS_CITY_CACHE[cache_key]
        
    print(f"  Fetching city name for GPS location ({lat:.6f}, {lon:.6f})...")
    # Respect Nominatim rate limit (maximum 1 request per second)
    time.sleep(1.0)
    url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}"
    req = urllib.request.Request(
        url,
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AntigravityGeocoding/1.0'}
    )
    
    # Empty SSL Context to bypass Windows cert store bug
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                address = data.get("address", {})
                city = address.get("city") or address.get("town") or address.get("village") or address.get("municipality") or address.get("suburb")
                if city:
                    # Clean city name for filesystem safety
                    city = "".join(c for c in city if c.isalnum() or c in (" ", "_", "-")).strip()
                    GPS_CITY_CACHE[cache_key] = city
                    try:
                        print(f"  City identified: {city}")
                    except UnicodeEncodeError:
                        safe_city = city.encode('ascii', errors='replace').decode('ascii')
                        print(f"  City identified: {safe_city} (unicode replaced for display)")
                    return city
                else:
                    print("  GPS location resolved, but no city name was found in address details.")
                    break
        except Exception as e:
            if attempt < 2:
                time.sleep(0.5)
            else:
                try:
                    print(f"  Failed to retrieve city from GPS: {e}")
                except UnicodeEncodeError:
                    print("  Failed to retrieve city from GPS due to terminal printing error.")
                
    GPS_CITY_CACHE[cache_key] = None
    return None

def parse_date_from_filename(filename):
    # Try WhatsApp format: "WhatsApp Image 2024-11-11 at 11.25.26.jpeg"
    m = re.search(r'(20\d{2})[-_](\d{2})[-_](\d{2})\s+at\s+(\d{2})\.(\d{2})\.(\d{2})', filename, re.IGNORECASE)
    if m:
        return f"{m.group(1)}:{m.group(2)}:{m.group(3)} {m.group(4)}:{m.group(5)}:{m.group(6)}"
        
    # Try YYYY-MM-DD-HH-MM-SS or YYYY_MM_DD_HH_MM_SS
    m = re.search(r'(20\d{2})[-_](\d{2})[-_](\d{2})[-_](\d{2})[-_](\d{2})[-_](\d{2})', filename)
    if m:
        return f"{m.group(1)}:{m.group(2)}:{m.group(3)} {m.group(4)}:{m.group(5)}:{m.group(6)}"

    # Try YYYYMMDD_HHMMSS
    m = re.search(r'(20\d{2})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})', filename)
    if m:
        return f"{m.group(1)}:{m.group(2)}:{m.group(3)} {m.group(4)}:{m.group(5)}:{m.group(6)}"

    # Try YYMMDD_HHMMSS (6 digits _ 6 digits, e.g. 151230_133136)
    m = re.search(r'(?<!\d)(\d{2})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})(?!\d)', filename)
    if m:
        yy = int(m.group(1))
        # Guess century: if yy >= 70, assume 19yy, else 20yy
        century = "19" if yy >= 70 else "20"
        return f"{century}{m.group(1)}:{m.group(2)}:{m.group(3)} {m.group(4)}:{m.group(5)}:{m.group(6)}"

    return None

def get_image_metadata(filepath):
    info = {
        "Type": "Image",
        "Width": "Unknown",
        "Height": "Unknown",
        "Date Taken": "Unknown",
        "Camera Make": "Unknown",
        "Camera Model": "Unknown",
        "Format": "Unknown",
        "GPS": None,
        "GPS_Lat": None,
        "GPS_Lon": None,
        "Error": None
    }
    try:
        with Image.open(filepath) as img:
            info["Format"] = img.format
            info["Width"], info["Height"] = img.size
            
            exif = img.getexif()
            
            # --- DATE EXTRACTION ---
            date_found = None
            
            def is_valid_exif_date(d_str):
                if not d_str or not isinstance(d_str, str):
                    return False
                d_str = d_str.strip()
                if len(d_str) < 10 or d_str.startswith("0000:00:00"):
                    return False
                return True

            # 1. Check Standard sub-IFD (0x8769) for DateTimeOriginal (36867), DateTimeDigitized (36868), DateTime (306)
            for tag_val in (36867, 36868, 306):
                try:
                    exif_ifd = exif.get_ifd(0x8769) if exif else None
                    if exif_ifd and tag_val in exif_ifd:
                        val = exif_ifd[tag_val]
                        if is_valid_exif_date(val):
                            date_found = val.strip()
                            break
                except Exception:
                    pass
                
                # Check main EXIF
                try:
                    if exif and tag_val in exif:
                        val = exif[tag_val]
                        if is_valid_exif_date(val):
                            date_found = val.strip()
                            break
                except Exception:
                    pass
            
            # 2. Check main EXIF dict keys loop as fallback
            if not date_found and exif:
                for tag_id, value in exif.items():
                    tag = TAGS.get(tag_id, tag_id)
                    if tag in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
                        if is_valid_exif_date(value):
                            date_found = value.strip()
                            break
                            
            # 3. Check GPS DateStamp / TimeStamp in GPS IFD (0x8825)
            if not date_found:
                try:
                    gps_ifd = exif.get_ifd(0x8825) if exif else None
                    if gps_ifd:
                        date_stamp = gps_ifd.get(29) # GPSDateStamp, string "YYYY:MM:DD"
                        time_stamp = gps_ifd.get(7)  # GPSTimeStamp, tuple of rationals
                        if date_stamp and time_stamp:
                            # Convert time_stamp components to integers
                            h = int(time_stamp[0][0] / time_stamp[0][1]) if hasattr(time_stamp[0], '__len__') else int(time_stamp[0])
                            m = int(time_stamp[1][0] / time_stamp[1][1]) if hasattr(time_stamp[1], '__len__') else int(time_stamp[1])
                            s = int(time_stamp[2][0] / time_stamp[2][1]) if hasattr(time_stamp[2], '__len__') else int(time_stamp[2])
                            date_stamp_clean = date_stamp.replace("-", ":")
                            date_found = f"{date_stamp_clean} {h:02d}:{m:02d}:{s:02d}"
                except Exception:
                    pass
                    
            # 4. Check Filename Regex
            if not date_found:
                filename = os.path.basename(filepath)
                parsed_filename_date = parse_date_from_filename(filename)
                if parsed_filename_date:
                    date_found = parsed_filename_date
                    
            # 5. Check Filesystem Fallback (ctime / mtime minimum)
            if not date_found:
                try:
                    stat = os.stat(filepath)
                    ctime = stat.st_ctime
                    mtime = stat.st_mtime
                    earliest_time = min(ctime, mtime)
                    date_found = datetime.fromtimestamp(earliest_time).strftime("%Y:%m:%d %H:%M:%S") + " (file system)"
                except Exception:
                    pass
                    
            info["Date Taken"] = date_found if date_found else "Unknown"
            
            # --- OTHER EXIF METADATA ---
            if exif:
                for tag_id, value in exif.items():
                    tag = TAGS.get(tag_id, tag_id)
                    if tag == "Make":
                        info["Camera Make"] = value.strip() if isinstance(value, str) else value
                    elif tag == "Model":
                        info["Camera Model"] = value.strip() if isinstance(value, str) else value
            
            # GPS tag is 0x8825
            try:
                gps_ifd = exif.get_ifd(0x8825)
                if gps_ifd:
                    lat_ref = gps_ifd.get(1)
                    lat = gps_ifd.get(2)
                    lon_ref = gps_ifd.get(3)
                    lon = gps_ifd.get(4)
                    if lat and lon:
                        def convert_to_degrees(value):
                            d = float(value[0])
                            m = float(value[1])
                            s = float(value[2])
                            return d + (m / 60.0) + (s / 3600.0)
                        
                        lat_val = convert_to_degrees(lat)
                        if lat_ref == 'S':
                            lat_val = -lat_val
                        lon_val = convert_to_degrees(lon)
                        if lon_ref == 'W':
                            lon_val = -lon_val
                        info["GPS_Lat"] = lat_val
                        info["GPS_Lon"] = lon_val
                        info["GPS"] = f"{lat_val:.6f}, {lon_val:.6f}"
            except Exception:
                pass
    except Exception as e:
        info["Error"] = str(e)
    return info

def get_video_metadata(filepath):
    info = {
        "Type": "Video",
        "Width": "Unknown",
        "Height": "Unknown",
        "Duration": "Unknown",
        "Bitrate": "Unknown",
        "Date Created": "Unknown",
        "GPS_Lat": None,
        "GPS_Lon": None,
        "Error": None
    }
    try:
        stat = os.stat(filepath)
        size_bytes = stat.st_size
        
        # Filesystem creation time as default fallback
        try:
            ctime = stat.st_ctime
            info["Date Created"] = datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M:%S") + " (file system)"
        except Exception:
            pass
            
        with open(filepath, 'rb') as f:
            def find_atoms(f_in, start, end, target_types):
                f_in.seek(start)
                res = []
                while f_in.tell() < end:
                    header = f_in.read(8)
                    if len(header) < 8:
                        break
                    size, type_ = struct.unpack('>I4s', header)
                    type_ = type_.decode('latin1', errors='ignore')
                    if size == 1:
                        size = struct.unpack('>Q', f_in.read(8))[0]
                    elif size == 0:
                        size = end - f_in.tell() + 8
                    
                    pos = f_in.tell()
                    if type_ in target_types:
                        res.append((type_, pos - 8, size))
                        
                    if type_ in ('moov', 'trak', 'mdia', 'minf', 'stbl'):
                        res.extend(find_atoms(f_in, pos, pos + size - 8, target_types))
                        
                    f_in.seek(pos + size - 8)
                return res
            
            atoms = find_atoms(f, 0, size_bytes, ['mvhd', 'tkhd'])
            
            duration_sec = None
            width = None
            height = None
            
            for type_, offset, size in atoms:
                f.seek(offset + 8)
                version = f.read(1)[0]
                f.read(3) # flags
                
                if type_ == 'mvhd':
                    if version == 1:
                        c_time = struct.unpack('>Q', f.read(8))[0]
                        m_time = struct.unpack('>Q', f.read(8))[0]
                        timescale = struct.unpack('>I', f.read(4))[0]
                        duration = struct.unpack('>Q', f.read(8))[0]
                    else:
                        c_time = struct.unpack('>I', f.read(4))[0]
                        m_time = struct.unpack('>I', f.read(4))[0]
                        timescale = struct.unpack('>I', f.read(4))[0]
                        duration = struct.unpack('>I', f.read(4))[0]
                    
                    if timescale:
                        duration_sec = duration / timescale
                        
                    epoch_c_time = c_time - 2082844800
                    if epoch_c_time > 0:
                        info["Date Created"] = datetime.fromtimestamp(epoch_c_time).strftime('%Y-%m-%d %H:%M:%S')
                        
                elif type_ == 'tkhd':
                    if version == 1:
                        f.read(8 + 8 + 4 + 4 + 8)
                    else:
                        f.read(4 + 4 + 4 + 4 + 4)
                    f.read(8 + 2 + 2 + 2 + 2 + 36)
                    w_val = struct.unpack('>I', f.read(4))[0]
                    h_val = struct.unpack('>I', f.read(4))[0]
                    w = w_val >> 16
                    h = h_val >> 16
                    if w > 0 and h > 0:
                        width = w
                        height = h
            
            if duration_sec is not None:
                info["Duration"] = format_duration(duration_sec)
                if duration_sec > 0:
                    bitrate_bps = (size_bytes * 8) / duration_sec
                    info["Bitrate"] = f"{bitrate_bps / (1024 * 1024):.2f} Mbps"
            
            if width is not None:
                info["Width"] = width
            if height is not None:
                info["Height"] = height
                
    except Exception as e:
        info["Error"] = str(e)
        
    return info

def get_file_datetime(meta):
    """Extract a datetime object from the file metadata."""
    date_str = None
    date_format = None
    
    if meta["Type"] == "Image":
        val = meta.get("Date Taken")
        if val and val != "Unknown" and not val.endswith(" (file system)"):
            date_str = val
            date_format = "%Y:%m:%d %H:%M:%S"
    elif meta["Type"] == "Video":
        val = meta.get("Date Created")
        if val and val != "Unknown" and not val.endswith(" (file system)"):
            date_str = val
            date_format = "%Y-%m-%d %H:%M:%S"
            
    if not date_str:
        return None
        
    try:
        return datetime.strptime(date_str, date_format)
    except Exception:
        return None

def get_proposed_filename(filepath, meta, ext):
    lat = meta.get("GPS_Lat")
    lon = meta.get("GPS_Lon")
    
    city = None
    if lat is not None and lon is not None:
        fetched_city = get_city_from_gps(lat, lon)
        if fetched_city:
            city = fetched_city.replace(" ", "_")
            
    dt = get_file_datetime(meta)
    
    if dt:
        try:
            date_part = dt.strftime("%y%m%d_%H%M%S")
            if city:
                return f"{date_part}_{city}{ext.lower()}"
            else:
                return f"{date_part}{ext.lower()}"
        except Exception:
            return None
    else:
        if city:
            base, _ = os.path.splitext(os.path.basename(filepath))
            if not base.endswith(f"_{city}"):
                return f"{base}_{city}{ext.lower()}"
        return None

def get_unique_filepath(root, current_filename, proposed_name):
    if current_filename.lower() == proposed_name.lower():
        return proposed_name
        
    base, ext = os.path.splitext(proposed_name)
    counter = 1
    new_name = proposed_name
    while os.path.exists(os.path.join(root, new_name)):
        if new_name.lower() == current_filename.lower():
            return new_name
        new_name = f"{base}_{counter}{ext}"
        counter += 1
    return new_name

def get_dir_stats(directory):
    """Scan directory and compute counts and total sizes for images, videos, and other files."""
    img_count = 0
    img_size = 0
    vid_count = 0
    vid_size = 0
    other_count = 0
    other_size = 0
    
    for root, dirs, files in os.walk(directory):
        # Skip hidden files
        files = [f for f in files if not f.startswith('.')]
        for file in files:
            filepath = os.path.join(root, file)
            try:
                size = os.path.getsize(filepath)
            except OSError:
                continue
            _, ext = os.path.splitext(file.lower())
            if ext in IMAGE_EXTENSIONS:
                img_count += 1
                img_size += size
            elif ext in VIDEO_EXTENSIONS:
                vid_count += 1
                vid_size += size
            else:
                other_count += 1
                other_size += size
                
    return {
        "img_count": img_count, "img_size": img_size,
        "vid_count": vid_count, "vid_size": vid_size,
        "other_count": other_count, "other_size": other_size
    }

def print_progress(current, total, bar_length=30):
    """Displays a live updating console progress bar at the current cursor line."""
    percent = (current / total) * 100 if total > 0 else 100
    filled = int(percent / 100 * bar_length)
    bar = "#" * filled + "-" * (bar_length - filled)
    sys.stdout.write(f"\rProgress: [{bar}] {percent:.1f}% ({current}/{total} files)")
    sys.stdout.flush()

def scan_directory(directory, results_dir=None):
    # Find the Sorted folder (same level as selected folder if not specified)
    if not results_dir:
        parent_dir = os.path.dirname(directory)
        results_dir = os.path.join(parent_dir, "Sorted")
    
    # 1. Print Initial Workspace Summary
    stats_before = get_dir_stats(directory)
    print("=" * 80)
    print("INITIAL WORKSPACE SUMMARY (Pre-execution)")
    print("=" * 80)
    print(f"Images:      {stats_before['img_count']} files (Size: {format_size(stats_before['img_size'])})")
    print(f"Videos:      {stats_before['vid_count']} files (Size: {format_size(stats_before['vid_size'])})")
    print(f"Other files: {stats_before['other_count']} files (Size: {format_size(stats_before['other_size'])})")
    total_files_before = stats_before['img_count'] + stats_before['vid_count'] + stats_before['other_count']
    total_size_before = stats_before['img_size'] + stats_before['vid_size'] + stats_before['other_size']
    print(f"Total:       {total_files_before} files (Size: {format_size(total_size_before)})")
    print("=" * 80)
    print(f"SORT DESTINATION:  {results_dir}")
    print("=" * 80)
    
    file_count = 0
    image_count = 0
    video_count = 0
    total_size = 0
    duplicates_found = 0
    duplicates_removed = 0
    files_renamed = 0
    files_copied = 0
    delete_all_duplicates = False
    skip_all_duplicates = False
    
    # Store file hashes to detect duplicates: hash_value -> rel_path_of_original
    seen_hashes = {}
    
    # Collect only media files to process to ensure accurate progress reporting
    files_to_process = []
    for root, dirs, files in os.walk(directory):
        dirs.sort()
        files.sort()
        files = [f for f in files if not f.startswith('.')]
        for file in files:
            _, ext = os.path.splitext(file.lower())
            if ext in IMAGE_EXTENSIONS or ext in VIDEO_EXTENSIONS:
                files_to_process.append((root, file))
                
    total_files_to_process = len(files_to_process)
    
    # Structures to categorize unique files by timestamp availability
    unique_with_ts = []
    unique_without_ts = []
    
    # Track destinations chosen for each folder path: folder_path -> list of destination folder names (e.g. "201512")
    folder_destinations = {}
    
    # Dict to log file details for the JSON output report
    file_log_entries = {}
            
    # First Pass: duplicate checking and metadata parsing
    for idx, (root, file) in enumerate(files_to_process, 1):
        filepath = os.path.join(root, file)
        _, ext = os.path.splitext(file.lower())
        
        # Clear the progress bar line from previous iteration
        sys.stdout.write("\r" + " " * 80 + "\r")
        sys.stdout.flush()
        
        file_count += 1
        try:
            stat = os.stat(filepath)
            size = stat.st_size
        except FileNotFoundError:
            # File was already renamed/deleted
            continue
            
        rel_path = os.path.relpath(filepath, directory)
        
        # Initialize default JSON log entry for the file
        file_log_entries[filepath] = {
            "relative_path": rel_path,
            "filename": file,
            "size_bytes": size,
            "size_formatted": format_size(size),
            "type": "Image" if ext in IMAGE_EXTENSIONS else "Video",
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
        
        # --- DUPLICATE DETECTION ---
        file_hash = get_file_hash(filepath)
        if file_hash:
            if file_hash in seen_hashes:
                duplicates_found += 1
                original_rel_path = seen_hashes[file_hash]
                
                try:
                    print(f"\n({idx}/{total_files_to_process}) {rel_path}")
                except UnicodeEncodeError:
                    safe_rel_path = rel_path.encode('ascii', errors='replace').decode('ascii')
                    print(f"\n({idx}/{total_files_to_process}) {safe_rel_path}")
                    
                try:
                    print(f"  Duplicate of:  {original_rel_path}")
                except UnicodeEncodeError:
                    safe_original_rel_path = original_rel_path.encode('ascii', errors='replace').decode('ascii')
                    print(f"  Duplicate of:  {safe_original_rel_path}")
                    
                file_log_entries[filepath]["duplicate_details"] = {
                    "is_duplicate": True,
                    "duplicate_of": original_rel_path,
                    "status": "Skipped (Duplicate)"
                }
                continue
            else:
                seen_hashes[file_hash] = rel_path
        
        total_size += size
        
        # --- METADATA EXTRACTION ---
        meta = None
        if ext in IMAGE_EXTENSIONS:
            image_count += 1
            meta = get_image_metadata(filepath)
        elif ext in VIDEO_EXTENSIONS:
            video_count += 1
            meta = get_video_metadata(filepath)
            
        if meta is None:
            meta = {"Type": "Image" if ext in IMAGE_EXTENSIONS else "Video", "Error": "Failed to parse metadata"}
            
        try:
            print(f"\n({idx}/{total_files_to_process}) {rel_path}")
        except UnicodeEncodeError:
            safe_rel_path = rel_path.encode('ascii', errors='replace').decode('ascii')
            print(f"\n({idx}/{total_files_to_process}) {safe_rel_path}")
            
        # Get proposed filename (this might print geocoding status for city reversed from GPS coordinates)
        proposed = get_proposed_filename(filepath, meta, ext)
        unique_name = file
        rename_status = "No rename proposed"
        if proposed:
            unique_name = get_unique_filepath(root, file, proposed)
            if unique_name.lower() != file.lower():
                files_renamed += 1
                rename_status = "Renamed"
            else:
                rename_status = "Already formatted"
                
        # Print metadata details in analysis phase
        if not meta.get("Error"):
            if ext in IMAGE_EXTENSIONS:
                print(f"  Type:          {meta.get('Format', 'Unknown')} Image")
                print(f"  Dimensions:    {meta.get('Width', 'Unknown')} x {meta.get('Height', 'Unknown')} px")
                print(f"  Date Taken:    {meta.get('Date Taken', 'Unknown')}")
                camera = f"{meta.get('Camera Make', '')} {meta.get('Camera Model', '')}".strip()
                if camera and camera != "Unknown Unknown" and camera != "":
                    try:
                        print(f"  Camera Model:  {camera}")
                    except UnicodeEncodeError:
                        safe_camera = camera.encode('ascii', errors='replace').decode('ascii')
                        print(f"  Camera Model:  {safe_camera}")
                if meta.get("GPS"):
                    print(f"  GPS Location:  {meta['GPS']}")
            else:
                print(f"  Type:          Video")
                print(f"  Dimensions:    {meta.get('Width', 'Unknown')} x {meta.get('Height', 'Unknown')} px")
                print(f"  Duration:      {meta.get('Duration', 'Unknown')}")
                print(f"  Bitrate:       {meta.get('Bitrate', 'Unknown')}")
                print(f"  Date Created:  {meta.get('Date Created', 'Unknown')}")
        else:
            print(f"  Type:          {ext[1:].upper()} File (Metadata Error)")
            
        print(f"  Proposed name:  {unique_name}")
        
        # Populate log entries
        meta_log = {
            "width": meta.get("Width"),
            "height": meta.get("Height")
        }
        if meta.get("Width") != "Unknown" and meta.get("Height") != "Unknown":
            meta_log["dimensions"] = f"{meta['Width']} x {meta['Height']} px"
        
        if ext in IMAGE_EXTENSIONS:
            file_log_entries[filepath]["type"] = f"{meta.get('Format', 'Unknown')} Image"
            meta_log["format"] = meta.get("Format")
            meta_log["date_taken"] = meta.get("Date Taken", "Unknown")
            camera = f"{meta.get('Camera Make', '')} {meta.get('Camera Model', '')}".strip()
            meta_log["camera_model"] = camera if camera and camera != "Unknown Unknown" and camera != "" else None
            meta_log["gps_location"] = meta.get("GPS")
            if meta.get("GPS_Lat") is not None:
                meta_log["city"] = get_city_from_gps(meta["GPS_Lat"], meta["GPS_Lon"])
        else:
            meta_log["duration"] = meta.get("Duration")
            meta_log["bitrate"] = meta.get("Bitrate")
            meta_log["date_created"] = meta.get("Date Created")
            
        file_log_entries[filepath]["metadata"] = meta_log
        file_log_entries[filepath]["rename_details"] = {
            "proposed_name": unique_name if proposed else None,
            "status": rename_status
        }
        
        if not meta.get("Error"):
            dt = get_file_datetime(meta)
            file_data = {
                "root": root, "file": file, "meta": meta, "file_hash": file_hash,
                "size": size, "ext": ext, "filepath": filepath, "unique_name": unique_name,
                "rename_status": rename_status
            }
            if dt:
                file_data["datetime"] = dt
                unique_with_ts.append(file_data)
            else:
                unique_without_ts.append(file_data)
        else:
            # If metadata extraction failed, classify as without timestamp
            file_data = {
                "root": root, "file": file, "meta": {"Type": "Image" if ext in IMAGE_EXTENSIONS else "Video", "Error": "Failed to parse metadata"},
                "file_hash": file_hash, "size": size, "ext": ext, "filepath": filepath,
                "unique_name": unique_name, "rename_status": rename_status
            }
            unique_without_ts.append(file_data)
            
    # Perform Metadata Analysis and display results
    both_ts_and_loc = 0
    only_ts = 0
    only_loc = 0
    neither_ts_nor_loc = 0
    
    for file_data in unique_with_ts:
        meta = file_data["meta"]
        lat = meta.get("GPS_Lat")
        lon = meta.get("GPS_Lon")
        has_loc = (lat is not None and lon is not None)
        if has_loc:
            both_ts_and_loc += 1
        else:
            only_ts += 1
            
    for file_data in unique_without_ts:
        meta = file_data["meta"]
        lat = meta.get("GPS_Lat")
        lon = meta.get("GPS_Lon")
        has_loc = (lat is not None and lon is not None)
        if has_loc:
            only_loc += 1
        else:
            neither_ts_nor_loc += 1
            
    other_files_count = stats_before['other_count']
    
    print("\n" + "=" * 80)
    print("METADATA ANALYSIS REPORT")
    print("=" * 80)
    print(f"Files with both timestamp and location available: {both_ts_and_loc}")
    print(f"Files with only timestamp available:             {only_ts}")
    print(f"Files with only location available:              {only_loc}")
    print(f"Files with none of the above (only name kept):   {neither_ts_nor_loc}")
    print(f"Files that are neither images nor videos:        {other_files_count}")
    print("=" * 80)
    
    confirm = input("Do you want to continue with renaming and copying of files? (y/N): ").strip().lower()
    if confirm not in ('y', 'yes'):
        print("\nOperation cancelled by user. Exiting.")
        sys.exit(0)
        
    total_to_copy = len(unique_with_ts) + len(unique_without_ts)
    copied_index = 0
    copied_images_count = 0
    copied_images_size = 0
    copied_videos_count = 0
    copied_videos_size = 0
    files_renamed = 0  # Reset counter to track actual renames during execution

    # Second Pass: Process files with timestamps
    if unique_with_ts:
        print("\n" + "=" * 80)
        print("PROCESSING FILES WITH TIMESTAMPS...")
        print("=" * 80)
        
        for i, file_data in enumerate(unique_with_ts, 1):
            copied_index += 1
            root = file_data["root"]
            filepath = file_data["filepath"]
            dt = file_data["datetime"]
            unique_name = file_data["unique_name"]
            ext = file_data["ext"]
            size = file_data.get("size", 0)
            
            # Copy / Sort logic
            folder_name = dt.strftime("%Y%m")
            final_name = unique_name
            dest_folder = os.path.join(results_dir, folder_name)
            
            # Record destination folder for parent directory statistics
            parent_path = os.path.abspath(root)
            if parent_path not in folder_destinations:
                folder_destinations[parent_path] = []
            folder_destinations[parent_path].append(folder_name)
            
            try:
                os.makedirs(dest_folder, exist_ok=True)
                dest_unique_name = get_unique_filepath(dest_folder, final_name, final_name)
                dest_filepath = os.path.join(dest_folder, dest_unique_name)
                
                shutil.copy2(filepath, dest_filepath)
                files_copied += 1
                
                # Update files renamed count if the final name in destination is different from original
                if dest_unique_name.lower() != file_data["file"].lower():
                    files_renamed += 1
                
                # Update processed statistics
                if ext in IMAGE_EXTENSIONS:
                    copied_images_count += 1
                    copied_images_size += size
                elif ext in VIDEO_EXTENSIONS:
                    copied_videos_count += 1
                    copied_videos_size += size
                
                rel_dest_print = os.path.join("Sorted", folder_name, dest_unique_name)
                file_log_entries[filepath]["copy_details"] = {
                    "destination": rel_dest_print,
                    "status": "Copied"
                }
            except Exception as e:
                print(f"\n  Error copying {filepath}: {e}")
                
            print_progress(copied_index, total_to_copy)

    # Third Pass: Process files without timestamps using parent folder majority destinations
    if unique_without_ts:
        print("\n" + "=" * 80)
        print("PROCESSING FILES WITHOUT TIMESTAMPS (Using folder majority)...")
        print("=" * 80)
        
        # Build global fallback majority in case some parent folder has no files with timestamps
        all_dests = []
        for dests in folder_destinations.values():
            all_dests.extend(dests)
        global_majority = Counter(all_dests).most_common(1)[0][0] if all_dests else "unknown"
        
        for i, file_data in enumerate(unique_without_ts, 1):
            copied_index += 1
            root = file_data["root"]
            filepath = file_data["filepath"]
            unique_name = file_data["unique_name"]
            ext = file_data["ext"]
            size = file_data.get("size", 0)
            
            # Find majority destination for this parent folder
            parent_path = os.path.abspath(root)
            folder_dests = folder_destinations.get(parent_path, [])
            basis_str = f"based on {len(folder_dests)} other files in folder" if folder_dests else "global fallback, folder has no sorted files"
            if folder_dests:
                majority_folder = Counter(folder_dests).most_common(1)[0][0]
            else:
                majority_folder = global_majority
                
            # Copy to the majority destination folder
            dest_folder = os.path.join(results_dir, majority_folder)
            final_name = unique_name
            
            try:
                os.makedirs(dest_folder, exist_ok=True)
                dest_unique_name = get_unique_filepath(dest_folder, final_name, final_name)
                dest_filepath = os.path.join(dest_folder, dest_unique_name)
                
                shutil.copy2(filepath, dest_filepath)
                rel_dest_print = os.path.join("Sorted", majority_folder, dest_unique_name)
                files_copied += 1
                
                # Update files renamed count if the final name in destination is different from original
                if dest_unique_name.lower() != file_data["file"].lower():
                    files_renamed += 1
                
                # Update processed statistics
                if ext in IMAGE_EXTENSIONS:
                    copied_images_count += 1
                    copied_images_size += size
                elif ext in VIDEO_EXTENSIONS:
                    copied_videos_count += 1
                    copied_videos_size += size
                
                file_log_entries[filepath]["copy_details"] = {
                    "destination": rel_dest_print,
                    "status": f"Copied to majority folder '{majority_folder}'",
                    "folder_majority": majority_folder,
                    "majority_basis": basis_str
                }
            except Exception as e:
                print(f"\n  Error copying {filepath}: {e}")
                
            print_progress(copied_index, total_to_copy)

    # 2. Print End Workspace Summary
    stats_after = {
        "img_count": copied_images_count,
        "img_size": copied_images_size,
        "vid_count": copied_videos_count,
        "vid_size": copied_videos_size,
        "other_count": 0,
        "other_size": 0
    }
    print("\n" + "=" * 80)
    print("FINAL WORKSPACE SUMMARY (Post-execution)")
    print("=" * 80)
    print(f"Images:      {stats_after['img_count']} files (Size: {format_size(stats_after['img_size'])})")
    print(f"Videos:      {stats_after['vid_count']} files (Size: {format_size(stats_after['vid_size'])})")
    print(f"Other files: {stats_after['other_count']} files (Size: {format_size(stats_after['other_size'])})")
    total_files_after = stats_after['img_count'] + stats_after['vid_count'] + stats_after['other_count']
    total_size_after = stats_after['img_size'] + stats_after['vid_size'] + stats_after['other_size']
    print(f"Total:       {total_files_after} files (Size: {format_size(total_size_after)})")
    print("-" * 80)
    print(f"Files Renamed:       {files_renamed}")
    print(f"Duplicates Skipped:  {duplicates_found}")
    print(f"Files Copied:        {files_copied}")
    print("=" * 80)

    # Save detailed JSON summary inside the Results folder (including individual file logs)
    summary_data = {
        "timestamp": datetime.now().isoformat(),
        "selected_directory": directory,
        "results_directory": results_dir,
        "initial_summary": {
            "images": { "count": stats_before['img_count'], "size_bytes": stats_before['img_size'], "size_formatted": format_size(stats_before['img_size']) },
            "videos": { "count": stats_before['vid_count'], "size_bytes": stats_before['vid_size'], "size_formatted": format_size(stats_before['vid_size']) },
            "other_files": { "count": stats_before['other_count'], "size_bytes": stats_before['other_size'], "size_formatted": format_size(stats_before['other_size']) },
            "total": { "count": total_files_before, "size_bytes": total_size_before, "size_formatted": format_size(total_size_before) }
        },
        "execution_actions": {
            "rename_enabled": True,
            "delete_duplicates_enabled": False,
            "copy_sorted_enabled": True
        },
        "execution_results": {
            "files_renamed": files_renamed,
            "duplicates_found": duplicates_found,
            "duplicates_removed": 0,
            "files_copied": files_copied
        },
        "final_summary": {
            "images": { "count": stats_after['img_count'], "size_bytes": stats_after['img_size'], "size_formatted": format_size(stats_after['img_size']) },
            "videos": { "count": stats_after['vid_count'], "size_bytes": stats_after['vid_size'], "size_formatted": format_size(stats_after['vid_size']) },
            "other_files": { "count": stats_after['other_count'], "size_bytes": stats_after['other_size'], "size_formatted": format_size(stats_after['other_size']) },
            "total": { "count": total_files_after, "size_bytes": total_size_after, "size_formatted": format_size(total_size_after) }
        },
        "processed_files": list(file_log_entries.values())
    }
    
    try:
        os.makedirs(results_dir, exist_ok=True)
        json_path = os.path.join(results_dir, "summary.json")
        with open(json_path, 'w', encoding='utf-8') as json_file:
            json.dump(summary_data, json_file, indent=2, ensure_ascii=False)
        print(f"\nSummary successfully written to: {json_path}")
    except Exception as e:
        print(f"\nError writing JSON summary: {e}")

if __name__ == "__main__":
    args = sys.argv[1:]
    
    # Remove any flags if passed for backwards compatibility
    for flag in ["--rename", "--copy-sorted", "--delete-duplicates"]:
        if flag in args:
            args.remove(flag)
            
    target_dir = args[0] if len(args) > 0 else None
    results_dir = args[1] if len(args) > 1 else None
    
    # UI directory selection if no folder argument is supplied on command line
    if not target_dir:
        import tkinter as tk
        from tkinter import filedialog
        
        # Hide the root window
        root = tk.Tk()
        root.withdraw()
        # Bring dialog to the front
        root.attributes("-topmost", True)
        
        print("Please select the target folder in the dialog window...")
        target_dir = filedialog.askdirectory(title="Select Folder to Process")
        
        if not target_dir:
            print("Folder selection cancelled. Exiting.")
            sys.exit(0)
            
        print("Please select the parent folder where the 'Sorted' folder shall be located...")
        results_parent_dir = filedialog.askdirectory(title="Select Location to Place 'Sorted' Folder")
        
        if not results_parent_dir:
            print("Sorted folder selection cancelled. Exiting.")
            sys.exit(0)
            
        results_dir = os.path.join(results_parent_dir, "Sorted")
            
    scan_directory(os.path.abspath(target_dir), results_dir=os.path.abspath(results_dir) if results_dir else None)
