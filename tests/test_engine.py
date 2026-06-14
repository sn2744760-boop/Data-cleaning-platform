import pytest
import pandas as pd
import numpy as np
import os
from execution.cleaning_engine import (
    DataIntelligenceEngine,
    _detect_semantic_type,
    _validate_emails,
    _standardize_dates,
    _clean_numeric_string,
)


# ─── Semantic Type Detection ─────────────────────────────────────────────────

def test_semantic_type_email():
    s = pd.Series(['john@example.com', 'jane@test.org', 'bob@domain.io'])
    assert _detect_semantic_type(s) == 'email'

def test_semantic_type_date():
    s = pd.Series(['2024-01-01', '2024-02-15', '2023-12-31'])
    assert _detect_semantic_type(s) == 'date'

def test_semantic_type_boolean():
    s = pd.Series(['True', 'False', 'true', 'false'])
    assert _detect_semantic_type(s) == 'boolean'

def test_semantic_type_numeric_string():
    s = pd.Series(['1,200', '15,000', '3,500', '999'])
    assert _detect_semantic_type(s) == 'numeric_string'

def test_semantic_type_currency():
    s = pd.Series(['₹5,000', '₹12,000', '$3,500', '₹999'])
    assert _detect_semantic_type(s) == 'currency'

def test_semantic_type_text():
    s = pd.Series(['apple', 'banana', 'orange', 'grape'])
    assert _detect_semantic_type(s) == 'text'

def test_semantic_type_city():
    s = pd.Series(['Pune', 'Mumbai', 'Delhi', 'Bangalore'])
    assert _detect_semantic_type(s) == 'city'


# ─── Email Validation ────────────────────────────────────────────────────────

def test_email_validation_valid():
    s = pd.Series(['a@b.com', 'x@y.org'])
    result = _validate_emails(s)
    assert result['valid'] == 2
    assert result['invalid'] == 0

def test_email_validation_invalid():
    s = pd.Series(['abc@', '@gmail.com', 'bob@wilson', 'good@valid.com'])
    result = _validate_emails(s)
    assert result['invalid'] == 3
    assert result['valid'] == 1
    assert len(result['invalid_examples']) <= 3

def test_email_validation_empty():
    s = pd.Series([], dtype=str)
    result = _validate_emails(s)
    assert result['valid'] == 0
    assert result['invalid'] == 0


# ─── Date Standardization ────────────────────────────────────────────────────

def test_date_standardization():
    s = pd.Series(['01/01/2025', 'Jan 15 2024', '2023-12-31'])
    result, count = _standardize_dates(s)
    assert count == 3
    assert all(r == '2025-01-01' or len(r) == 10 for r in result)  # YYYY-MM-DD length

def test_date_standardization_partial():
    s = pd.Series(['2024-01-01', 'not-a-date', '2023-06-15'])
    result, count = _standardize_dates(s)
    assert count == 2  # Only 2 valid dates


# ─── Numeric String Cleaning ─────────────────────────────────────────────────

def test_clean_numeric_string_currency():
    s = pd.Series(['₹5,000', '$1,500', '€3,200'])
    result = _clean_numeric_string(s)
    assert result.iloc[0] == 5000.0
    assert result.iloc[1] == 1500.0
    assert result.iloc[2] == 3200.0

def test_clean_numeric_string_plain():
    s = pd.Series(['100', '200', '300'])
    result = _clean_numeric_string(s)
    assert list(result) == [100.0, 200.0, 300.0]


# ─── Engine: Existing Tests (updated for new API) ────────────────────────────

def test_basic_cleaning():
    data = {
        'name':  ['John ', 'Bob', 'John '],
        'age':   [28, 45, 28],
        'score': [85.5, np.nan, 85.5],
    }
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)

    assert engine.audit_results['duplicate_count'] == 1
    assert engine.audit_results['null_count'] == 1

    opt_df, report, score = engine.optimize()

    assert len(opt_df) == 2          # duplicates removed
    assert opt_df['score'].isnull().sum() == 0   # nulls filled
    assert opt_df['name'].iloc[0] == 'John'       # whitespace trimmed
    assert score >= engine.health_score


def test_skewed_data_imputation():
    data = {'income': [50000, 52000, 48000, 51000, 1000000, np.nan]}
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    opt_df, report, score = engine.optimize()
    # Median (~51000) should be used, not mean (~200k)
    assert opt_df['income'].iloc[-1] < 100000
    assert any('Median' in s for s in report['steps_applied'])


