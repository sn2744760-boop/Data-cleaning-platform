"""
AI Data Optimizer v10 — Cleaning Engine
Deterministic, rule-based data cleaning & standardization.
"""

import pandas as pd
import numpy as np
import os
import re
import logging
import unicodedata
from typing import Dict, List, Tuple, Any, Optional
from scipy.stats import skew
from rapidfuzz import process as rf_process, fuzz as rf_fuzz
from email_validator import validate_email, EmailNotValidError
import phonenumbers
from thefuzz import fuzz

# ─── Optional dateparser (grace fallback) ─────────────────────────────────
try:
    import dateparser
    _HAS_DATEPARSER = True
except ImportError:
    _HAS_DATEPARSER = False

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("DataIntelligenceEngine")

# ─── Regex Patterns ─────────────────────────────────────────────────────────
EMAIL_REGEX    = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]{2,}$')
PHONE_REGEX    = re.compile(r'^[\+\(\)\-\s\d\.]{7,20}$')
CURRENCY_REGEX = re.compile(r'^[₹$€£¥\s]*-?[\d,]+(\.\d+)?$')
DATE_HINT_REGEX = re.compile(
    r'\d{2,4}[-/\.]\d{1,2}|[A-Za-z]{3,9}\s+\d{1,2}|\d{1,2}\s+[A-Za-z]{3,9}'
)
PHONE_CLEAN_RE  = re.compile(r'[^\d+]')

# Strict YYYY-MM-DD — must be a calendar-valid date
STRICT_DATE_RE = re.compile(r'^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$')

# ─── Boolean string mapping ──────────────────────────────────────────────────
BOOL_MAP: Dict[str, bool] = {
    'true': True, 'false': False,
    'yes': True,  'no': False,
    't': True,    'f': False,
    'y': True,    'n': False,
    '1': True,    '0': False,
}

# ─── Semantic type → display label + badge class ────────────────────────────
SEMANTIC_LABELS: Dict[str, Tuple[str, str]] = {
    'email':          ('Email',          'sem-email'),
    'phone':          ('Phone',          'sem-phone'),
    'date':           ('Date',           'sem-date'),
    'boolean':        ('Boolean',        'sem-bool'),
    'numeric_string': ('Numeric String', 'sem-numstr'),
    'integer':        ('Integer',        'sem-num'),
    'float':          ('Float',          'sem-num'),
    'currency':       ('Currency',       'sem-numstr'),
    'name':           ('Name',           'sem-text'),
    'city':           ('City',           'sem-text'),
    'address':        ('Address',        'sem-text'),
    'id':             ('ID',             'sem-num'),
    'age':            ('Age',            'sem-num'),
    'text':           ('Text',           'sem-text'),
    'unknown':        ('Unknown',        'sem-text'),
}

# ─── Canonical City Dictionary (Indian + global) ─────────────────────────────
CANONICAL_CITIES: List[str] = [
    # India
    "Mumbai", "Delhi", "Bangalore", "Bengaluru", "Hyderabad", "Chennai",
    "Kolkata", "Pune", "Ahmedabad", "Jaipur", "Surat", "Lucknow", "Kanpur",
    "Nagpur", "Indore", "Thane", "Bhopal", "Visakhapatnam", "Pimpri-Chinchwad",
    "Patna", "Vadodara", "Ghaziabad", "Ludhiana", "Agra", "Nashik", "Faridabad",
    "Meerut", "Rajkot", "Kalyan-Dombivali", "Vasai-Virar", "Varanasi",
    "Srinagar", "Aurangabad", "Dhanbad", "Amritsar", "Navi Mumbai", "Allahabad",
    "Howrah", "Ranchi", "Coimbatore", "Jabalpur", "Gwalior", "Vijayawada",
    "Jodhpur", "Madurai", "Raipur", "Kota", "Guwahati", "Chandigarh",
    "Thiruvananthapuram", "Mysuru", "Mysore", "Noida", "Gurugram", "Gurgaon",
    # Global
    "New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Philadelphia",
    "San Antonio", "San Diego", "Dallas", "San Jose", "London", "Paris",
    "Berlin", "Tokyo", "Beijing", "Shanghai", "Sydney", "Melbourne",
    "Toronto", "Vancouver", "Dubai", "Singapore", "Hong Kong",
]

# ─── Column-name heuristics for semantic detection ──────────────────────────
_NAME_HINTS    = {'name', 'full_name', 'fullname', 'first_name', 'last_name',
                  'fname', 'lname', 'customer_name', 'client_name'}
_EMAIL_HINTS   = {'email', 'email_address', 'e_mail', 'mail'}
_PHONE_HINTS   = {'phone', 'mobile', 'cell', 'telephone', 'tel', 'contact',
                  'phone_number', 'mobile_number', 'contact_number'}
_DATE_HINTS    = {'date', 'dob', 'birth', 'created', 'updated', 'signup',
                  'join', 'joined', 'timestamp', 'order_date', 'purchase_date',
                  'date_of_birth', 'created_at', 'updated_at'}
_CITY_HINTS    = {'city', 'town', 'location', 'place', 'district', 'municipality'}
_ADDRESS_HINTS = {'address', 'addr', 'street', 'locality', 'area'}
_ID_HINTS      = {'id', 'uid', 'user_id', 'customer_id', 'order_id', 'employee_id',
                  'product_id', 'transaction_id', 'account_id', 'ref', 'reference'}
_AGE_HINTS     = {'age', 'years_old', 'age_years'}


# ═══════════════════════════════════════════════════════════════════════════
#  Module-level helpers
# ═══════════════════════════════════════════════════════════════════════════

def _load_file(filepath: str) -> pd.DataFrame:
    """Load CSV or XLSX/XLS file into a DataFrame."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext in ('.xlsx', '.xls'):
        return pd.read_excel(filepath, engine='openpyxl')
    return pd.read_csv(filepath)


def _detect_semantic_type(series: pd.Series, col_name: str = '') -> str:
    """
    Infer semantic column type from values + column-name heuristics.
    Returns one of: email, phone, date, boolean, numeric_string, currency,
                    name, city, address, id, age, integer, float, text, unknown.
    """
    col_lower = col_name.lower().replace(' ', '_').replace('-', '_')
    tokens = set(col_lower.split('_'))

    # ── Column-name fast-path ─────────────────────────────────────────────
    if (tokens & _EMAIL_HINTS) or col_lower in _EMAIL_HINTS:   return 'email'
    if (tokens & _PHONE_HINTS) or col_lower in _PHONE_HINTS:   return 'phone'
    if (tokens & _DATE_HINTS) or col_lower in _DATE_HINTS:    return 'date'
    if (tokens & _NAME_HINTS) or col_lower in _NAME_HINTS:    return 'name'
    if (tokens & _CITY_HINTS) or col_lower in _CITY_HINTS:    return 'city'
    if (tokens & _ADDRESS_HINTS) or col_lower in _ADDRESS_HINTS: return 'address'
    if (tokens & _ID_HINTS) or col_lower in _ID_HINTS or col_lower.endswith('_id') or col_lower.startswith('id_') or col_lower == 'id':      return 'id'
    if (tokens & _AGE_HINTS) or col_lower in _AGE_HINTS:     return 'age'

    sample = series.dropna().astype(str).str.strip()
    if sample.empty:
        return 'unknown'

    sample = sample.head(100)
    lower  = sample.str.lower()

    # ── Boolean ───────────────────────────────────────────────────────────
    if set(lower.unique()) <= set(BOOL_MAP.keys()):
        return 'boolean'

    # ── Email ─────────────────────────────────────────────────────────────
    if sample.str.contains('@', na=False).mean() > 0.5:
        return 'email'

    # ── Date ──────────────────────────────────────────────────────────────
    date_hint_rate = sample.str.contains(DATE_HINT_REGEX, regex=True, na=False).mean()
    if date_hint_rate > 0.5:
        try:
            parsed = pd.to_datetime(sample, format='mixed', dayfirst=False, errors='coerce')
            if parsed.notna().mean() > 0.65:
                return 'date'
        except Exception:
            try:
                parsed = pd.to_datetime(sample, errors='coerce')
                if parsed.notna().mean() > 0.65:
                    return 'date'
            except Exception:
                pass

    # ── Numeric string (currency symbols / commas) ────────────────────────
    cleaned = sample.str.replace(r'[₹$€£¥,\s]', '', regex=True)
    has_currency_sym = sample.str.contains(r'[₹$€£¥]', regex=True).mean() > 0.3
    if cleaned.str.match(r'^-?\d+(\.\d+)?$').mean() > 0.7:
        return 'currency' if has_currency_sym else 'numeric_string'

    # ── Phone ─────────────────────────────────────────────────────────────
    if sample.str.match(PHONE_REGEX).mean() > 0.6:
        return 'phone'

    # ── City (check against canonical list with fuzzy) ────────────────────
    if len(sample) >= 3:
        city_hits = 0
        for v in sample.head(20):
            if rf_process.extractOne(v, CANONICAL_CITIES, score_cutoff=82):
                city_hits += 1
        if city_hits / max(len(sample.head(20)), 1) > 0.5:
            return 'city'

    return 'text'


def _validate_emails(series: pd.Series) -> Dict[str, Any]:
    """Categorize emails into valid / malformed / suspicious using email-validator."""
    non_null = series.dropna().astype(str)
    if non_null.empty:
        return {'valid': 0, 'malformed': 0, 'suspicious': 0, 'invalid': 0,
                'invalid_examples': []}

    valid_count = 0
    malformed_count = 0
    suspicious_count = 0
    invalid_examples = []

    for val in non_null:
        # Pre-process: remove whitespace
        val_clean = re.sub(r'\s+', '', str(val).strip())
        
        try:
            validate_email(val_clean, check_deliverability=False)
            valid_count += 1
        except EmailNotValidError:
            if '@' not in val_clean or val_clean.startswith('@') or val_clean.endswith('@'):
                malformed_count += 1
            else:
                suspicious_count += 1
            
            if len(invalid_examples) < 3:
                invalid_examples.append(str(val))

    return {
        'valid':            valid_count,
        'malformed':        malformed_count,
        'suspicious':       suspicious_count,
        'invalid':          malformed_count + suspicious_count,
        'invalid_examples': invalid_examples,
    }


def _clean_and_validate_email(val: Any) -> Tuple[Optional[str], str]:
    """
    Validate and clean email using email-validator.
    Returns: (cleaned_email, status)
    status can be: 'valid', 'corrected', 'invalid'
    """
    if pd.isna(val) or str(val).strip() == '':
        return None, 'invalid'
    
    val_str = str(val).strip()
    # Rule 2: Remove whitespace
    val_str = re.sub(r'\s+', '', val_str)
    
    try:
        email_info = validate_email(val_str, check_deliverability=False)
        # Rule 1: Normalize valid emails
        cleaned = email_info.normalized
        
        # Determine if it was corrected
        if cleaned != str(val):
            return cleaned, 'corrected'
        else:
            return cleaned, 'valid'
    except EmailNotValidError:
        return None, 'invalid'



# Regex for slash/dot delimited dates: YYYY/MM/DD or YYYY.MM.DD or YYYY-MM-DD variants
# Captures year, middle, and day segments separately so we can validate the month.
_SLASH_DATE_RE = re.compile(
    r'^(\d{2,4})[/\.\-](\d{1,2})[/\.\-](\d{1,2})$'
)


def _pre_screen_date(val_str: str) -> bool:
    """
    Return False if the date string contains an impossible month or day segment
    that pandas would silently reinterpret (e.g. 2025/13/01 → 2025-01-13).

    Only screens slash/dot/dash-delimited numeric strings where the
    middle segment is unambiguously a month position (year-month-day order).
    Returns True if the string MAY be valid and should be passed to parsers.
    """
    m = _SLASH_DATE_RE.match(val_str.strip())
    if not m:
        # Not a purely numeric delimited date — let parsers decide
        return True

    year_seg  = int(m.group(1))
    mid_seg   = int(m.group(2))
    right_seg = int(m.group(3))

    # Determine order: if year_seg > 31, treat as YYYY-MM-DD
    if year_seg > 31:
        month_candidate = mid_seg
        day_candidate   = right_seg
    else:
        # Could be DD-MM-YYYY or MM-DD-YYYY; be conservative:
        # if either mid or right > 12 AND > 31 → garbage
        month_candidate = mid_seg
        day_candidate   = right_seg

    if month_candidate < 1 or month_candidate > 12:
        return False  # impossible month
    if day_candidate < 1 or day_candidate > 31:
        return False  # impossible day
    return True


def _parse_single_date(val_str: str) -> Optional[pd.Timestamp]:
    """
    Attempt to parse a single date string.
    Returns a pd.Timestamp if successful, else None.

    Strategy:
      0. Pre-screen for impossible month/day values.
      1. pandas to_datetime with explicit format strings (strict).
      2. dateparser (handles natural language: 'Jan 1 2025', etc.)
    """
    # Pre-screen: reject strings with impossible numeric segments
    if not _pre_screen_date(val_str):
        return None

    # Try a set of explicit format strings first (no ambiguity)
    _EXPLICIT_FORMATS = [
        '%Y-%m-%d',   # 2025-01-15
        '%d-%m-%Y',   # 15-01-2025
        '%m-%d-%Y',   # 01-15-2025
        '%Y/%m/%d',   # 2025/01/15
        '%d/%m/%Y',   # 15/01/2025
        '%m/%d/%Y',   # 01/15/2025
        '%d.%m.%Y',   # 15.01.2025
        '%Y.%m.%d',   # 2025.01.15
        '%d %b %Y',   # 15 Jan 2025
        '%d %B %Y',   # 15 January 2025
        '%b %d %Y',   # Jan 15 2025
        '%B %d %Y',   # January 15 2025
        '%d-%b-%Y',   # 15-Jan-2025
        '%b-%d-%Y',   # Jan-15-2025
        '%d/%b/%Y',   # 15/Jan/2025
        '%m/%d/%y',   # 01/15/25
        '%d/%m/%y',   # 15/01/25
        '%y-%m-%d',   # 25-01-15
    ]
    for fmt in _EXPLICIT_FORMATS:
        try:
            ts = pd.to_datetime(val_str, format=fmt)
            if not pd.isna(ts):
                return ts
        except Exception:
            continue

    # Fallback: dateparser for natural language (e.g. 'Jan 1 2025', '1st Jan 2025')
    if _HAS_DATEPARSER:
        try:
            dp = dateparser.parse(val_str, settings={
                'PREFER_DAY_OF_MONTH': 'first',
                'RETURN_AS_TIMEZONE_AWARE': False,
                'STRICT_PARSING': False,
            })
            if dp is not None:
                return pd.Timestamp(dp)
        except Exception:
            pass

    return None


def _is_strictly_valid_date_string(val_str: str) -> bool:
    """
    Return True only if val_str is already a calendar-valid YYYY-MM-DD string.
    e.g. '2025-01-01' → True, '2025-13-01' → False, '2025-00-01' → False.
    """
    if not STRICT_DATE_RE.match(val_str):
        return False
    # Additional calendar check via pandas
    try:
        pd.to_datetime(val_str, format='%Y-%m-%d')
        return True
    except Exception:
        return False


def _standardize_dates_v2(series: pd.Series, mode: str = 'nullify') -> Tuple[pd.Series, int, int, int]:
    """
    Strict date cleaning pipeline.

    For each non-null value:
      1. If already a calendar-valid YYYY-MM-DD → keep, count as converted.
      2. Otherwise attempt parsing via pandas + dateparser.
      3. If parsed successfully → format as YYYY-MM-DD, count as converted.
      4. If parsing fails → count as rejected.
         - mode='nullify'      → replace with NaN  (default, recommended)
         - mode='standardize'  → keep original string
         - mode='keep'         → no changes at all

    Returns: (result_series, detected, converted, rejected)
    """
    if mode == 'keep':
        return series.copy(), 0, 0, 0

    result    = series.copy().astype(object)
    detected  = 0
    converted = 0
    rejected  = 0

    for idx, val in series.items():
        if pd.isna(val) or str(val).strip() == '':
            continue
        val_str = str(val).strip()
        detected += 1

        # Fast-path: already a strict YYYY-MM-DD (calendar-valid)
        if _is_strictly_valid_date_string(val_str):
            # Already in correct format and valid — no change needed
            result[idx] = val_str
            converted += 1
            continue

        # Attempt full parse
        parsed = _parse_single_date(val_str)

        if parsed is not None and not pd.isna(parsed):
            result[idx] = parsed.strftime('%Y-%m-%d')
            converted += 1
        else:
            rejected += 1
            if mode == 'nullify':
                result[idx] = np.nan
            else:
                # mode == 'standardize': keep original invalid string as-is
                result[idx] = val_str

    return result, detected, converted, rejected


def _standardize_dates(series: pd.Series) -> Tuple[pd.Series, int]:
    """Legacy-compatible wrapper around _standardize_dates_v2."""
    result, detected, converted, rejected = _standardize_dates_v2(series)
    return result, converted


def _clean_numeric_string(series: pd.Series) -> pd.Series:
    """Strip currency symbols, commas, spaces then coerce to float."""
    stripped = series.astype(str).str.replace(r'[₹$€£¥,\s]', '', regex=True)
    return pd.to_numeric(stripped, errors='coerce')


def _clean_and_validate_phone(val: Any) -> Tuple[Optional[str], str]:
    """
    Validate and clean phone using phonenumbers library.
    Returns: (cleaned_value, status) where status is 'valid'|'corrected'|'invalid'
    """
    if not val or pd.isna(val):
        return val, 'invalid'
    val_str = str(val).strip()

    try:
        parsed = phonenumbers.parse(val_str, 'IN')
        if not phonenumbers.is_valid_number(parsed):
            return None, 'invalid'
        cleaned = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        if cleaned == val_str:
            return cleaned, 'valid'
        else:
            return cleaned, 'corrected'
    except Exception:
        return None, 'invalid'


def _clean_phone(val: str) -> Tuple[str, str]:
    """Legacy wrapper for phone cleaning using phonenumbers."""
    cleaned, status = _clean_and_validate_phone(val)
    if status == 'invalid':
        return val, 'invalid'
    return cleaned, 'ok'



def _normalize_city(val: str, threshold: int = 80) -> Tuple[str, bool]:
    """
    Normalize a city name against the canonical list using RapidFuzz.
    Uses a threshold of 80 to ensure high-confidence matches only.
    Returns: (normalized_value, was_changed)
    """
    if not val or pd.isna(val):
        return val, False
    val_str = str(val).strip()
    result  = rf_process.extractOne(val_str, CANONICAL_CITIES,
                                    scorer=rf_fuzz.WRatio,
                                    score_cutoff=threshold)
    if result and result[0] != val_str:
        return result[0], True
    return val_str, False


def _clean_age_series(series: pd.Series) -> Tuple[pd.Series, int]:
    """
    Enforce age business rule: 0 <= age <= 120.
    Values outside this range are replaced with NaN.
    Returns: (cleaned_series, count_nullified)
    """
    numeric = pd.to_numeric(series, errors='coerce')
    invalid_mask = numeric.notna() & ((numeric < 0) | (numeric > 120))
    count = int(invalid_mask.sum())
    if count > 0:
        numeric[invalid_mask] = np.nan
    return numeric, count


def _post_clean_verify(df: pd.DataFrame,
                        column_diagnostics: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run a second-pass audit after ALL cleaning steps on the FINAL dataframe.
    Every metric here is derived from the actual exported data — no estimates.
    Returns a verification dict with remaining issue counts.
    """
    remaining_invalid_emails  = 0
    remaining_invalid_dates   = 0
    remaining_invalid_phones  = 0
    remaining_outliers        = 0
    remaining_duplicates      = int(df.duplicated().sum())
    remaining_nulls           = int(df.isnull().sum().sum())

    for col, diag in column_diagnostics.items():
        if col not in df.columns:
            continue
        sem      = diag.get('semantic', 'text')
        col_data = df[col]

        if sem == 'email':
            non_null = col_data.dropna()
            for v in non_null:
                _, status = _clean_and_validate_email(v)
                if status == 'invalid':
                    remaining_invalid_emails += 1

        elif sem == 'date':
            # Count values that are NOT calendar-valid YYYY-MM-DD
            non_null = col_data.dropna().astype(str).str.strip()
            if not non_null.empty:
                bad = non_null[~non_null.apply(_is_strictly_valid_date_string)]
                remaining_invalid_dates += len(bad)

        elif sem == 'phone':
            non_null = col_data.dropna()
            for v in non_null:
                _, status = _clean_and_validate_phone(v)
                if status == 'invalid':
                    remaining_invalid_phones += 1

    # Outlier count across numeric columns
    num_cols = df.select_dtypes(include=[np.number]).columns
    for col in num_cols:
        clean = df[col].dropna()
        if clean.empty:
            continue
        q1, q3 = clean.quantile([0.25, 0.75])
        iqr = q3 - q1
        if iqr == 0:
            continue
        remaining_outliers += int(((clean < q1 - 1.5 * iqr) | (clean > q3 + 1.5 * iqr)).sum())

    all_clear = (
        remaining_invalid_emails == 0 and
        remaining_invalid_dates  == 0 and
        remaining_invalid_phones == 0 and
        remaining_duplicates     == 0
    )
    export_status = 'PASS' if all_clear else 'PASS WITH WARNINGS'

    return {
        'invalid_emails_remaining':  remaining_invalid_emails,
        'invalid_dates_remaining':   remaining_invalid_dates,
        'invalid_phones_remaining':  remaining_invalid_phones,
        'outliers_remaining':        remaining_outliers,
        'duplicates_remaining':      remaining_duplicates,
        'nulls_remaining':           remaining_nulls,
        'all_clear':                 all_clear,
        'export_status':             export_status,
    }


def _calculate_quality_score_v2(audit: Dict[str, Any],
                                  total_rows: int,
                                  total_cols: int) -> int:
    """
    7-dimension Quality Score (0–100).
    Penalties: missing(20) + duplicates(15) + invalid_types(15) +
               invalid_emails(10) + invalid_dates(10) + outliers(15) + norm(15)
    """
    score = 100
    cell_count = max(total_rows * total_cols, 1)

    # 1. Missing values (max -20)
    null_density = audit.get('null_density', 0)
    score -= min(20, null_density * 1.5)

    # 2. Duplicates (max -15)
    if total_rows > 0:
        dup_rate = audit.get('duplicate_count', 0) / total_rows
        score -= min(15, dup_rate * 100)

    # 3. Invalid types (max -15)
    numeric_str_count = len(audit.get('numeric_str_cols', []))
    score -= min(15, numeric_str_count * 4)

    # 4. Invalid emails (max -10)
    if total_rows > 0 and audit.get('total_email_issues', 0) > 0:
        email_bad_rate = audit['total_email_issues'] / total_rows
        score -= min(10, email_bad_rate * 30)

    # 5. Invalid dates (max -10)
    date_rejected = audit.get('dates_rejected', 0)
    if total_rows > 0 and date_rejected > 0:
        score -= min(10, (date_rejected / total_rows) * 30)

    # 6. Outliers (max -15)
    if total_rows > 0:
        outlier_rate = audit.get('outlier_count', 0) / total_rows
        score -= min(15, outlier_rate * 5)

    # 7. Normalization issues — city / phone problems (max -15)
    phone_cols = audit.get('phone_cols', [])
    city_cols  = audit.get('city_cols', [])
    norm_issue_cols = len(phone_cols) + len(city_cols)
    score -= min(15, norm_issue_cols * 4)

    return max(0, int(score))


# ═══════════════════════════════════════════════════════════════════════════════
#  DataIntelligenceEngine v10
# ═══════════════════════════════════════════════════════════════════════════════
class DataIntelligenceEngine:
    """
    AI Data Optimizer v10 — Full cleaning & standardization engine.
    Deterministic, rule-based, post-clean verified.
    """

    def __init__(self, df: pd.DataFrame):
        self.df: pd.DataFrame = df.copy()
        self.raw_stats         = self._get_base_stats(df)
        self.column_diagnostics: Dict[str, Any] = self._run_column_diagnostics(df)
        self.audit_results:      Dict[str, Any] = self._audit_health(df)
        self.health_score: int   = self._calculate_health_score(self.audit_results)
        self.quality_score: int  = _calculate_quality_score_v2(
            self.audit_results,
            self.raw_stats['rows'],
            self.raw_stats['cols']
        )
        self.optimization_report: Dict[str, Any] = {
            'steps_applied': [],
            'metrics': {
                'rows_before':            int(len(df)),
                'rows_after':             int(len(df)),
                'nulls_filled':           0,
                'outliers_clipped':       0,
                'outliers_replaced':      0,
                'outlier_rows_removed':   0,
                'duplicates_dropped':     0,
                'fuzzy_duplicates_dropped': 0,
                'emails_flagged':         0,
                'emails_nullified':       0,
                'emails_rows_removed':    0,
                'dates_detected':         0,
                'dates_standardized':     0,
                'dates_rejected':         0,
                'types_converted':        0,
                'phones_corrected':       0,
                'phones_flagged_invalid': 0,
                'cities_normalized':      0,
                'ages_nullified':         0,
                'emails_found':           0,
                'emails_valid':           0,
                'emails_invalid':         0,
                'emails_corrected':       0,
                'emails_removed':         0,
                'emails_remaining':       0,
                'phones_found':           0,
                'phones_valid':           0,
                'phones_invalid':         0,
                'phones_corrected':       0,
                'phones_remaining':       0,
            },
        }

    # ── Base Stats ─────────────────────────────────────────────────────────
    def _get_base_stats(self, df: pd.DataFrame) -> Dict[str, Any]:
        return {
            'rows': int(len(df)),
            'cols': int(len(df.columns)),
            'columns': list(df.columns)
        }

    # ── Column Diagnostics ─────────────────────────────────────────────────
    def _run_column_diagnostics(self, df: pd.DataFrame) -> Dict[str, Any]:
        diagnostics = {}
        for col in df.columns:
            col_data   = df[col]
            null_count = int(col_data.isnull().sum())
            sparsity   = round((null_count / len(df)) * 100, 2) if len(df) > 0 else 0

            is_string_col = (
                pd.api.types.is_object_dtype(col_data) or
                pd.api.types.is_string_dtype(col_data)
            ) and not pd.api.types.is_bool_dtype(col_data)

            if pd.api.types.is_bool_dtype(col_data):
                semantic = 'boolean'
            elif pd.api.types.is_datetime64_any_dtype(col_data):
                semantic = 'date'
            elif pd.api.types.is_integer_dtype(col_data):
                semantic = 'integer'
            elif pd.api.types.is_float_dtype(col_data):
                semantic = 'float'
            elif is_string_col:
                semantic = _detect_semantic_type(col_data, col_name=col)
            else:
                semantic = 'text'

            sem_label, sem_class = SEMANTIC_LABELS.get(semantic, ('Text', 'sem-text'))

            diag: Dict[str, Any] = {
                'type':        str(col_data.dtype),
                'semantic':    semantic,
                'sem_label':   sem_label,
                'sem_class':   sem_class,
                'null_count':  null_count,
                'sparsity':    sparsity,
                'unique_count': int(col_data.nunique()),
                'status':      'Clean',
                'recommended': 'Auto',
            }

            # ── Numeric stats ──────────────────────────────────────────────
            if pd.api.types.is_numeric_dtype(col_data.dtype):
                clean = col_data.dropna()
                if not clean.empty:
                    diag['skewness'] = round(float(skew(clean)), 2)
                    diag['mean']     = round(float(clean.mean()),   4)
                    diag['median']   = round(float(clean.median()), 4)
                    diag['min']      = round(float(clean.min()),    4)
                    diag['max']      = round(float(clean.max()),    4)
                    diag['std']      = round(float(clean.std()),    4)

                    # Age bounds check: flag if min < 0 or max > 120
                    if semantic == 'age' or col.lower() in _AGE_HINTS:
                        invalid_ages = int(((clean < 0) | (clean > 120)).sum())
                        if invalid_ages > 0:
                            diag['status'] = f'Warning ({invalid_ages} out-of-range ages)'

                if sparsity > 50:
                    diag['status'] = 'Critical (High Sparsity)'
                if abs(diag.get('skewness', 0)) > 1.0:
                    if 'Critical' not in diag.get('status', ''):
                        diag['status'] = 'Warning (High Skew)'
                    diag['recommended'] = 'Median'
                else:
                    diag['recommended'] = 'Mean'

            else:
                if semantic in ('email', 'phone', 'date'):
                    diag['recommended'] = 'Skip'
                else:
                    diag['recommended'] = 'Mode'
                clean = col_data.dropna()
                if not clean.empty:
                    m = clean.mode()
                    if not m.empty:
                        diag['mode'] = str(m.iloc[0])

                if semantic == 'email':
                    ev = _validate_emails(col_data)
                    diag['email_issues']            = ev['invalid']
                    diag['email_malformed']         = ev['malformed']
                    diag['email_suspicious']        = ev['suspicious']
                    diag['invalid_email_examples']  = ev['invalid_examples']
                    if ev['invalid'] > 0:
                        diag['status'] = f"Warning ({ev['invalid']} invalid emails)"

                if sparsity > 50:
                    diag['status'] = 'Critical (High Sparsity)'

            diagnostics[col] = diag
        return diagnostics

    # ── Audit Health ───────────────────────────────────────────────────────
    def _audit_health(self, df: pd.DataFrame) -> Dict[str, Any]:
        null_count      = int(df.isnull().sum().sum())
        cell_count      = df.size
        duplicate_count = int(df.duplicated().sum())

        num_cols = df.select_dtypes(include=[np.number]).columns
        outlier_total = 0
        for col in num_cols:
            clean = df[col].dropna()
            if clean.empty:
                continue
            q1, q3 = clean.quantile([0.25, 0.75])
            iqr    = q3 - q1
            if iqr == 0:
                continue
            outlier_total += int(((clean < q1 - 1.5 * iqr) | (clean > q3 + 1.5 * iqr)).sum())

        email_cols     = [c for c, d in self.column_diagnostics.items() if d.get('semantic') == 'email']
        date_cols      = [c for c, d in self.column_diagnostics.items() if d.get('semantic') == 'date']
        phone_cols     = [c for c, d in self.column_diagnostics.items() if d.get('semantic') == 'phone']
        city_cols      = [c for c, d in self.column_diagnostics.items() if d.get('semantic') == 'city']
        num_str_cols   = [c for c, d in self.column_diagnostics.items() if d.get('semantic') in ('numeric_string','currency')]
        critical_cols  = [c for c, d in self.column_diagnostics.items() if 'Critical' in d['status']]
        total_email_issues = sum(self.column_diagnostics[c].get('email_issues', 0) for c in email_cols)

        # Fuzzy duplicate quick-scan
        fuzzy_key_col = None
        for col, diag in self.column_diagnostics.items():
            if diag.get('semantic') in ('text', 'name', 'email', 'phone', 'unknown') \
                    and diag.get('unique_count', 0) > 2:
                fuzzy_key_col = col
                break

        fuzzy_duplicate_count = 0
        if fuzzy_key_col and not df.empty:
            unique_vals = list(df[fuzzy_key_col].dropna().unique())[:200]
            for i in range(len(unique_vals)):
                v1 = str(unique_vals[i]).strip()
                if len(v1) < 3:
                    continue
                for j in range(i + 1, len(unique_vals)):
                    v2 = str(unique_vals[j]).strip()
                    if len(v2) < 3:
                        continue
                    if v1.lower() != v2.lower() and fuzz.token_sort_ratio(v1, v2) >= 85:
                        fuzzy_duplicate_count += 1

        return {
            'null_density':         round((null_count / cell_count) * 100, 2) if cell_count > 0 else 0,
            'null_count':           null_count,
            'duplicate_count':      duplicate_count,
            'fuzzy_duplicate_count': fuzzy_duplicate_count,
            'fuzzy_key_col':        fuzzy_key_col,
            'outlier_count':        outlier_total,
            'email_cols':           email_cols,
            'date_cols':            date_cols,
            'phone_cols':           phone_cols,
            'city_cols':            city_cols,
            'numeric_str_cols':     num_str_cols,
            'critical_cols':        critical_cols,
            'total_email_issues':   total_email_issues,
            'dates_rejected':       0,   # updated after optimize
        }

    # ── Health Score (legacy) ──────────────────────────────────────────────
    def _calculate_health_score(self, audit: Dict[str, Any]) -> int:
        score = 100
        score -= min(30, audit['null_density'] * 2)
        rows   = self.raw_stats['rows']
        if rows > 0:
            score -= min(20, (audit['duplicate_count'] / rows) * 100)
            score -= min(15, (audit['outlier_count']   / rows) * 5)
            if audit.get('fuzzy_duplicate_count', 0) > 0:
                score -= min(10, audit['fuzzy_duplicate_count'] * 2)
        if rows > 0 and audit.get('total_email_issues', 0) > 0:
            score -= min(10, (audit['total_email_issues'] / rows) * 20)
        score -= min(10, len(audit.get('numeric_str_cols', [])) * 3)
        return max(0, int(score))

    # ── Structured text detection (skip title-case) ────────────────────────
    def _is_structured_text_column(self, col: str) -> bool:
        sem = self.column_diagnostics.get(col, {}).get('semantic', 'text')
        return sem in ('email', 'phone', 'date', 'boolean', 'numeric_string', 'currency', 'id')

    # ══════════════════════════════════════════════════════════════════════
    #  optimize() — v10 pipeline (10 steps)
    # ══════════════════════════════════════════════════════════════════════
    def optimize(self, overrides: Dict[str, str] = None) -> Tuple[pd.DataFrame, Dict[str, Any], int]:
        overrides   = overrides or {}
        logger.info(f"Original rows: {len(self.df)}")
        drop_set    = {c for c, s in overrides.items() if s == 'Drop'}
        # Email cleaning mode per-column (override key: 'email_mode_<col>')
        email_modes = {
            c: overrides.get(f'email_mode_{c}', 'flag')   # default: flag (as per specs)
            for c in self.df.columns
        }
        # Date cleaning mode per-column (override key: 'date_mode_<col>')
        date_modes = {
            c: overrides.get(f'date_mode_{c}', 'nullify')
            for c in self.df.columns
        }

        # Snapshot semantic types BEFORE any transformation
        pre_semantics = {
            col: self.column_diagnostics.get(col, {}).get('semantic', 'text')
            for col in self.df.columns
        }

        # ── Step 0: Type Conversion (numeric_string, boolean) ─────────────
        for col in list(self.df.columns):
            if col in drop_set:
                continue
            sem = pre_semantics[col]

            if sem in ('numeric_string', 'currency'):
                converted = _clean_numeric_string(self.df[col])
                if converted.notna().sum() / max(len(converted), 1) >= 0.5:
                    self.df[col] = converted
                    self.optimization_report['metrics']['types_converted'] += 1
                    self.optimization_report['steps_applied'].append(
                        f"Converted '{col}' from currency/string to numeric."
                    )

            elif sem == 'boolean':
                try:
                    lower  = self.df[col].astype(str).str.lower().str.strip()
                    mapped = lower.map(BOOL_MAP)
                    if mapped.notna().sum() / max(len(mapped), 1) >= 0.8:
                        self.df[col] = mapped
                        self.optimization_report['metrics']['types_converted'] += 1
                        self.optimization_report['steps_applied'].append(
                            f"Converted '{col}' to boolean (True/False)."
                        )
                except Exception as exc:
                    logger.warning(f"Boolean conversion failed for '{col}': {exc}")

        self.column_diagnostics = self._run_column_diagnostics(self.df)

        # ── Step 1: Column Dropping ───────────────────────────────────────
        existing_drops = [c for c in drop_set if c in self.df.columns]
        if existing_drops:
            self.df = self.df.drop(columns=existing_drops)
            self.optimization_report['steps_applied'] += [
                f"Manual Override: Dropped column '{c}'" for c in existing_drops
            ]

        # ── Step 2: Exact + Fuzzy Deduplication ──────────────────────────
        init_len = len(self.df)
        self.df  = self.df.drop_duplicates()
        dupes    = init_len - len(self.df)
        self.optimization_report['metrics']['duplicates_dropped'] = dupes
        if dupes > 0:
            self.optimization_report['steps_applied'].append(
                f"Removed {dupes} exact duplicate rows."
            )

        # Fuzzy dedup
        fuzzy_dupes_dropped = 0
        audit_res  = self._audit_health(self.df)
        fuzzy_key  = audit_res.get('fuzzy_key_col')
        if fuzzy_key and not self.df.empty:
            dropped_idx    = []
            seen_canonical = []
            for idx, val in zip(self.df.head(1000).index, self.df.head(1000)[fuzzy_key]):
                if pd.isna(val):
                    continue
                val_str = str(val).strip()
                if len(val_str) < 3:
                    continue
                is_dupe = any(
                    fuzz.token_sort_ratio(val_str, c) >= 85
                    for c in seen_canonical
                )
                if is_dupe:
                    dropped_idx.append(idx)
                else:
                    seen_canonical.append(val_str)
            if dropped_idx:
                self.df = self.df.drop(index=dropped_idx)
                fuzzy_dupes_dropped = len(dropped_idx)
                self.optimization_report['steps_applied'].append(
                    f"Removed {fuzzy_dupes_dropped} fuzzy duplicate row(s) in '{fuzzy_key}'."
                )
        self.optimization_report['metrics']['fuzzy_duplicates_dropped'] = fuzzy_dupes_dropped
        logger.info(f"Rows after dedupe: {len(self.df)}")

        # ── Step 3: Strict Date Standardization ──────────────────────────
        total_dates_detected  = 0
        total_dates_converted = 0
        total_dates_rejected  = 0

        for col in self.df.columns:
            sem = pre_semantics.get(col, self.column_diagnostics.get(col, {}).get('semantic', 'text'))
            is_str = (
                pd.api.types.is_object_dtype(self.df[col]) or
                pd.api.types.is_string_dtype(self.df[col])
            )
            if sem == 'date' and is_str:
                try:
                    mode = date_modes.get(col, 'nullify')
                    standardized, detected, converted, rejected = _standardize_dates_v2(self.df[col], mode=mode)
                    self.df[col] = standardized
                    total_dates_detected  += detected
                    total_dates_converted += converted
                    total_dates_rejected  += rejected
                    if detected > 0:
                        self.optimization_report['steps_applied'].append(
                            f"Date '{col}': {converted}/{detected} standardized → YYYY-MM-DD. "
                            f"{rejected} invalid → {'NaN' if mode == 'nullify' else 'kept'}."
                        )
                    logger.info(f"Date '{col}': detected={detected} converted={converted} rejected={rejected}")
                except Exception as exc:
                    logger.warning(f"Date standardization failed for '{col}': {exc}")

        self.optimization_report['metrics']['dates_detected']     = total_dates_detected
        self.optimization_report['metrics']['dates_standardized'] = total_dates_converted
        self.optimization_report['metrics']['dates_rejected']     = total_dates_rejected
        logger.info(f"Rows after date cleaning: {len(self.df)}")

        # ── Step 4: Email Cleaning ────────────────────────────────────────
        emails_found     = 0
        emails_valid     = 0
        emails_invalid   = 0
        emails_corrected = 0
        emails_removed   = 0

        rows_to_remove: List[Any] = []
        for col in self.df.columns:
            sem  = pre_semantics.get(col, 'text')
            mode = email_modes.get(col, 'nullify')  # default: nullify (NULL_INVALID)
            if sem != 'email':
                continue

            new_col = []
            for idx, val in self.df[col].items():
                if pd.isna(val) or str(val).strip() == '':
                    new_col.append(val)
                    continue

                emails_found += 1
                cleaned, status = _clean_and_validate_email(val)
                if status == 'valid':
                    emails_valid += 1
                    new_col.append(cleaned)
                elif status == 'corrected':
                    emails_corrected += 1
                    new_col.append(cleaned)
                else:  # status == 'invalid'
                    emails_invalid += 1
                    if mode == 'remove':
                        rows_to_remove.append(idx)
                        emails_removed += 1
                        new_col.append(val)
                    elif mode == 'flag':
                        new_col.append(val)
                    else:  # mode == 'nullify' (default)
                        new_col.append(np.nan)

            self.df[col] = new_col

            # Sync with old report keys to prevent breaking templates/PDF
            emails_flagged_count = emails_invalid if mode == 'flag' else 0
            emails_nullified_count = emails_invalid if mode == 'nullify' else 0
            self.optimization_report['metrics']['emails_flagged'] += emails_flagged_count
            self.optimization_report['metrics']['emails_nullified'] += emails_nullified_count
            self.optimization_report['metrics']['emails_rows_removed'] += emails_removed

            if emails_invalid > 0:
                self.optimization_report['steps_applied'].append(
                    f"Processed email column '{col}': {emails_valid} valid, {emails_corrected} corrected, {emails_invalid} invalid."
                )

        if rows_to_remove:
            self.df = self.df.drop(index=list(set(rows_to_remove)))
            self.df = self.df.reset_index(drop=True)

        self.optimization_report['metrics']['emails_found']     = emails_found
        self.optimization_report['metrics']['emails_valid']     = emails_valid
        self.optimization_report['metrics']['emails_invalid']   = emails_invalid
        self.optimization_report['metrics']['emails_corrected'] = emails_corrected
        self.optimization_report['metrics']['emails_removed']   = emails_removed
        logger.info(f"Rows after email cleaning: {len(self.df)}")

        # ── Step 5: Phone Standardization ────────────────────────────────
        phones_found    = 0
        phones_valid    = 0
        phones_invalid  = 0
        phones_corrected = 0

        for col in self.df.columns:
            sem = pre_semantics.get(col, 'text')
            if sem != 'phone':
                continue

            new_col = []
            for val in self.df[col]:
                if pd.isna(val) or str(val).strip() == '':
                    new_col.append(val)
                    continue

                phones_found += 1
                cleaned, status = _clean_and_validate_phone(val)
                if status == 'valid':
                    phones_valid += 1
                    new_col.append(cleaned)
                elif status == 'corrected':
                    phones_corrected += 1
                    new_col.append(cleaned)
                else:  # status == 'invalid'
                    phones_invalid += 1
                    new_col.append(np.nan)  # invalid numbers -> NULL

            self.df[col] = new_col
            
            # Sync with old report keys
            self.optimization_report['metrics']['phones_corrected'] += phones_corrected
            self.optimization_report['metrics']['phones_flagged_invalid'] += phones_invalid

            if phones_corrected > 0:
                self.optimization_report['steps_applied'].append(
                    f"Normalized {phones_corrected} phone number(s) in '{col}' using phonenumbers."
                )
            if phones_invalid > 0:
                self.optimization_report['steps_applied'].append(
                    f"Nullified {phones_invalid} invalid phone number(s) in '{col}'."
                )

        self.optimization_report['metrics']['phones_found']     = phones_found
        self.optimization_report['metrics']['phones_valid']     = phones_valid
        self.optimization_report['metrics']['phones_invalid']   = phones_invalid
        self.optimization_report['metrics']['phones_corrected'] = phones_corrected

        # ── Step 6: City Normalization (threshold=90) ─────────────────────
        cities_normalized = 0
        for col in self.df.columns:
            sem = pre_semantics.get(col, 'text')
            if sem != 'city':
                continue
            new_col = []
            for val in self.df[col]:
                if pd.isna(val):
                    new_col.append(val)
                    continue
                normalized, changed = _normalize_city(str(val), threshold=80)
                if changed:
                    cities_normalized += 1
                new_col.append(normalized)
            self.df[col] = new_col

        self.optimization_report['metrics']['cities_normalized'] = cities_normalized
        if cities_normalized > 0:
            self.optimization_report['steps_applied'].append(
                f"Normalized {cities_normalized} city name(s) to canonical form (threshold=90)."
            )

        # ── Step 6b: Age Business-Rule Validation (0 ≤ age ≤ 120) ────────
        ages_nullified_total = 0
        for col in self.df.columns:
            col_lower = col.lower().replace(' ', '_').replace('-', '_')
            sem       = pre_semantics.get(col, 'text')
            is_age_col = col_lower in _AGE_HINTS or sem == 'age'
            is_numeric = pd.api.types.is_numeric_dtype(self.df[col].dtype)

            if not is_age_col:
                continue

            if not is_numeric:
                # Attempt coercion first
                coerced = pd.to_numeric(self.df[col], errors='coerce')
                if coerced.notna().sum() / max(len(coerced), 1) >= 0.5:
                    self.df[col] = coerced
                    is_numeric = True

            if is_numeric:
                cleaned_age, n_nullified = _clean_age_series(self.df[col])
                if n_nullified > 0:
                    self.df[col] = cleaned_age
                    ages_nullified_total += n_nullified
                    self.optimization_report['steps_applied'].append(
                        f"Nullified {n_nullified} out-of-range age(s) in '{col}' "
                        f"(valid range: 0–120)."
                    )

        self.optimization_report['metrics']['ages_nullified'] = ages_nullified_total

        # ── Step 7: Null Filling ──────────────────────────────────────────
        # Re-run diagnostics so imputation strategies reflect cleaned data
        self.column_diagnostics = self._run_column_diagnostics(self.df)
        nulls_filled_total = 0
        for col in self.df.columns:
            col_nulls = int(self.df[col].isnull().sum())
            if col_nulls == 0:
                continue
            strategy = overrides.get(col, 'Auto')
            diag     = self.column_diagnostics.get(col, {})
            if strategy == 'Auto':
                strategy = diag.get('recommended', 'Mode')

            if strategy == 'Skip':
                continue

            elif strategy.startswith('Custom:'):
                raw_val  = strategy.split(':', 1)[1].strip()
                fill_val: Any = raw_val
                if pd.api.types.is_numeric_dtype(self.df[col].dtype):
                    try:
                        fill_val = float(raw_val)
                    except ValueError:
                        pass
                self.df[col] = self.df[col].fillna(fill_val)
                nulls_filled_total += col_nulls
                self.optimization_report['steps_applied'].append(
                    f"Filled '{col}' with custom value '{raw_val}'."
                )

            elif strategy == 'Median':
                val = diag.get('median') if diag.get('median') is not None else self.df[col].median()
                self.df[col] = self.df[col].fillna(val)
                nulls_filled_total += col_nulls
                self.optimization_report['steps_applied'].append(
                    f"Auto-healed '{col}' using Median imputation."
                )

            elif strategy == 'Mean':
                val = diag.get('mean') if diag.get('mean') is not None else self.df[col].mean()
                self.df[col] = self.df[col].fillna(val)
                nulls_filled_total += col_nulls
                self.optimization_report['steps_applied'].append(
                    f"Auto-healed '{col}' using Mean imputation."
                )

            elif strategy == 'Mode':
                val = diag.get('mode')
                if val is None:
                    m   = self.df[col].mode()
                    val = m.iloc[0] if not m.empty else None
                if val is not None:
                    self.df[col] = self.df[col].fillna(val)
                    nulls_filled_total += col_nulls
                    self.optimization_report['steps_applied'].append(
                        f"Auto-healed '{col}' using Mode imputation."
                    )

        self.optimization_report['metrics']['nulls_filled'] = nulls_filled_total

        # ── Step 8: Text Standardisation ─────────────────────────────────
        text_cols = [
            c for c in self.df.columns
            if (pd.api.types.is_object_dtype(self.df[c]) or
                pd.api.types.is_string_dtype(self.df[c]))
            and not pd.api.types.is_bool_dtype(self.df[c])
        ]
        for col in text_cols:
            if self._is_structured_text_column(col):
                self.df[col] = self.df[col].astype(str).str.strip()
            else:
                self.df[col] = self.df[col].astype(str).str.strip().str.title()

        # ── Step 9: Smart Outlier Management ─────────────────────────────
        num_cols         = self.df.select_dtypes(include=[np.number]).columns
        outliers_clipped = 0
        outliers_replaced= 0
        outlier_rows_rem = 0
        rows_to_drop: List[Any] = []

        for col in num_cols:
            clean = self.df[col].dropna()
            if clean.empty or len(clean) < 4:
                continue
            q1, q3 = clean.quantile([0.25, 0.75])
            iqr    = q3 - q1
            if iqr == 0:
                continue
            lower_bound = q1 - 1.5 * iqr
            upper_bound = q3 + 1.5 * iqr
            outlier_mask = (self.df[col] < lower_bound) | (self.df[col] > upper_bound)
            n_outliers   = int(outlier_mask.sum())
            if n_outliers == 0:
                continue

            # Determine outlier strategy for this column
            col_strategy = overrides.get(f'outlier_{col}', 'clip')

            if col_strategy == 'median_replace':
                med = float(clean.median())
                self.df.loc[outlier_mask, col] = med
                outliers_replaced += n_outliers
                self.optimization_report['steps_applied'].append(
                    f"Replaced {n_outliers} outlier(s) in '{col}' with median ({med:.2f})."
                )
            elif col_strategy == 'remove_row':
                rows_to_drop.extend(self.df[outlier_mask].index.tolist())
                outlier_rows_rem += n_outliers
                self.optimization_report['steps_applied'].append(
                    f"Flagged {n_outliers} outlier row(s) in '{col}' for removal."
                )
            else:  # clip (default)
                self.df[col] = self.df[col].clip(lower_bound, upper_bound)
                outliers_clipped += n_outliers
                self.optimization_report['steps_applied'].append(
                    f"Clipped {n_outliers} outlier(s) in '{col}' to IQR bounds "
                    f"[{lower_bound:.2f}, {upper_bound:.2f}]."
                )

        if rows_to_drop:
            self.df = self.df.drop(index=list(set(rows_to_drop))).reset_index(drop=True)

        self.optimization_report['metrics']['outliers_clipped']     = outliers_clipped
        self.optimization_report['metrics']['outliers_replaced']    = outliers_replaced
        self.optimization_report['metrics']['outlier_rows_removed'] = outlier_rows_rem
        self.optimization_report['metrics']['rows_after']           = len(self.df)

        # ── Step 9.5: Final Deduplication ────────────────────────────
        final_init_len = len(self.df)
        self.df = self.df.drop_duplicates().reset_index(drop=True)
        final_dupes = final_init_len - len(self.df)
        if final_dupes > 0:
            self.optimization_report['metrics']['duplicates_dropped'] += final_dupes
            self.optimization_report['steps_applied'].append(
                f"Removed {final_dupes} exact duplicate rows created during normalization/imputation."
            )

        logger.info(f"Final exported rows: {len(self.df)}")

        # ── Step 10: Post-Clean Verification (audit FINAL dataframe) ─────
        # Re-run diagnostics on the final, fully-cleaned dataframe
        self.column_diagnostics = self._run_column_diagnostics(self.df)
        final_audit = self._audit_health(self.df)
        final_audit['dates_rejected'] = total_dates_rejected

        # Verification: every counter comes from the actual exported data
        verification = _post_clean_verify(self.df, self.column_diagnostics)

        # Count remaining non-null emails and phones on the final exported dataframe
        final_emails_remaining = 0
        final_phones_remaining = 0
        for col in self.df.columns:
            col_sem = pre_semantics.get(col, 'text')
            if col_sem == 'email':
                final_emails_remaining += int(self.df[col].notna().sum())
            elif col_sem == 'phone':
                final_phones_remaining += int(self.df[col].notna().sum())

        self.optimization_report['metrics']['emails_remaining'] = final_emails_remaining
        self.optimization_report['metrics']['phones_remaining'] = final_phones_remaining

        # Reconcile report metrics with post-clean reality
        # (override pre-clean counts with verified post-clean counts)
        self.optimization_report['metrics']['dates_rejected'] = (
            verification['invalid_dates_remaining'] + total_dates_rejected
        )

        # Quality Score 2.0: before & after
        q_after = _calculate_quality_score_v2(
            final_audit, len(self.df), len(self.df.columns)
        )
        improvement_pct = round(
            ((q_after - self.quality_score) / max(self.quality_score, 1)) * 100, 1
        )

        self.optimization_report['verification']   = verification
        self.optimization_report['quality_before'] = self.quality_score
        self.optimization_report['quality_after']  = q_after
        self.optimization_report['quality_improvement_pct'] = improvement_pct

        return self.df, self.optimization_report, self._calculate_health_score(final_audit)


