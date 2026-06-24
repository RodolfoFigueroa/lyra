# API Documentation

## REST API

Interactive documentation is available when the server is running:

- **Swagger UI** (interactive): http://localhost:5219/docs
- **ReDoc** (clean HTML): http://localhost:5219/redoc

These are automatically generated from the FastAPI application and include:
- All REST endpoints (`/data_types`, `/metrics`, `/metrics/{metric_name}`, `/jobs`, `/jobs/{job_id}`, `/jobs/{job_id}/events`, `/jobs/{job_id}/result`, `/met_zone_code`)
- Request/response schemas
- Parameter descriptions
