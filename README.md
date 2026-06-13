# AI Data Intelligence Platform (Tier 10)

A high-performance, autonomous data prepared suite designed to bridge the gap between raw, noisy datasets and analysis-ready business intelligence.

## 🚀 Key Features
- **Autonomous Health Audit**: Immediate data health scoring (0-100%) and diagnostic scanning upon upload.
- **Human-in-the-Loop Orchestration**: Select and override AI-prescribed cleaning strategies per-column.
- **Deep Analytics**: Automatic detection of skewness, sparsity, and categorical cardinality.
- **Prescriptive Optimization**: Intelligent strategy selection (Median vs Mean vs Mode) based on statistical distribution.
- **Standardization Compliance**: Automated enforcement of ISO-8601 for time-series and title-case for characters.
- **Skeuomorphic Dashboard**: A tactile, premium UI designed for professional observability.

## 🛠️ Tech Stack
- **Backend**: Python, Flask, Pandas, NumPy, SciPy (Selective Stats).
- **Frontend**: HTML5, Vanilla CSS (Skeuomorphic/Neumorphic), Chart.js.
- **Testing**: Pytest for industrial-grade reliability.

## 📦 Setup & Installation

1. **Clone the Repository**
   ```bash
   git clone https://github.com/MizanShaikh19/Ai-based-data-cleaning.git
   cd Ai-based-data-cleaning
   ```

2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the Application**
   ```bash
   python app.py
   ```
   Access the platform at `http://localhost:5000`.

## 📂 Project Structure
- `app.py`: Flask orchestration and interactive routing.
- `execution/`: Core intelligence engine (`cleaning_engine.py`).
- `test_datasets/`: 5 realistic scenario datasets for stress testing.
- `templates/` & `static/`: Premium UI assets.
- `tests/`: Comprehensive unit testing suite.

## 🧪 Testing
Run the industrial reliability suite:
```bash
pytest tests/test_engine.py
```

## ⚖️ License
MIT License
