# Amalitech Competitor Research Scraper

A deep-crawl scraper that collects competitor website content — page text and relevant images — and runs OCR on images to extract structured text. Results are saved in versioned, timestamped folders for each competitor site.

---

## Table of Contents

- [Project Structure](#project-structure)
- [Input File Format](#input-file-format)
- [Output Structure](#output-structure)
- [Option A: Run Locally (Python)](#option-a-run-locally-python)
- [Option B: Run with Docker](#option-b-run-with-docker)
- [Environment Variables](#environment-variables)
- [Tips & Troubleshooting](#tips--troubleshooting)

---

## Project Structure

```
project/
├── scraper.py            # Main scraper script
├── requirements.txt      # Python dependencies
├── Dockerfile            # Docker image definition
├── docker-compose.yml    # Docker Compose configuration
├── README.md             # This file
├── competitors.csv       # Your input file (CSV or TXT)
└── sites/                # Output folder — auto-created on first run
    └── accenture-com_2025-03-16_14-30-00/
        ├── pages/
        ├── images/
        ├── pages_text.csv
        └── ocr_output.csv
```

---

## Input File Format

Place a `.csv` or `.txt` file in the **same directory as `scraper.py`**.

### CSV format
The script auto-detects the URL column. Any of these column names will work:
`url`, `website`, `site`, `link`, `domain`, `competitor_url`

You can have other columns — they will be ignored.

```csv
competitor_name,website,region
Accenture,https://www.accenture.com,Global
McKinsey,https://www.mckinsey.com,Global
Deloitte,https://www2.deloitte.com,Global
```

### TXT format
One URL per line. Lines starting with `#` are ignored (use for comments).

```
# Global consultancies
https://www.accenture.com
https://www.mckinsey.com

# Regional competitors
https://www.example-africa-ai.com
```

---

## Output Structure

Each run creates a **new timestamped folder** per site, preserving history across runs.

```
sites/
└── accenture-com_2025-03-16_14-30-00/
    ├── pages/
    │   ├── index.html
    │   ├── services_ai.html
    │   └── about.html
    ├── images/
    │   ├── a1b2c3d4e5f6.jpg
    │   └── 7890abcdef12.png
    ├── pages_text.csv     ← one row per crawled page
    └── ocr_output.csv     ← one row per image with extracted text
```

### `pages_text.csv` columns

| Column | Description |
|---|---|
| `url` | Full URL of the crawled page |
| `page_title` | HTML `<title>` of the page |
| `depth` | Crawl depth (0 = starting URL) |
| `clean_text` | Cleaned readable text extracted from the page |

### `ocr_output.csv` columns

| Column | Description |
|---|---|
| `image_path` | Relative path to the saved image file |
| `image_url` | Original URL the image was downloaded from |
| `source_page_url` | The page on which this image was found |
| `extracted_text` | Text extracted from the image via OCR |

---

## Option A: Run Locally (Python)

### 1. Prerequisites

- Python 3.10 or higher
- `pip`

For `pytesseract` (optional): install [Tesseract OCR](https://github.com/tesseract-ocr/tesseract#installing-tesseract) on your system.
- **Ubuntu/Debian:** `sudo apt install tesseract-ocr tesseract-ocr-eng`
- **macOS:** `brew install tesseract`
- **Windows:** Download the installer from the Tesseract GitHub releases page.

### 2. Install dependencies

From the project folder, run:

```bash
pip install -r requirements.txt
```

> **Note:** `easyocr` will download its English language model (~100MB) on first use.
> This only happens once and is cached locally.

### 3. Add your input file

Place your `competitors.csv` or `competitors.txt` in the project folder.

### 4. Run the scraper

**Basic run (all defaults):**
```bash
python scraper.py
```

**With custom depth:**
```bash
MAX_DEPTH=5 python scraper.py
```

**Switch to pytesseract:**
```bash
OCR_ENGINE=pytesseract python scraper.py
```

**Force a specific input file:**
```bash
INPUT_FILE=my_list.csv python scraper.py
```

**Combine multiple options:**
```bash
MAX_DEPTH=4 OCR_ENGINE=pytesseract INPUT_FILE=competitors.csv python scraper.py
```

**Windows (Command Prompt):**
```cmd
set MAX_DEPTH=4 && set OCR_ENGINE=easyocr && python scraper.py
```

**Windows (PowerShell):**
```powershell
$env:MAX_DEPTH="4"; $env:OCR_ENGINE="easyocr"; python scraper.py
```

### 5. View results

Results are saved in the `sites/` folder in the project directory.

---

## Option B: Run with Docker

### 1. Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- Docker Compose (included with Docker Desktop)

### 2. Add your input file

Place your `competitors.csv` or `competitors.txt` in the **project root** (same folder as `docker-compose.yml`).

### 3. Build the Docker image

This only needs to be done once, or again after any code changes:

```bash
docker compose build
```

> **Note:** The build downloads and caches the EasyOCR model inside the image.
> This makes subsequent runs fast. The build may take 3–5 minutes on first run.

### 4. Run the scraper

**Basic run (all defaults):**
```bash
docker compose up
```

**With custom depth:**
```bash
MAX_DEPTH=5 docker compose up
```

**Switch to pytesseract:**
```bash
OCR_ENGINE=pytesseract docker compose up
```

**Combine options:**
```bash
MAX_DEPTH=4 OCR_ENGINE=pytesseract docker compose up
```

**Run in the background (detached):**
```bash
docker compose up -d
```

**Follow logs while running in background:**
```bash
docker compose logs -f
```

### 5. View results

Results are written to the `sites/` folder in your project directory on your host machine — the same as the local option. Docker mounts this folder automatically.

### 6. Stop / clean up

```bash
# Stop the container
docker compose down

# Remove the built image to free disk space (optional)
docker compose down --rmi all
```

### Re-running after code changes

If you edit `scraper.py`, rebuild before running:

```bash
docker compose build && docker compose up
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MAX_DEPTH` | `3` | How many link levels deep to crawl from the starting URL |
| `OCR_ENGINE` | `easyocr` | OCR engine to use: `easyocr` or `pytesseract` |
| `INPUT_FILE` | _(auto-detect)_ | Force a specific input filename (e.g. `competitors.csv`) |

---

## Tips & Troubleshooting

**The script found multiple input files**
If you have more than one CSV or TXT in the folder, it will use the first one it finds alphabetically and warn you. Use `INPUT_FILE=yourfile.csv` to be explicit.

**A site is being skipped or returning errors**
Some sites block automated requests. The script will log the error and move on without crashing. You can check the logs for `Skip` messages. Try increasing `REQUEST_DELAY` in `scraper.py` if many sites are blocking you.

**EasyOCR is slow on first run**
EasyOCR downloads its language model (~100MB) on first use if not already cached. In Docker, this is handled at build time. Locally, it caches in `~/.EasyOCR/`.

**No images are being saved**
Images must be at least 100×100px and inside a content tag (`<article>`, `<section>`, `<main>`, `<div>`). Logos, icons, and decorative images are filtered out by design. If you want to lower the size threshold, edit `MIN_IMAGE_WIDTH` and `MIN_IMAGE_HEIGHT` in `scraper.py`.

**OCR text is empty or poor quality**
- Make sure the image is high resolution (low-res screenshots will produce poor results)
- Try switching OCR engines: `OCR_ENGINE=pytesseract` sometimes performs better on certain image types
- Check that Tesseract is correctly installed if using `pytesseract`

**Running on Windows locally**
Use PowerShell to set environment variables:
```powershell
$env:MAX_DEPTH="5"; python scraper.py
```

---

*Amalitech Benchmarking Team — Internal Research Tooling*
