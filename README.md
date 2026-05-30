# ImgSorter

Sorting of images and videos, renaming, geolocation, copy, duplicate removal.

---

## Key Features

1. **Pre-Execution Analysis Phase:**
   * Scans all files and displays detailed file information (Dimensions, Format, Date Taken/Created, Camera Model, GPS location, and proposed new name).
   * Resolves GPS coordinates to city names (e.g., *Berlin*, *Rostock*, *København*) using the Nominatim OpenStreetMap API.
   * Calculates SHA-256 hashes to identify and skip duplicate files (duplicates are never copied or modified).
   * Displays analysis summaries and prompts you for confirmation before making any changes.

2. **Smart Sorting & Rename Logic:**
   * **Images with Timestamps:** Named using `YYMMDD_HHMMSS_City.ext` and copied to `Sorted/YYYYMM/`.
   * **Images/Videos without Timestamps (Folder Majority):** Placed in the folder where the majority of other files from the same parent folder were sorted. If no sorted files exist in that parent folder, it falls back to the global folder majority, or `unknown` as a final fallback.
   * **Duplicate Files:** Retained untouched in their original locations and skipped during copying to prevent clutter in the `Sorted` folder.

3. **Safe Operation (Non-Destructive):**
   * The script **never** moves, edits, or deletes your original source files. All operations are safe copy actions into the designated `Sorted` folder.

4. **JSON Execution Summary:**
   * Generates a detailed execution report at `Sorted/summary.json` mapping every processed file, its extracted metadata, rename details, duplicates found, and final sort destinations.

---

## Setup Instructions

To run this project on a new computer:

1. **Create a virtual environment (optional but recommended):**
   ```bash
   python -m venv venv
   ```

2. **Activate the virtual environment:**
   * **Windows (PowerShell):**
     ```powershell
     .\venv\Scripts\Activate.ps1
     ```
   * **Windows (Command Prompt):**
     ```cmd
     .\venv\Scripts\activate.bat
     ```
   * **macOS / Linux:**
     ```bash
     source venv/bin/activate
     ```

3. **Install the dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

## Usage

From the project root directory:

* **Interactive mode (UI directory pickers):**
  ```bash
  python scripting/metadata_sorter.py
  ```

* **CLI mode (specifying the target directory):**
  ```bash
  python scripting/metadata_sorter.py "path/to/your/images_folder"
  ```

---

## Detailed Execution Flow

### Pass 1: Scan & Analysis Phase
Before copying, the script lists every media file and processes its metadata:
```text
(4/14) A1\IMG_0247.JPEG
  Type:          MPO Image
  Dimensions:    1536 x 2048 px
  Date Taken:    2024:11:11 11:25:26
  Camera Model:  Apple iPhone 12 mini
  GPS Location:  52.520881, 13.320072
  Proposed name:  241111_112526_Berlin.jpeg
```
After scanning, it prints a summary of the analyzed metadata:
```text
================================================================================
METADATA ANALYSIS REPORT
================================================================================
Files with both timestamp and location available: 14
Files with only timestamp available:             6
...
================================================================================
Do you want to continue with renaming and copying of files? (y/N): 
```

### Pass 2 & 3: Copying & Sorting Phase
Upon confirmation, files are copied with a live progress bar:
```text
================================================================================
PROCESSING FILES WITH TIMESTAMPS...
================================================================================
Progress: [#################-------------] 59.1% (13/22 files)
```

At the end of copying, it outputs a post-execution workspace summary:
```text
================================================================================
FINAL WORKSPACE SUMMARY (Post-execution)
================================================================================
Images:      18 files (Size: 21.61 MB)
Videos:      4 files (Size: 24.32 MB)
Other files: 0 files (Size: 0 B)
Total:       22 files (Size: 45.93 MB)
--------------------------------------------------------------------------------
Files Renamed:       0
Duplicates Skipped:  22
Files Copied:        22
================================================================================
```
