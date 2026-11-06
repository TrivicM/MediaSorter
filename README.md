# Media Metadata Sorter

Sorting of images and videos, renaming, geolocation, copy, duplicate removal.

![Media Metadata Sorter Dashboard](docs/gui_screenshot.png)
![Photo GPS Map View](docs/GPS_screenshot.png)

---

## Key Features

1. **Pre-Execution Analysis Phase:**
   * Scans all files and displays detailed file information (Dimensions, Format, Date Taken/Created, Camera Model, GPS location, and proposed new name).
   * Resolves GPS coordinates to city names (e.g., *Berlin*, *Rostock*, *København*) using the Nominatim OpenStreetMap API.
   * Calculates SHA-256 hashes to identify and classify duplicate files.
   * Displays analysis summaries and prompts you for confirmation before making any changes.

2. **Custom Folder Structuring Options:**
   Choose how the destination folders are structured:
   * **Option 1:** Sort by Year & Month (e.g., `Sorted/202412/`) [Default]
   * **Option 2:** Sort by Year (e.g., `Sorted/2024/`)
   * **Option 3:** Sort by Year, Month, and Location (e.g., `Sorted/202412_Berlin/`)
   * **Option 4:** Sort by Year and Location (e.g., `Sorted/2024_Berlin/`)
   * **Option 5:** Sort by Location only (e.g., `Sorted/Berlin/`)

3. **Renaming & Duplicate Copy Toggles:**
   * **Renaming Toggle:** Choose to rename files to a standardized format (`YYMMDD_HHMMSS_City.ext`) or preserve their original filenames.
   * **Duplicates Handling:** Choose to **Skip duplicates** (exclude identical files from copying) or **Keep duplicates** (copy duplicates and resolve filename collisions automatically by appending numeric suffixes like `_1`).
   * **Fallback Folder Majority:** If metadata required for your sorting option is missing (e.g., location is missing under Option 3/4/5), the file will automatically sort into the target folder used by the majority of its neighboring files.

4. **Safe Operation (Non-Destructive):**
   * The script **never** moves, edits, or deletes your original source files. All operations are safe copy actions into the designated `Sorted` folder. Target directory name collisions are resolved automatically using unique suffixes to prevent overwrites.

5. **JSON Execution Summary:**
   * Generates a detailed execution report at `Sorted/summary.json` mapping every processed file, its extracted metadata, rename and duplicate choices, and final sort destinations.

---

## Setup Instructions

This project uses [`uv`](https://docs.astral.sh/uv/) for fast, deterministic Python environment and dependency management.

### 1. Install `uv` (if not already installed)

* **Windows (PowerShell):**
  ```powershell
  winget install astral-sh.uv
  # or: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```
* **macOS / Linux:**
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

### 2. Install Project Dependencies

From the project root directory, synchronize the environment:

```bash
uv sync
```
This automatically sets up the `.venv` virtual environment and installs all dependencies specified in `pyproject.toml` using `uv.lock`.

## Usage

From the project root directory:

* **Web GUI Dashboard (Recommended):**
  Start the FastAPI local web application:
  ```bash
  uv run media-sorter-gui
  ```
  *(Or `uv run python scripting/gui_app.py`)*  
  This launches your default browser at `http://127.0.0.1:8000` showing the interactive dashboard and map view.

* **CLI Mode (Terminal prompts):**
  Run the command-line sorter:
  ```bash
  uv run media-sorter
  ```
  *(Or `uv run python scripting/metadata_sorter.py`)*  
  You can also specify the source directory directly:
  ```bash
  uv run media-sorter "path/to/your/images_folder"
  ```

---

## Running Tests

To run the automated test suite with pytest:

```bash
uv run pytest
```

To run tests with test coverage reporting:

```bash
uv run pytest --cov=scripting --cov-report=term-missing
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
  GPS Location:  xx.xxxxxx, yy.yyyyyy
  Proposed name:  241111_112526_London.jpeg
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
