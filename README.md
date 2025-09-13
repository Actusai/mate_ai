# Mate AI – Backend (FastAPI)

FastAPI backend for the Mate AI platform. It provides authentication, company & user management, AI system registry with Authorized Representative (AR) features, calendar (incl. ICS), packages & subscriptions, metrics, and more.

## Tech stack
- Python 3.12, FastAPI, SQLAlchemy
- Alembic (DB migrations)
- SQLite (dev) / Postgres (prod-ready)
- APScheduler (optional background jobs)

## Requirements
- Python 3.12+
- A virtual environment
- SQLite (bundled) or Postgres URL

## Environment variables
Mate AI loads environment from **root `.env`** (preferred) and then from **`app/.env`** (fallback).  
Key variables: