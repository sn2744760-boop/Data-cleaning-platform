import pandas as pd
import numpy as np
import os
import logging
from typing import Dict, List, Tuple, Any, Optional
from scipy.stats import skew

# Industry Standard Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("DataIntelligenceEngine")

class DataIntelligenceEngine:
    """
    Tier 10 Data Intelligence Engine.
    Supports Human-in-the-Loop overrides and prescriptive stabilization.
    """
    def __init__(self, df: pd.DataFrame):
        self.df: pd.DataFrame = df.copy()
        self.raw_stats = self._get_base_stats(df)
        self.column_diagnostics: Dict[str, Any] = self._run_column_diagnostics(df)
        self.audit_results: Dict[str, Any] = self._audit_health(df)
        self.health_score: int = self._calculate_health_score(self.audit_results)
        self.optimization_report: Dict[str, Any] = {
            'steps_applied': [],
            'metrics': {'nulls_filled': 0, 'outliers_clipped': 0, 'duplicates_dropped': 0}
        }

    def _get_base_stats(self, df: pd.DataFrame) -> Dict[str, Any]:
        return {'rows': int(len(df)), 'cols': int(len(df.columns)), 'columns': list(df.columns)}

    def _run_column_diagnostics(self, df: pd.DataFrame) -> Dict[str, Any]:
        diagnostics = {}
        for col in df.columns:
            col_data = df[col]
            null_count = int(col_data.isnull().sum())
            sparsity = round((null_count / len(df)) * 100, 2) if len(df) > 0 else 0
            
            diag = {
                'type': str(col_data.dtype),
                'null_count': null_count,
                'sparsity': sparsity,
                'unique_count': int(col_data.nunique()),
                'status': 'Clean',
                'recommended': 'Auto'
            }

            if pd.api.types.is_numeric_dtype(col_data.dtype):
                clean_data = col_data.dropna()
                if not clean_data.empty:
                    diag['skewness'] = round(float(skew(clean_data)), 2)
                    diag['mean'] = float(clean_data.mean())
                    diag['median'] = float(clean_data.median())
                
                if sparsity > 50: diag['status'] = 'Critical (High Sparsity)'
                elif abs(diag.get('skewness', 0)) > 1.0:
                    diag['status'] = 'Warning (High Skew)'
                    diag['recommended'] = 'Median'
                else: diag['recommended'] = 'Mean'
            else:
                diag['recommended'] = 'Mode'
                clean_data = col_data.dropna()
                if not clean_data.empty:
                    m = clean_data.mode()
                    if not m.empty:
                        diag['mode'] = m.iloc[0]
                if sparsity > 50: diag['status'] = 'Critical (High Sparsity)'

            diagnostics[col] = diag
        return diagnostics

    def _audit_health(self, df: pd.DataFrame) -> Dict[str, Any]:
        null_count = int(df.isnull().sum().sum())
        cell_count = df.size
        duplicate_count = int(df.duplicated().sum())
        
        num_cols = df.select_dtypes(include=[np.number]).columns
        outlier_total = 0
        for col in num_cols:
            # Fix M1: Drop NaN before outlier detection to avoid NaN propagation
            clean = df[col].dropna()
            if clean.empty:
                continue
            q1, q3 = clean.quantile([0.25, 0.75])
            iqr = q3 - q1
            outlier_total += int(((clean < (q1 - 1.5 * iqr)) | (clean > (q3 + 1.5 * iqr))).sum())

        return {
            'null_density': round((null_count / cell_count) * 100, 2) if cell_count > 0 else 0,
            'null_count': null_count,
            'duplicate_count': duplicate_count,
            'outlier_count': outlier_total,
            'potential_dates': [c for c, d in self.column_diagnostics.items() if 'date' in c.lower()],
            'critical_cols': [c for c, d in self.column_diagnostics.items() if 'Critical' in d['status']]
        }

    def _calculate_health_score(self, audit: Dict[str, Any]) -> int:
        score = 100
        score -= min(30, audit['null_density'] * 2)
        if self.raw_stats['rows'] > 0:
            score -= min(20, (audit['duplicate_count'] / self.raw_stats['rows']) * 100)
            score -= min(20, (audit['outlier_count'] / self.raw_stats['rows']) * 5)
        return max(0, int(score))

    def _is_structured_text_column(self, col: str) -> bool:
        """Detect columns containing emails, URLs, or IDs that should NOT be title-cased."""
        sample = self.df[col].dropna().head(100)
        if sample.empty:
            return False
        # Check for email/URL patterns
        if sample.astype(str).str.contains(r'@|https?://|www\.', regex=True, na=False).any():
            return True
        return False

    def optimize(self, overrides: Dict[str, str] = None) -> Tuple[pd.DataFrame, Dict[str, Any], int]:
        overrides = overrides or {}
        
        # 1. Human-in-the-Loop Dropping
        to_drop = [c for c, s in overrides.items() if s == 'Drop']
        if to_drop:
            self.df = self.df.drop(columns=to_drop)
            self.optimization_report['steps_applied'] += [f"Manual Override: Dropped column '{c}'" for c in to_drop]
            logger.info(f"Dropped columns: {to_drop}")

        # 2. Advanced Deduplication
        init_len = len(self.df)
        self.df = self.df.drop_duplicates()
        dupes_dropped = init_len - len(self.df)
        self.optimization_report['metrics']['duplicates_dropped'] = dupes_dropped
        if dupes_dropped > 0:
            logger.info(f"Removed {dupes_dropped} duplicate rows.")

        # 3. Prescriptive Strategy Execution
        nulls_filled_total = 0
        for col in self.df.columns:
            col_nulls = int(self.df[col].isnull().sum())
            if col_nulls > 0:
                strategy = overrides.get(col, 'Auto')
                diag = self.column_diagnostics.get(col, {})
                
                if strategy == 'Auto': strategy = diag.get('recommended', 'Mode')

                if strategy == 'Median': val = diag.get('median', self.df[col].median())
                elif strategy == 'Mean': val = diag.get('mean', self.df[col].mean())
                elif strategy == 'Mode': 
                    val = diag.get('mode')
                    if val is None:
                        m = self.df[col].mode()
                        # Fix L3: Use pd.NA instead of string "N/A" to avoid injecting fake data
                        val = m.iloc[0] if not m.empty else pd.NA
                else: continue

                # Fix H2: Avoid deprecated inplace=True; use assignment instead
                self.df[col] = self.df[col].fillna(val)
                # Fix M2: Count actual null cells filled, not just columns
                nulls_filled_total += col_nulls
                self.optimization_report['steps_applied'].append(f"Auto-healed '{col}' using {strategy} strategy.")
                logger.info(f"Filled {col_nulls} nulls in '{col}' using {strategy}.")
        
        self.optimization_report['metrics']['nulls_filled'] = nulls_filled_total

        # 4. Standardization & Stabilization
        text_cols = self.df.select_dtypes(include=['object', 'string']).columns
        for col in text_cols:
            # Fix H3: Skip title-casing for email/URL columns to avoid data corruption
            if self._is_structured_text_column(col):
                # Still strip whitespace for structured text, just don't change case
                self.df[col] = self.df[col].astype(str).str.strip()
                logger.info(f"Stripped whitespace in structured column '{col}' (skipped title-case).")
            else:
                self.df[col] = self.df[col].astype(str).str.strip().str.title()
        
        # Fix M3: Outlier clipping now uses post-dedup data consistently
        num_cols = self.df.select_dtypes(include=[np.number]).columns
        outliers_clipped = 0
        for col in num_cols:
            clean = self.df[col].dropna()
            if clean.empty:
                continue
            q1, q3 = clean.quantile([0.25, 0.75])
            iqr = q3 - q1
            lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            clipped_count = int(((self.df[col] < lower) | (self.df[col] > upper)).sum())
            self.df[col] = self.df[col].clip(lower, upper)
            outliers_clipped += clipped_count

        self.optimization_report['metrics']['outliers_clipped'] = outliers_clipped
        if outliers_clipped > 0:
            logger.info(f"Clipped {outliers_clipped} outlier values.")

        # Recalculate Health
        new_diag = self._run_column_diagnostics(self.df)
        self.column_diagnostics = new_diag
        final_audit = self._audit_health(self.df)
        return self.df, self.optimization_report, self._calculate_health_score(final_audit)


