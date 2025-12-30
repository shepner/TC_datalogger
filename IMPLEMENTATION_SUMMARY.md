# Faction Management Automation - Implementation Summary

## Overview

This implementation extends the TC_datalogger system with comprehensive faction management automation features. All features are dashboard-based with copy/paste functionality, avoiding fragile automation.

## Components Implemented

### 1. TC_faction_chains Microservice ✅

**Location**: `TC_faction_chains/`

**Purpose**: Collects historical chain data from Torn City API

**Features**:
- Fetches chain IDs from `/faction/chains`
- For each chain ID, fetches detailed report from `/faction/{chainId}/chainreport`
- Stores in BigQuery table: `v2_faction_40832_chains-raw`
- Uses append mode with deduplication
- Runs every hour (configurable)

**Files Created**:
- `TC_faction_chains/src/main.py` - Main pipeline with two-step fetch logic
- `TC_faction_chains/config/TC_API_config.json` - Configuration
- `TC_faction_chains/requirements.txt` - Dependencies
- `TC_faction_chains/Dockerfile` - Docker configuration
- `TC_faction_chains/docker-compose.yml` - Service orchestration
- `TC_faction_chains/README.md` - Documentation

### 2. BigQuery Client for Dashboard ✅

**Location**: `TC_dashboard/src/bigquery_client.py`

**Purpose**: Enables dashboard to query BigQuery data

**Features**:
- Executes SQL queries from dashboard
- Handles authentication using microservice credentials
- Provides query execution and file-based query support
- Auto-discovers credentials from microservice configs

### 3. SQL Queries ✅

**Location**: `sql_queries/`

**Queries Created**:
1. `oc_participation_30d.sql` - 30-day OC participation counts
2. `oc_participation_7d.sql` - 7-day OC participation counts
3. `oc_rewards_with_items.sql` - OC rewards including item values
4. `trading_items_summary.sql` - Trading items with market prices
5. `chain_participation.sql` - Chain participation tracking
6. `faction_requirements.sql` - Complete requirements compliance check

### 4. OC Assignment Email Generator ✅

**Location**: 
- `TC_dashboard/src/oc_email_generator.py`
- `TC_dashboard/templates/oc_assignment.html`

**Purpose**: Generate email text for OC assignments

**Features**:
- Queries members not in OC
- Prioritizes members by 30-day OC count (lower = higher priority)
- Active members (within 24hrs) → OCs starting soonest
- Inactive members → OCs with longer delay
- Generates formatted email text with OC URLs and member assignments
- Copy/paste functionality

**Routes**:
- `GET /oc-assignment` - Dashboard page
- `POST /api/oc-assignment/generate` - Generate email API

### 5. Trading Items Dashboard ✅

**Location**:
- `TC_dashboard/src/trading_dashboard.py`
- `TC_dashboard/templates/trading.html`

**Purpose**: Display and manage pending trades

**Features**:
- Displays pending trades from "You were sent" events
- Shows item details, quantities, and market prices
- Copy button for monetary values
- Copy button for formatted chat messages
- Mark as paid functionality
- Filtering by date range and member

**Routes**:
- `GET /trading` - Dashboard page
- `GET /api/trading/pending` - Get pending trades
- `POST /api/trading/mark-paid` - Mark trade as paid
- `POST /api/trading/chat-message` - Get formatted chat message

### 6. Faction Requirements Report ✅

**Location**:
- `TC_dashboard/src/requirements_report.py`
- `TC_dashboard/templates/requirements.html`

**Purpose**: Track member compliance with faction requirements

**Features**:
- Tracks OC participation (3+ per month)
- Tracks trading items (120+ per week)
- Tracks chain participation (requirement)
- Calculates promotion/demotion/removal recommendations
- Considers offline status (2+ days = no promotion)
- Generates copy/paste action summary

**Routes**:
- `GET /requirements` - Dashboard page
- `GET /api/requirements/report` - Get requirements report

## Configuration

### Environment Variables

- `BIGQUERY_CREDENTIALS_PATH` - Path to GCP credentials (optional, auto-discovers)
- `BIGQUERY_PROJECT_ID` - GCP project ID (default: torncity-402423)
- `BIGQUERY_DATASET_ID` - BigQuery dataset (default: torn_data)

### Docker Compose

Updated root `docker-compose.yml` to include:
- `tc-faction-chains` service
- Updated dashboard volumes to include chains logs and credentials

## Usage

### Starting Services

```bash
cd TC_datalogger
docker-compose up -d
```

### Accessing Dashboard

1. Health Dashboard: http://localhost:8080
2. OC Assignment: http://localhost:8080/oc-assignment
3. Trading Dashboard: http://localhost:8080/trading
4. Requirements Report: http://localhost:8080/requirements

### Using Features

1. **OC Assignment Email**:
   - Navigate to `/oc-assignment`
   - Optionally customize instructions
   - Click "Generate Email"
   - Copy generated text and paste into Torn City email

2. **Trading Dashboard**:
   - Navigate to `/trading`
   - View pending trades
   - Use "Copy Value" for monetary amounts
   - Use "Copy Chat" for formatted messages
   - Mark trades as paid when processed

3. **Requirements Report**:
   - Navigate to `/requirements`
   - View member compliance status
   - Copy action summary for end-of-month processing

## Data Flow

```
Torn City API
    ↓
TC_faction_chains (microservice)
    ↓
BigQuery (v2_faction_40832_chains-raw)
    ↓
TC_dashboard (queries BigQuery)
    ↓
User (copy/paste from dashboard)
```

## Notes

- All features use copy/paste (no direct API integration for email/chat)
- Payment tracking is in-memory (could be moved to BigQuery table)
- SQL queries can be tested independently using `sql_queries/test_query.py`
- Dashboard auto-discovers BigQuery credentials from microservice configs

## Future Enhancements

- Persistent payment tracking in BigQuery
- Scheduled email generation
- More detailed OC analysis with item rewards
- Chain participation visualization
- Member activity trends