def test_categorical_mode():
    data = {
        'id': [1, 2, 3, 4, 5],
        'city': ['NY', 'NY', 'LA', 'SF', np.nan]
    }
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    opt_df, report, score = engine.optimize()
    # Filled with mode (NY) then title-cased → 'Ny'
    assert opt_df['city'].iloc[-1] == 'Ny'


def test_sparse_column_dropping():
    data = {
        'useful':  [1, 2, 3, 4, 5],
        'garbage': [np.nan, np.nan, np.nan, np.nan, 1],
    }
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    opt_df, report, score = engine.optimize({'garbage': 'Drop'})
    assert 'garbage' not in opt_df.columns
    assert any('Dropped' in s for s in report['steps_applied'])


# ─── Engine: New Tier 1 / 2 Tests ────────────────────────────────────────────

def test_numeric_string_conversion():
    """₹/$ columns should be converted to numeric automatically."""
    data = {'revenue': ['₹5,000', '₹12,000', '₹3,500']}
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    opt_df, report, score = engine.optimize()
    assert pd.api.types.is_numeric_dtype(opt_df['revenue'])
    assert opt_df['revenue'].iloc[0] == 5000.0


def test_boolean_conversion():
    """'True'/'False' string columns should be coerced to bool."""
    data = {'active': ['True', 'False', 'true', 'false']}
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    opt_df, report, score = engine.optimize()
    assert any('boolean' in s.lower() for s in report['steps_applied'])


def test_custom_fill_value():
    """Custom fill strategy should use the user-supplied value."""
    data = {'score': [85.0, np.nan, 90.0]}
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    opt_df, report, score = engine.optimize({'score': 'Custom:99.0'})
    assert opt_df['score'].iloc[1] == 99.0


def test_skip_fill_value():
    """Skip strategy should leave nulls intact."""
    data = {'notes': ['ok', np.nan, 'good']}
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    opt_df, report, score = engine.optimize({'notes': 'Skip'})
    # After title-case + strip, NaN becomes 'Nan' string via astype(str)
    # but original null count should have been left alone before text step
    assert report['metrics']['nulls_filled'] == 0


def test_min_max_in_diagnostics():
    """Min, max, and std should be present for numeric columns."""
    data = {'salary': [30000, 50000, 70000, 90000]}
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    diag = engine.column_diagnostics['salary']
    assert 'min' in diag
    assert 'max' in diag
    assert 'std' in diag
    assert diag['min'] == 30000.0
    assert diag['max'] == 90000.0


def test_email_flagging_in_optimize():
    """Invalid emails should be counted in optimization metrics."""
    data = {'email': ['good@valid.com', 'abc@', '@no.com', 'also@good.org']}
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    opt_df, report, score = engine.optimize()
    assert report['metrics']['emails_flagged'] == 2


def test_date_standardization_in_optimize():
    """Date columns should be standardized to YYYY-MM-DD during optimize."""
    data = {'signup_date': ['01/15/2024', 'Feb 20 2024', '2024-03-10']}
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    opt_df, report, score = engine.optimize()
    assert report['metrics']['dates_standardized'] >= 2


def test_metrics_rows_before_after():
    """rows_before and rows_after must be tracked in the report."""
    data = {
        'name': ['A', 'B', 'A'],
        'val':  [1, 2, 1],
    }
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    opt_df, report, score = engine.optimize()
    assert report['metrics']['rows_before'] == 3
    assert report['metrics']['rows_after'] == 2   # 1 duplicate removed
    assert report['metrics']['duplicates_dropped'] == 1


def test_health_score_improves():
    """Health score should increase (or stay same) after optimization."""
    data = {
        'name':    ['Alice', 'Bob', 'Alice', None],
        'revenue': ['₹1,000', '₹2,000', '₹1,000', '₹500'],
        'email':   ['a@b.com', 'bad@', 'a@b.com', 'c@d.com'],
    }
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    before = engine.health_score
    opt_df, report, score = engine.optimize()
    assert score >= before


def test_fuzzy_duplicates():
    """Fuzzy duplicates should be detected and removed during optimize."""
    data = {
        'name': ['Rahul Sharma', 'Rahul Shrma', 'Rahul Sharma', 'Alice Cooper', 'Alice Copr'],
        'age': [30, 31, 30, 25, 26]
    }
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    
    assert engine.audit_results['fuzzy_duplicate_count'] >= 1
    
    opt_df, report, score = engine.optimize()
    
    assert report['metrics']['fuzzy_duplicates_dropped'] >= 1
    assert any('fuzzy duplicate' in s.lower() for s in report['steps_applied'])