# ─── Public API ───────────────────────────────────────────────────────────────
def run_diagnostic(filepath: str) -> Tuple[Dict[str, Any], pd.DataFrame]:
    """Run diagnostic analysis on a CSV or XLSX file."""
    df     = _load_file(filepath)
    engine = DataIntelligenceEngine(df)
    logger.info(
        f"Diagnostic complete for '{os.path.basename(filepath)}': "
        f"Health={engine.health_score}% Quality={engine.quality_score}%"
    )
    diagnostic = {
        'filename':           os.path.basename(filepath),
        'raw_stats':          engine.raw_stats,
        'audit':              engine.audit_results,
        'health_score':       engine.health_score,
        'quality_score':      engine.quality_score,
        'column_diagnostics': engine.column_diagnostics,
    }
    return diagnostic, df


def run_optimization(filepath: str,
                     overrides: Dict[str, str] = None) -> Dict[str, Any]:
    """Run the full v10 optimization pipeline and save output as CSV."""
    df       = _load_file(filepath)
    engine   = DataIntelligenceEngine(df)
    h_before = engine.health_score

    opt_df, report, h_after = engine.optimize(overrides)

    base     = os.path.splitext(os.path.basename(filepath))[0]
    out_name = f"optimized_{base}.csv"
    out_path = os.path.join(os.path.dirname(filepath), out_name)
    opt_df.to_csv(out_path, index=False)
    logger.info(f"Optimization complete: Health {h_before}% → {h_after}%  ({out_name})")

    return {
        'output_file':        out_name,
        'report':             report,
        'health_before':      h_before,
        'health_after':       h_after,
        'column_diagnostics': engine.column_diagnostics,
    }
