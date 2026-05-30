# ImgSorter

Sorting of images and videos, renaming, geolocation, copy, duplicate removal.

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
