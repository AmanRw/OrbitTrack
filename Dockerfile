# Use a lightweight Python base image
FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory in container
WORKDIR /app

# Copy the project files
COPY . /app

# Install the package and all dependencies in editable mode
RUN pip install --no-cache-dir -e .

# Run the Discord bot when the container starts
CMD ["orbit-track", "--run-bot"]
