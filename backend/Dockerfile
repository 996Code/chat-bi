FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]" 2>/dev/null || pip install --no-cache-dir fastapi uvicorn[standard] sqlalchemy[asyncio] aiomysql asyncpg psycopg2-binary alembic pydantic pydantic-settings python-jose[cryptography] passlib[bcrypt] bcrypt python-multipart redis cryptography sqlglot langgraph langchain langchain-openai pymilvus httpx openai pytest pytest-asyncio aiosqlite

# Copy source
COPY . .

EXPOSE 8999

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8999", "--reload"]