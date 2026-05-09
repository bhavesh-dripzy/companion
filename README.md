# Companion Django Project

## Setup

1. Create and activate a virtual environment:
   - `python3 -m venv .venv`
   - `source .venv/bin/activate`
2. Install dependencies:
   - `pip install django`
3. Run migrations:
   - `python manage.py migrate`
4. Start development server:
   - `python manage.py runserver`

## Project layout

- `config/` contains Django project settings and URL configuration.
- `app/` is organized with:
  - `models/`
  - `services/`
  - `views/`
  - `routes.py`

## API: Samosa options via Zomato MCP

- Endpoint: `POST /api/food/samosa-options/`
- Body:
  - `area` (string, optional if `address_id` provided)
  - `address_id` (string, optional if `area` provided)
  - `limit` (number, optional, default `5`)

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/food/samosa-options/ \
  -H "Content-Type: application/json" \
  -d '{"area":"sector 49, gurugram","limit":5}'
```
