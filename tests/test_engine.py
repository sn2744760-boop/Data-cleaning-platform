import pytest
import pandas as pd
import numpy as np
import os
from execution.cleaning_engine import DataIntelligenceEngine

def test_basic_cleaning():
    data = {
        'name': ['John ', 'Bob', 'John '],
        'age': [28, 45, 28],
        'score': [85.5, np.nan, 85.5]
    }
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    
    # Audit check
    assert engine.audit_results['duplicate_count'] == 1
    assert engine.audit_results['null_count'] == 1
    
    # Process
    opt_df, report, score = engine.optimize()
    
    # Cleanup check
    assert len(opt_df) == 2  # Duplicates removed
    assert opt_df['score'].isnull().sum() == 0  # Nulls filled
    assert opt_df['name'].iloc[0] == 'John'  # Whitespace trimmed
    assert score > engine.health_score

def test_skewed_data_imputation():
    # Heavily skewed data should prefer Median
    data = {
        'income': [50000, 52000, 48000, 51000, 1000000, np.nan]
    }
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    
    opt_df, report, score = engine.optimize()
    
    # Median is roughly 51000, whereas Mean would be much higher
    assert opt_df['income'].iloc[5] < 100000 
    assert "Median" in report['steps_applied'][0]

def test_categorical_mode():
    data = {
        'city': ['NY', 'NY', 'LA', 'SF', np.nan]
    }
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    
    opt_df, report, score = engine.optimize()
    assert opt_df['city'].iloc[-1] == 'Ny' # Title Case + Mode

def test_sparse_column_dropping():
    data = {
        'useful': [1, 2, 3, 4, 5],
        'garbage': [np.nan, np.nan, np.nan, np.nan, 1]
    }
    df = pd.DataFrame(data)
    engine = DataIntelligenceEngine(df)
    
    # Dropping requires a Human-in-the-Loop override (by design)
    opt_df, report, score = engine.optimize({'garbage': 'Drop'})
    assert 'garbage' not in opt_df.columns
    assert "Dropped" in report['steps_applied'][0]