def run_diagnostic(filepath: str) -> Tuple[Dict[str, Any], pd.DataFrame]:
    """
    Run diagnostic analysis on a CSV file.
    Returns both the diagnostic report and the loaded DataFrame
    to avoid redundant file reads in the caller.
    """
    df = pd.read_csv(filepath)
    engine = DataIntelligenceEngine(df)
    logger.info(f"Diagnostic complete for '{os.path.basename(filepath)}': Health={engine.health_score}%")
    diagnostic = {
        'filename': os.path.basename(filepath),
        'raw_stats': engine.raw_stats,
        'audit': engine.audit_results,
        'health_score': engine.health_score,
        'column_diagnostics': engine.column_diagnostics
    }
    return diagnostic, df


def run_optimization(filepath: str, overrides: Dict[str, str] = None) -> Dict[str, Any]:
    df = pd.read_csv(filepath)
    engine = DataIntelligenceEngine(df)
    h_before = engine.health_score
    opt_df, report, h_after = engine.optimize(overrides)
    
    out_name = f"optimized_{os.path.basename(filepath)}"
    opt_df.to_csv(os.path.join(os.path.dirname(filepath), out_name), index=False)
    logger.info(f"Optimization complete: Health {h_before}% -> {h_after}%")
    
    return {
        'output_file': out_name,
        'report': report,
        'health_before': h_before,
        'health_after': h_after,
        'column_diagnostics': engine.column_diagnostics
    }
