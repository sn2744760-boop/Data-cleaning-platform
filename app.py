from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, jsonify
import os
import time
import logging
import pandas as pd
from typing import Optional
from werkzeug.utils import secure_filename
from execution.cleaning_engine import run_diagnostic, run_optimization

# Application Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FlaskApp")

app = Flask(__name__)

# Fix S1: Use environment variable for secret key instead of hardcoded value
app.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(24).hex())

# Use /tmp on Vercel serverless environment, otherwise local .tmp
UPLOAD_FOLDER = '/tmp' if os.environ.get('VERCEL') == '1' else '.tmp'
ALLOWED_EXTENSIONS = {'csv'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024

# Fix L1: Maximum age for uploaded files (1 hour in seconds)
FILE_MAX_AGE_SECONDS = 3600

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename: str) -> bool:
    """Check if the uploaded file has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def cleanup_old_files() -> None:
    """Fix L1: Remove files older than FILE_MAX_AGE_SECONDS from the upload folder."""
    try:
        now = time.time()
        for fname in os.listdir(UPLOAD_FOLDER):
            fpath = os.path.join(UPLOAD_FOLDER, fname)
            if os.path.isfile(fpath) and (now - os.path.getmtime(fpath)) > FILE_MAX_AGE_SECONDS:
                os.remove(fpath)
                logger.info(f"Cleaned up expired file: {fname}")
    except OSError as e:
        logger.warning(f"File cleanup error: {e}")


@app.route('/')
def index():
    """Serve the main application page."""
    cleanup_old_files()
    return render_template('index.html')


@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    """Handle CSV file upload and run diagnostic analysis."""
    if request.method == 'GET':
        return redirect(url_for('index'))
    
    file = request.files.get('file')
    if not file or file.filename == '' or not allowed_file(file.filename):
        flash('Please upload a valid CSV dataset.')
        return redirect(url_for('index'))
    
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    logger.info(f"File uploaded: {filename}")
    
    try:
        # Fix H4/P1: run_diagnostic now returns the DataFrame, eliminating double read
        diagnostic, df = run_diagnostic(filepath)
        preview = df.head(10).to_dict(orient='records')
        
        return render_template('index.html', 
                             diagnostic=diagnostic,
                             preview=preview,
                             columns=df.columns.tolist(),
                             filename=filename)
    except Exception as e:
        logger.error(f"Diagnostic failure for {filename}: {e}", exc_info=True)
        # Fix S4: Sanitize error messages — don't expose internal details to the user
        flash("Diagnostic failed. Please ensure your CSV file is well-formed and try again.")
        return redirect(url_for('index'))


@app.route('/optimize/<filename>', methods=['POST'])
def optimize_file(filename: str):
    """Run optimization with Human-in-the-Loop overrides on a previously uploaded file."""
    # Fix S2: Validate filename to prevent path traversal
    filename = secure_filename(filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.exists(filepath):
        flash("Session expired. Please re-upload your dataset.")
        return redirect(url_for('index'))
    
    # Extract Human-in-the-Loop Overrides from Form
    try:
        overrides = {}
        for key, value in request.form.items():
            if key.startswith('strategy_'):
                col_name = key.replace('strategy_', '')
                overrides[col_name] = value
        
        logger.info(f"Optimizing {filename} with overrides: {overrides}")
        results = run_optimization(filepath, overrides)
        
        opt_path = os.path.join(app.config['UPLOAD_FOLDER'], results['output_file'])
        opt_df = pd.read_csv(opt_path)
        
        return render_template('index.html',
                             optimized_results=results,
                             preview=opt_df.head(10).to_dict(orient='records'),
                             columns=opt_df.columns.tolist())
    except Exception as e:
        logger.error(f"Optimization error for {filename}: {e}", exc_info=True)
        # Fix S4: Sanitize error messages
        flash("Optimization failed. Please re-upload your dataset and try again.")
        return redirect(url_for('index'))


@app.route('/download/<filename>')
def download_file(filename: str):
    """Serve an optimized file for download."""
    # Fix S2: Validate filename to prevent directory traversal
    filename = secure_filename(filename)
    if not filename:
        flash("Invalid filename.")
        return redirect(url_for('index'))
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


if __name__ == '__main__':
    # Fix S6: Debug mode controlled by environment variable
    debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug_mode)
