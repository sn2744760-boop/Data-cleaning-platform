from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, jsonify, make_response
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
ALLOWED_EXTENSIONS = {'csv', 'xlsx', 'xls'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024

# Fix L1: Maximum age for uploaded files (1 hour in seconds)
FILE_MAX_AGE_SECONDS = 3600

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename: str) -> bool:
    """Check if the uploaded file has an allowed extension (csv, xlsx, xls)."""
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


@app.route('/favicon.ico')
def favicon():
    """Favicon route fallback to avoid 404 logs."""
    return '', 204


@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    """Handle CSV file upload and run diagnostic analysis."""
    if request.method == 'GET':
        return redirect(url_for('index'))
    
    file = request.files.get('file')
    if not file or file.filename == '' or not allowed_file(file.filename):
        flash('Please upload a valid dataset (.csv, .xlsx, or .xls).')
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
        flash("Diagnostic failed. Please ensure your file is well-formed (CSV/XLSX) and try again.")
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
            elif key.startswith('email_mode_') or key.startswith('outlier_') or key.startswith('date_mode_'):
                # Pass through email, outlier, and date cleaning mode overrides
                overrides[key] = value
        
        logger.info(f"Optimizing {filename} with overrides: {overrides}")
        results = run_optimization(filepath, overrides)
        
        # Save results to a json file to allow pdf/xlsx generation to read it later
        import json
        res_json_name = f"results_{filename}.json"
        res_json_path = os.path.join(app.config['UPLOAD_FOLDER'], res_json_name)
        with open(res_json_path, 'w') as f:
            json.dump(results, f)
        
        opt_path = os.path.join(app.config['UPLOAD_FOLDER'], results['output_file'])
        opt_df = pd.read_csv(opt_path)

        # Extract verification and quality data for template
        report      = results.get('report', {})
        verification = report.get('verification', {})
        quality_before = report.get('quality_before', 0)
        quality_after  = report.get('quality_after',  0)
        quality_improvement = report.get('quality_improvement_pct', 0)
        
        return render_template('index.html',
                             optimized_results=results,
                             preview=opt_df.head(10).to_dict(orient='records'),
                             columns=opt_df.columns.tolist(),
                             verification=verification,
                             quality_before=quality_before,
                             quality_after=quality_after,
                             quality_improvement=quality_improvement)
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


@app.route('/download_xlsx/<filename>')
def download_xlsx(filename: str):
    """Serve the optimized file as an Excel spreadsheet (.xlsx)."""
    filename = secure_filename(filename)
    if not filename:
        flash("Invalid filename.")
        return redirect(url_for('index'))
        
    csv_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.exists(csv_path):
        flash("File not found.")
        return redirect(url_for('index'))
        
    base = os.path.splitext(filename)[0]
    xlsx_name = f"{base}.xlsx"
    xlsx_path = os.path.join(app.config['UPLOAD_FOLDER'], xlsx_name)
    
    try:
        df = pd.read_csv(csv_path)
        df.to_excel(xlsx_path, index=False, engine='openpyxl')
        return send_from_directory(app.config['UPLOAD_FOLDER'], xlsx_name, as_attachment=True)
    except Exception as e:
        logger.error(f"Failed to export XLSX for {filename}: {e}", exc_info=True)
        flash("Failed to generate Excel file.")
        return redirect(url_for('index'))