def test_dateparser_standardization():
    """Test dateparser standardization options (standardize, nullify, keep)."""
    data = {'date': ['Jan 1 2025', '01/15/2024', 'invalid_date']}
    df = pd.DataFrame(data)
    
    # Test standardize mode (standardize valid, keep invalid)
    engine = DataIntelligenceEngine(df)
    opt_df, report, score = engine.optimize({'date_mode_date': 'standardize'})
    assert opt_df['date'].iloc[0] == '2025-01-01'
    assert opt_df['date'].iloc[1] == '2024-01-15'
    assert opt_df['date'].iloc[2] == 'invalid_date'

    # Test nullify mode (default)
    engine = DataIntelligenceEngine(df)
    opt_df, report, score = engine.optimize({'date_mode_date': 'nullify'})
    assert opt_df['date'].iloc[0] == '2025-01-01'
    assert opt_df['date'].iloc[1] == '2024-01-15'
    assert pd.isna(opt_df['date'].iloc[2])

    # Test keep mode
    engine = DataIntelligenceEngine(df)
    opt_df, report, score = engine.optimize({'date_mode_date': 'keep'})
    assert opt_df['date'].iloc[0] == 'Jan 1 2025'


def test_email_cleaning_modes():
    """Test the three modes (flag, nullify, remove) for email cleaning."""
    data = {'email': ['good@valid.com', 'abc@', 'another@valid.org']}
    
    # Flag mode (default)
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    opt_df, report, score = engine.optimize({'email_mode_email': 'flag'})
    assert len(opt_df) == 3
    assert opt_df['email'].iloc[1] == 'abc@'
    assert report['metrics']['emails_flagged'] == 1

    # Nullify mode
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    opt_df, report, score = engine.optimize({'email_mode_email': 'nullify'})
    assert len(opt_df) == 3
    assert pd.isna(opt_df['email'].iloc[1])
    assert report['metrics']['emails_nullified'] == 1

    # Remove mode
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    opt_df, report, score = engine.optimize({'email_mode_email': 'remove'})
    assert len(opt_df) == 2
    assert opt_df['email'].iloc[1] == 'another@valid.org'
    assert report['metrics']['emails_rows_removed'] == 1


def test_phone_normalization():
    """Test phone standardizer digits strip, length check and invalid flag."""
    data = {'phone': ['+91 (123) 456-7890', '123', '00000000000']}
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    opt_df, report, score = engine.optimize()

    # The valid phone must be present and normalized to E164
    assert '+911234567890' in opt_df['phone'].values

    # All non-valid phones remaining in the output must be NaN (nullified)
    non_e164 = opt_df['phone'][opt_df['phone'] != '+911234567890']
    assert non_e164.isna().all(), f"Expected NaN for invalid phones, got: {non_e164.tolist()}"

    # At least 1 phone corrected, at least 1 invalid (exact count depends on fuzzy-dedup)
    assert report['metrics']['phones_corrected'] >= 1
    assert report['metrics']['phones_invalid'] >= 1


def test_city_normalization():
    """Test city normalization against canonical list using RapidFuzz."""
    data = {'city': ['Mumbia', 'Punne', 'Mumbai']}
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    opt_df, report, score = engine.optimize()
    
    assert opt_df['city'].iloc[0] == 'Mumbai'
    assert opt_df['city'].iloc[1] == 'Pune'
    assert report['metrics']['cities_normalized'] == 2


def test_verification_panel():
    """Test post-clean verification results match remaining counts."""
    data = {
        'email': ['good@valid.com', 'abc@'],
        'date': ['2025-01-01', 'not-a-date']
    }
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    # Using 'flag' and 'standardize' to leave them invalid
    opt_df, report, score = engine.optimize({
        'email_mode_email': 'flag',
        'date_mode_date': 'standardize'
    })
    
    verify = report['verification']
    assert verify['invalid_emails_remaining'] == 1
    assert verify['invalid_dates_remaining'] == 1
    assert verify['all_clear'] is False
    assert verify['export_status'] == 'PASS WITH WARNINGS'


def test_quality_score_v2():
    """Test 7-dimension Quality Score before/after calculations."""
    data = {
        'email': ['good@valid.com', 'abc@', None],
        'age': [25, 150, np.nan],
        'city': ['Mumbia', 'Pune', 'Delhi']
    }
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    
    # Check before score is lower due to issues
    score_before = engine.quality_score
    opt_df, report, score_after = engine.optimize()
    
    assert report['quality_before'] == score_before
    assert report['quality_after'] > score_before

