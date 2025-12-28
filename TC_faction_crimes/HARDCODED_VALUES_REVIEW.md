# Hardcoded Values Review

This document identifies hardcoded values in the codebase that should be moved to configuration files for better maintainability and flexibility.

## Summary

Found **9 categories** of hardcoded values that should be configurable:

1. API Base URL
2. Allowed Pre-existing Tables
3. Time Window Buffer
4. Pagination Constants
5. BigQuery Dataset Location
6. Deduplication Key
7. Schema Path
8. Endpoint Names in Utility Scripts
9. Default Timezone (partially configurable)

---

## 1. API Base URL

**Location:** `src/api_client.py:65`
```python
self.base_url = "https://api.torn.com"
```

**Also used in:**
- `src/main.py:126, 132` - URL parsing
- `load_all_historical.py:35` - URL parsing
- `validate_counts.py:70` - URL parsing

**Recommendation:** Add to `config/TC_API_config.json`:
```json
{
  "api": {
    "base_url": "https://api.torn.com"
  }
}
```

**Impact:** Medium - Allows switching between API environments (dev/staging/prod) or API versions.

---

## 2. Allowed Pre-existing Tables

**Location:** `src/bigquery_loader.py:314`
```python
ALLOWED_PRE_EXISTING_TABLES = ["v2_faction_40832_crimes-new", "v2_faction_40832_crimes-raw"]
```

**Recommendation:** Add to `config/TC_API_config.json`:
```json
{
  "gcp": {
    "allowed_pre_existing_tables": [
      "v2_faction_40832_crimes-new",
      "v2_faction_40832_crimes-raw"
    ]
  }
}
```

**Impact:** High - This is a safety mechanism that should be configurable per environment/deployment.

---

## 3. Time Window Buffer

**Location:** `src/main.py:144`
```python
# Add 50% buffer to ensure we don't miss records
window_seconds = int(interval_seconds * 1.5)
```

**Recommendation:** Add to `config/TC_API_config.json`:
```json
{
  "defaults": {
    "time_window_buffer": 1.5
  }
}
```

**Impact:** Low - Allows tuning the buffer percentage for different use cases.

---

## 4. Pagination Constants

**Location:** `src/api_client.py`

Multiple hardcoded values:
- Line 215: `max_consecutive_empty = 3` - Stop after 3 empty pages
- Line 216: `max_consecutive_duplicates = 2` - Stop after 2 consecutive pages of all duplicates
- Line 349, 353, 359: `offset += 100` - Default page increment when next URL is missing
- Line 368: `offset > 1000000` - Safety limit to prevent infinite loops

**Recommendation:** Add to `config/TC_API_config.json`:
```json
{
  "defaults": {
    "pagination": {
      "max_consecutive_empty_pages": 3,
      "max_consecutive_duplicate_pages": 2,
      "default_page_increment": 100,
      "max_offset_limit": 1000000
    }
  }
}
```

**Impact:** Medium - Allows tuning pagination behavior for different API endpoints or scenarios.

---

## 5. BigQuery Dataset Location

**Location:** `src/bigquery_loader.py:323`
```python
dataset.location = "US"  # Default location
```

**Recommendation:** Add to `config/TC_API_config.json`:
```json
{
  "gcp": {
    "dataset_location": "US"
  }
}
```

**Impact:** Medium - Important for compliance and performance (data residency requirements).

---

## 6. Deduplication Key

**Location:** Multiple files
- `src/main.py:219` - `deduplication_key="id"`
- `load_all_historical.py:94` - `deduplication_key="id"`
- `src/bigquery_loader.py:423` - Default parameter `deduplication_key: str = "id"`

**Recommendation:** Add to `config/TC_API_config.json`:
```json
{
  "defaults": {
    "deduplication_key": "id"
  }
}
```

Or make it configurable per endpoint:
```json
{
  "endpoints": [
    {
      "deduplication_key": "id"
    }
  ]
}
```

**Impact:** Medium - Different endpoints might use different unique key fields.

---

## 7. Schema Path

**Location:** `src/main.py:77`
```python
schema_path = project_root / "config" / "oc_records_schema.json"
```

**Also in:** `load_all_historical.py:59`

**Recommendation:** Add to `config/TC_API_config.json`:
```json
{
  "schema": {
    "path": "config/oc_records_schema.json"
  }
}
```

**Impact:** Low - Allows using different schemas for different environments or endpoints.

---

## 8. Endpoint Names in Utility Scripts

**Location:** 
- `load_all_historical.py:28` - `"v2_faction_40832_crimes"`
- `validate_counts.py:63` - `"v2_faction_40832_crimes"`

**Recommendation:** Make these scripts accept endpoint name as a command-line argument or read from config.

**Impact:** Low - These are utility scripts, but hardcoding limits reusability.

---

## 9. Default Timezone

**Location:** `src/scheduler.py:51`
```python
timezone: str = "America/Chicago",
```

**Note:** This is already configurable via `config.get_timezone()` which defaults to `"America/Chicago"` in `src/config.py:195`, so this is acceptable as a fallback default.

**Impact:** None - Already properly configurable.

---

## Priority Recommendations

### High Priority
1. **Allowed Pre-existing Tables** - Safety mechanism that should be configurable
2. **API Base URL** - Important for environment management

### Medium Priority
3. **Pagination Constants** - Affects API fetching behavior
4. **BigQuery Dataset Location** - Important for compliance
5. **Deduplication Key** - May vary by endpoint

### Low Priority
6. **Time Window Buffer** - Rarely needs to change
7. **Schema Path** - Usually consistent across environments
8. **Endpoint Names in Utility Scripts** - Utility scripts, less critical

---

## Implementation Notes

When implementing these changes:

1. **Backward Compatibility:** Ensure defaults match current hardcoded values
2. **Environment Variables:** Consider allowing override via environment variables (like other config values)
3. **Validation:** Add validation for new config values (e.g., buffer must be > 1.0)
4. **Documentation:** Update README.md with new configuration options