def generate_pdf_report(filename, report_metrics, steps_applied, health_before, health_after, column_diagnostics, quality_before=None, quality_after=None):
    from io import BytesIO
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40
    )
    
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=24,
        leading=28,
        textColor=colors.HexColor('#44476a'),
        spaceAfter=15
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=11,
        leading=14,
        textColor=colors.HexColor('#7e8299'),
        spaceAfter=20
    )
    
    heading_style = ParagraphStyle(
        'SectionHeading',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=17,
        textColor=colors.HexColor('#4d7cfe'),
        spaceBefore=15,
        spaceAfter=10
    )
    
    body_style = ParagraphStyle(
        'BodyTextCustom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#44476a')
    )
    
    step_style = ParagraphStyle(
        'StepTextCustom',
        parent=body_style,
        leftIndent=15,
        firstLineIndent=-10,
        spaceAfter=5
    )

    story = []
    
    story.append(Paragraph("AI Data Optimizer v10", title_style))
    story.append(Paragraph(f"Standardization Report for: <b>{filename}</b>", subtitle_style))
    story.append(Spacer(1, 10))
    
    story.append(Paragraph("Health &amp; Quality Score Summary", heading_style))
    story.append(Paragraph(f"Health Score: <b>{health_before}%</b> → <b>{health_after}%</b>", body_style))
    story.append(Spacer(1, 5))
    if quality_before is not None and quality_after is not None:
        story.append(Paragraph(f"Quality Score: <b>{quality_before}%</b> → <b>{quality_after}%</b>", body_style))
    else:
        story.append(Paragraph("Quality Score: tracked in optimization report (see metrics below)", body_style))
    story.append(Spacer(1, 15))
    
    story.append(Paragraph("Standardization Metrics", heading_style))
    metrics_data = [
        ["Metric", "Value"],
        ["Original Row Count", f"{report_metrics.get('rows_before', 0)}"],
        ["Cleaned Row Count", f"{report_metrics.get('rows_after', 0)}"],
        ["Missing Values Filled", f"{report_metrics.get('nulls_filled', 0)}"],
        ["Exact Duplicates Removed", f"{report_metrics.get('duplicates_dropped', 0)}"],
        ["Fuzzy Duplicates Removed", f"{report_metrics.get('fuzzy_duplicates_dropped', 0)}"],
        ["Data Types Standardized", f"{report_metrics.get('types_converted', 0)}"],
        ["Dates Detected", f"{report_metrics.get('dates_detected', 0)}"],
        ["Date Formats Standardized", f"{report_metrics.get('dates_standardized', 0)}"],
        ["Invalid Dates Rejected", f"{report_metrics.get('dates_rejected', 0)}"],
        ["Invalid Emails Flagged", f"{report_metrics.get('emails_flagged', 0)}"],
        ["Invalid Emails Nullified", f"{report_metrics.get('emails_nullified', 0)}"],
        ["Rows Removed (bad email)", f"{report_metrics.get('emails_rows_removed', 0)}"],
        ["Phone Numbers Normalized", f"{report_metrics.get('phones_corrected', 0)}"],
        ["Invalid Phones Flagged", f"{report_metrics.get('phones_flagged_invalid', 0)}"],
        ["Cities Normalized", f"{report_metrics.get('cities_normalized', 0)}"],
        ["Outliers Clipped", f"{report_metrics.get('outliers_clipped', 0)}"],
        ["Outliers Replaced (median)", f"{report_metrics.get('outliers_replaced', 0)}"],
        ["Outlier Rows Removed", f"{report_metrics.get('outlier_rows_removed', 0)}"]
    ]
    
    table = Table(metrics_data, colWidths=[250, 150])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e0e5ec')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor('#44476a')),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#f8fafc'), colors.HexColor('#ffffff')]),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#bebebe')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 6)
    ]))
    story.append(table)
    story.append(Spacer(1, 15))
    
    story.append(Paragraph("Optimization Audit Log", heading_style))
    if steps_applied:
        for idx, step in enumerate(steps_applied, 1):
            story.append(Paragraph(f"{idx}. {step}", step_style))
    else:
        story.append(Paragraph("Dataset was already clean — no transformations required.", body_style))
    
    story.append(Spacer(1, 15))
    
    story.append(Paragraph("Column Quality Diagnostics (Final State)", heading_style))
    col_data = [["Column Name", "Semantic Type", "Nulls", "Status"]]
    for col, diag in column_diagnostics.items():
        col_data.append([
            col,
            diag.get('sem_label', 'Text'),
            f"{diag.get('null_count', 0)} ({diag.get('sparsity', 0)}%)",
            diag.get('status', 'Clean')
        ])
    
    col_table = Table(col_data, colWidths=[150, 100, 100, 150])
    col_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e0e5ec')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor('#44476a')),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 9),
        ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#f8fafc'), colors.HexColor('#ffffff')]),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#bebebe')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 5)
    ]))
    story.append(col_table)
    
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


@app.route('/download_pdf/<filename>')
def download_pdf(filename: str):
    """Serve the PDF cleaning report."""
    filename = secure_filename(filename)
    if not filename:
        flash("Invalid filename.")
        return redirect(url_for('index'))
        
    raw_filename = filename[10:] if filename.startswith("optimized_") else filename
    raw_base = os.path.splitext(raw_filename)[0]
    
    # Try different fallback filenames for results JSON
    paths_to_try = [
        f"results_{raw_base}.csv.json",
        f"results_{raw_base}.xlsx.json",
        f"results_{raw_filename}.json",
        f"results_{raw_base}.json"
    ]
    
    res_json_path = None
    for p in paths_to_try:
        check_path = os.path.join(app.config['UPLOAD_FOLDER'], p)
        if os.path.exists(check_path):
            res_json_path = check_path
            break
            
    if not res_json_path:
        flash("Report session expired. Please re-upload and optimize.")
        return redirect(url_for('index'))
        
    try:
        import json
        with open(res_json_path, 'r') as f:
            results = json.load(f)
            
        pdf_data = generate_pdf_report(
            filename=raw_filename,
            report_metrics=results['report']['metrics'],
            steps_applied=results['report']['steps_applied'],
            health_before=results['health_before'],
            health_after=results['health_after'],
            column_diagnostics=results['column_diagnostics'],
            quality_before=results['report'].get('quality_before'),
            quality_after=results['report'].get('quality_after')
        )
        
        response = make_response(pdf_data)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=cleaning_report_{raw_base}.pdf'
        return response
    except Exception as e:
        logger.error(f"Failed to generate PDF report for {filename}: {e}", exc_info=True)
        flash("Failed to generate PDF report.")
        return redirect(url_for('index'))


if __name__ == '__main__':
    # Fix S6: Debug mode controlled by environment variable
    debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug_mode)
