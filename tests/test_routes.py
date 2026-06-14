import os
import json
import pytest
from app import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'temp_uploads')
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    with app.test_client() as client:
        yield client
    # Clean up temp folder
    import shutil
    if os.path.exists(app.config['UPLOAD_FOLDER']):
        shutil.rmtree(app.config['UPLOAD_FOLDER'])


def test_index_route(client):
    """Test that index page loads successfully."""
    rv = client.get('/')
    assert rv.status_code == 200
    assert b'AI Data Optimizer' in rv.data


def test_full_pipeline_via_routes(client):
    """Upload, optimize, and export CSV/XLSX/PDF."""
    # 1. Create a dummy CSV
    csv_content = (
        "name,age,email\n"
        "Rahul Sharma,30,rahul@sharma.com\n"
        "Rahul Shrma,30,rahul@sharma.com\n" # Fuzzy dupe
        "Alice Cooper,25,alice@cooper.com\n"
        "Alice Cooper,25,alice@cooper.com\n" # Exact dupe
        "Bob Wilson,,bob@wilson\n" # Null age + invalid email
    )
    
    from io import BytesIO
    data = {
        'file': (BytesIO(csv_content.encode('utf-8')), 'test_leads.csv')
    }
    
    # 2. Upload
    rv = client.post('/upload', data=data, content_type='multipart/form-data')
    assert rv.status_code == 200
    assert b'test_leads.csv' in rv.data
    assert b'Missing Values' in rv.data
    assert b'Duplicates' in rv.data
    
    # 3. Optimize
    form_data = {
        'strategy_name': 'Auto',
        'strategy_age': 'Mean',
        'strategy_email': 'Auto'
    }
    rv = client.post('/optimize/test_leads.csv', data=form_data)
    assert rv.status_code == 200
    assert b'Standardization Report' in rv.data
    assert b'Fuzzy Dupes Removed' in rv.data
    
    # 4. Export CSV
    rv = client.get('/download/optimized_test_leads.csv')
    assert rv.status_code == 200
    assert b'Rahul Sharma' in rv.data
    
    # 5. Export XLSX
    rv = client.get('/download_xlsx/optimized_test_leads.csv')
    assert rv.status_code == 200
    assert rv.headers['Content-Type'] == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    
    # 6. Export PDF report
    rv = client.get('/download_pdf/optimized_test_leads.csv')
    assert rv.status_code == 200
    assert rv.headers['Content-Type'] == 'application/pdf'
    assert rv.headers['Content-Disposition'].startswith('attachment; filename=cleaning_report_test_leads.pdf')


def test_favicon(client):
    """Test favicon returns 204 No Content fallback."""
    rv = client.get('/favicon.ico')
    assert rv.status_code == 204

