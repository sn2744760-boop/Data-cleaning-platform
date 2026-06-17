# AI Data Optimizer (Tier 10)

A high-performance, autonomous data preparation suite designed to clean, standardize, and optimize noisy datasets (CSV and Excel formats) into analysis-ready business intelligence.

The application features a stunning, premium **Dark Glassmorphism UI** with a custom-tailored **Blue-Cyan palette** (strictly free of purple accents), complete with micro-animations, real-time health charts, and quality score diagnostics.

---

## 🚀 Key Features

- **Autonomous Health Audit & Diagnostics**: Scans datasets upon upload to detect null sparsity, duplicate rows, fuzzy duplicates, invalid emails, date patterns, and numeric string types.
- **Human-in-the-Loop Orchestration**: Allows users to override recommended cleaning strategies (Median, Mean, Mode, Skip, Drop, or Custom values) per column.
- **Robust Email Validation**: Integrates `email-validator` to normalize emails, strip whitespace, and handle invalid emails using three cleaning modes (`FLAG_ONLY`, `NULL_INVALID`, `REMOVE_ROW`).
- **Date Standardization**: Automatically detects date columns, standardizes them to ISO-8601 formatting, and handles outliers/invalid strings.
- **Outlier Mitigation**: Handles statistical anomalies in numeric columns via clipping (IQR bounds), median replacement, or row removal.
- **Quality Score 2.0**: Calculates a composite quality metric (0–100%) based on 7 distinct data quality dimensions: missing values, duplicates, type integrity, email validity, date validity, outlier severity, and normalization completeness.
- **Post-Clean Verification Audit**: Runs a final validation pass on the cleaned dataset to ensure compliance and verify that no issues remain before export.
- **Multi-Format Export**: Download the cleaned, validated datasets in **CSV** format, standard Excel **XLSX** spreadsheets, or as a professionally generated **PDF Report**.

---

## 🛠️ Tech Stack

- **Backend**: Python 3.x, Flask, Pandas, NumPy, SciPy (Selective Stats), `email-validator`, OpenPyXL (Excel integration), ReportLab (PDF generation).
- **Frontend**: HTML5, Vanilla CSS (Premium Dark Glassmorphism, Blue/Cyan theme), Chart.js (interactive visualizations).
- **Testing**: Pytest for regression testing and reliability verification.

---

## 📦 Setup & Installation

1. **Clone the Repository**
   ```bash
   git clone https://github.com/sn2744760-boop/Data-cleaning-platform.git
   cd Data-cleaning-platform
   ```

2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the Application**
   ```bash
   python app.py
   ```
   Access the platform locally at `http://localhost:5000`.

---

## 📂 Project Structure

- `app.py`: Flask routing, template orchestration, and file exports.
- `execution/`: Core intelligence cleaning engine (`cleaning_engine.py`).
- `static/css/`: Premium dark theme styling (`style.css`).
- `templates/`: Interactive interface (`index.html`).
- `test_datasets/`: Realistic scenario datasets for testing and verification.
- `tests/`: Comprehensive unit testing suite for the engine and routes.

---

## 🧪 Testing

To run the unit tests:
```bash
python -m pytest
```

---

## ⚖️ License

MIT License
